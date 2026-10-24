"""Validate the read-only pull against one newly created fictional local trace.

This writes one synthetic observation to the configured local Langfuse project.
It makes no model call and prints only validation counts and hashes.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trailgate.archive import ingest_snapshot, read_archive
from trailgate.pull import PullSpec, _request_page, pull_snapshot
from trailgate.snapshot_input import SnapshotError, read_snapshot

LOCAL_URL = "http://127.0.0.1:3000"


def _utc() -> datetime:
    return datetime.now(UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    root = args.out.absolute()
    if root.exists() or not root.parent.is_dir():
        parser.error("--out must name a new directory under an existing parent")

    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public or not secret:
        parser.error("Langfuse environment keys are required")
    os.environ["LANGFUSE_BASE_URL"] = LOCAL_URL

    from langfuse import get_client

    client = get_client()
    if not client.auth_check():
        raise RuntimeError("local Langfuse authentication failed")

    started = _utc()
    with client.start_as_current_observation(
        as_type="span",
        name="trailgate-fictional-pull-check",
        input={"question": "Which fictional archive is this?"},
    ) as observation:
        trace_id = observation.trace_id
        observation_id = observation.id
        observation.update(output={"answer": "A fictional validation trace."})
    score_id = str(uuid.uuid4())
    client.create_score(
        score_id=score_id,
        trace_id=trace_id,
        name="fictional-validation",
        value=1.0,
        data_type="NUMERIC",
    )
    client.flush()
    ended = _utc()

    # Discover the project ID from this trace alone; never fetch other project rows.
    from_time = (started - timedelta(seconds=10)).isoformat()
    to_time = (ended + timedelta(seconds=10)).isoformat()
    discovery = PullSpec(
        base_url=LOCAL_URL,
        project_id="discovery-only",
        from_time=from_time,
        to_time=to_time,
        trace_id=trace_id,
    )
    credentials = (public, secret)
    for attempt in range(10):
        page = json.loads(_request_page(discovery, "observations", None, credentials))
        matching = [row for row in page["data"] if row.get("id") == observation_id]
        if matching:
            break
        if attempt == 9:
            raise SnapshotError("fictional observation did not appear in local Langfuse")
        time.sleep(0.5)
    project_id = matching[0]["projectId"]
    spec = PullSpec(
        base_url=LOCAL_URL,
        project_id=project_id,
        from_time=from_time,
        to_time=to_time,
        trace_id=trace_id,
        limit=1,
    )

    for attempt in range(30):
        score_page = json.loads(_request_page(spec, "scores", None, credentials))
        if any(row.get("id") == score_id for row in score_page["data"]):
            break
        if attempt == 29:
            raise SnapshotError("fictional score did not appear in local Langfuse")
        time.sleep(1)

    root.mkdir(mode=0o700)
    result = pull_snapshot(spec, root / "snapshot", credentials)
    snapshot = read_snapshot(root / "snapshot")
    observed_ids = {item["row"]["id"] for item in snapshot["observations"]}
    if observation_id not in observed_ids or any(
        item["row"]["traceId"] != trace_id for item in snapshot["observations"]
    ):
        raise SnapshotError("pull did not preserve the fictional trace scope")

    ingest_snapshot(root / "snapshot", root / "archive")
    imported = read_archive(root / "archive")
    imported_ids = {item["id"] for item in imported["observations"]}
    if observed_ids != imported_ids:
        raise SnapshotError("pulled and offline-imported observation IDs differ")
    if score_id not in {item["id"] for item in imported["scores"]}:
        raise SnapshotError("fictional score was lost on import")
    print(
        json.dumps(
            {
                "validated": True,
                "observations": len(observed_ids),
                "scores": result["scores"],
                "snapshot_id": result["snapshot_id"],
                "offline_import_agrees": True,
                "model_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
