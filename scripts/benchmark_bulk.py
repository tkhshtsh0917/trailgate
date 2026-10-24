"""Measure synthetic enriched-blob intake and report memory."""

from __future__ import annotations

import argparse
import json
import platform
import resource
import subprocess
import tempfile
import time
from pathlib import Path

from trailgate.bulk_archive import ingest_blob_export
from trailgate.bulk_report import public_bulk_summary


def _revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--max-rss-mib", type=int)
    args = parser.parse_args()
    if not 1 <= args.rows <= 1_000_000:
        parser.error("rows must be 1..1000000")

    with tempfile.TemporaryDirectory(prefix="trailgate-bulk-bench-") as temp:
        root = Path(temp)
        objects = root / "objects" / "fictional-project"
        observation_file = objects / "observations_v2" / "run.jsonl"
        score_file = objects / "scores" / "run.jsonl"
        observation_file.parent.mkdir(parents=True)
        score_file.parent.mkdir(parents=True)
        with observation_file.open("w") as stream:
            for index in range(args.rows):
                row = {
                    "id": f"fictional-{index:06d}",
                    "trace_id": f"trace-{index // 10:06d}",
                    "project_id": "fictional-project",
                    "start_time": "2026-10-01 00:00:00.000000",
                    "end_time": "2026-10-01 00:00:01.000000",
                    "type": "GENERATION" if index % 10 == 0 else "SPAN",
                    "parent_observation_id": "",
                }
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        score_file.write_bytes(b"")
        manifest_path = objects / "manifests" / "run.json"
        manifest_path.parent.mkdir()
        files = []
        for table, path, count in (
            ("observations_v2", observation_file, args.rows),
            ("scores", score_file, 0),
        ):
            files.append(
                {
                    "key": f"fictional-project/{table}/run.jsonl",
                    "table": table,
                    "fileType": "JSONL",
                    "format": "jsonl-raw",
                    "compressed": False,
                    "contentType": "application/x-ndjson",
                    "sizeBytes": path.stat().st_size,
                    "rowCount": count,
                }
            )
        manifest_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "projectId": "fictional-project",
                    "exportSource": "EVENTS",
                    "window": {
                        "minTimestamp": "2026-10-01T00:00:00Z",
                        "maxTimestamp": "2026-10-02T00:00:00Z",
                    },
                    "maxTimestamp": "2026-10-02T00:00:00Z",
                    "createdAt": "2026-10-02T00:20:00Z",
                    "tables": ["observations_v2", "scores"],
                    "files": files,
                }
            )
            + "\n"
        )

        started = time.monotonic()
        index = ingest_blob_export(root / "objects", manifest_path, root / "archive")
        report = public_bulk_summary(root / "archive")
        elapsed = time.monotonic() - started
        raw_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_bytes = raw_rss if platform.system() == "Darwin" else raw_rss * 1024
        print(
            json.dumps(
                {
                    "rows": args.rows,
                    "observations": report["observations"],
                    "generations": report["generations"],
                    "peak_rss_bytes": rss_bytes,
                    "duration_seconds": round(elapsed, 3),
                    "snapshot_id": index["snapshot_id"],
                    "derived_hashes": index["derived_hashes"],
                    "revision": _revision(),
                    "platform": platform.platform(),
                },
                sort_keys=True,
            )
        )
        if args.max_rss_mib is not None and rss_bytes > args.max_rss_mib * 1024 * 1024:
            raise SystemExit("bulk benchmark exceeded --max-rss-mib")


if __name__ == "__main__":
    main()
