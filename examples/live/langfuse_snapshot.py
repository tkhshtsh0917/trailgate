"""Capture complete saved Langfuse API pages for local live diagnostics."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trailgate.audit import public_report
from trailgate.private_io import _private_bytes
from trailgate.snapshot import SNAPSHOT_SCHEMA, ingest_snapshot, read_snapshot

LOCAL_LANGFUSE_URL = "http://127.0.0.1:3000"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode()


def _pull_pages(
    fetch: Any, *, trace_id: str, fields: str, start: datetime, end: datetime, score: bool
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor = None
    while True:
        kwargs: dict[str, Any] = {
            "trace_id": trace_id,
            "fields": fields,
            "limit": 100,
            "cursor": cursor,
        }
        if score:
            kwargs.update(from_timestamp=start, to_timestamp=end)
        else:
            kwargs.update(from_start_time=start, to_start_time=end)
        page = json.loads(fetch(**kwargs).json())
        pages.append(page)
        cursor = page["meta"].get("cursor")
        if cursor is None:
            return pages
        if len(pages) >= 20:
            raise RuntimeError("pagination limit reached; snapshot left incomplete")


def _collect_complete(
    fetch: Any,
    *,
    trace_id: str,
    fields: str,
    start: datetime,
    end: datetime,
    score: bool,
    expected_ids: set[str],
    attempts: int,
    retry_delay: float,
) -> list[dict[str, Any]]:
    for attempt in range(attempts):
        pages = _pull_pages(
            fetch, trace_id=trace_id, fields=fields, start=start, end=end, score=score
        )
        found_ids = {row["id"] for page in pages for row in page["data"]}
        if expected_ids <= found_ids:
            return pages
        if attempt < attempts - 1:
            time.sleep(retry_delay)

    kind = "score" if score else "observation"
    raise RuntimeError(f"Langfuse has not returned every expected {kind}; capture remains private")


def _save_snapshot(
    langfuse: Any,
    trace_id: str,
    expected_ids: set[str],
    started: datetime,
    ended: datetime,
    run_dir: Path,
    expected_score_ids: set[str] | None = None,
) -> Path:
    start, end = started - timedelta(seconds=5), ended + timedelta(seconds=5)
    observations = _collect_complete(
        langfuse.api.observations.get_many,
        trace_id=trace_id,
        fields="core,basic,io,metadata,model",
        start=start,
        end=end,
        score=False,
        expected_ids=expected_ids,
        attempts=5,
        retry_delay=1,
    )
    scores = _collect_complete(
        langfuse.api.scores_v3.get_many_v3,
        trace_id=trace_id,
        fields="details,subject",
        start=start,
        end=end,
        score=True,
        expected_ids=expected_score_ids or set(),
        attempts=10,
        retry_delay=0.5,
    )

    snapshot = run_dir / "snapshot"
    snapshot.mkdir(mode=0o700)
    manifest = {
        "schema_version": SNAPSHOT_SCHEMA,
        "source": "langfuse",
        "observations_api": "v2",
        "scores_api": "v3",
        "observation_fields": ["core", "basic", "io", "metadata", "model"],
        "score_fields": ["details", "subject"],
        "query": {"fromStartTime": start.isoformat(), "toStartTime": end.isoformat()},
        "score_query": {"fromTimestamp": start.isoformat(), "toTimestamp": end.isoformat()},
        "complete": True,
        "trace_id_filter": trace_id,
    }

    _private_bytes(snapshot / "manifest.json", _json_bytes(manifest))
    _private_bytes(
        snapshot / "observations.jsonl", b"".join(_json_bytes(page) for page in observations)
    )
    _private_bytes(snapshot / "scores.jsonl", b"".join(_json_bytes(page) for page in scores))
    _private_bytes(snapshot / "capture.jsonl", (run_dir / "capture.jsonl").read_bytes())

    read_snapshot(snapshot)
    return snapshot


def _reconcile_score(langfuse: Any, task_dir: Path) -> int:
    state = json.loads((task_dir / "run_state.json").read_text(encoding="utf-8"))
    refreshed = task_dir / "rescored"
    suffix = 2
    while refreshed.exists():
        refreshed = task_dir / f"rescored-{suffix}"
        suffix += 1

    refreshed.mkdir(mode=0o700)
    _private_bytes(refreshed / "capture.jsonl", (task_dir / "capture.jsonl").read_bytes())
    snapshot = _save_snapshot(
        langfuse,
        state["trace_id"],
        set(state["expected_observation_ids"]),
        datetime.fromisoformat(state["started"]),
        datetime.fromisoformat(state["ended"]),
        refreshed,
        {state["score_id"]},
    )
    archive = refreshed / "archive"
    ingest_snapshot(snapshot, archive)

    return public_report(archive)["scores"]
