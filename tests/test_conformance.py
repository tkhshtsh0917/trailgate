"""Compare the same fictional telemetry across supported source paths."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trailgate.archive import ingest_snapshot, read_archive
from trailgate.bulk_archive import ingest_blob_export
from trailgate.bulk_report import public_bulk_summary
from trailgate.decision_audit import public_report
from trailgate.pull import PullSpec, pull_snapshot

PROJECT = "fictional-project"
TRACE = "fictional-trace"
START = "2026-10-01T00:00:00Z"
END = "2026-10-02T00:00:00Z"

OBSERVATIONS = [
    {
        "id": "gen-1",
        "traceId": TRACE,
        "projectId": PROJECT,
        "parentObservationId": None,
        "startTime": "2026-10-01T01:00:00Z",
        "endTime": "2026-10-01T01:00:01Z",
        "type": "GENERATION",
        "name": "fictional-decision",
        "sessionId": "fictional-session",
        "metadata": {"fixture": True},
        "input": {"question": "Where is the fictional document?"},
        "output": {"tool_call": "fictional-search"},
    },
    {
        "id": "tool-1",
        "traceId": TRACE,
        "projectId": PROJECT,
        "parentObservationId": "gen-1",
        "startTime": "2026-10-01T01:00:02Z",
        "endTime": "2026-10-01T01:00:03Z",
        "type": "TOOL",
        "name": "fictional-search",
        "sessionId": "fictional-session",
        "metadata": None,
        "input": {"query": "fictional document"},
        "output": {"document": "fictional-doc-1"},
    },
]
SCORES = [
    {
        "id": "score-1",
        "projectId": PROJECT,
        "name": "fictional-review",
        "value": True,
        "dataType": "BOOLEAN",
        "source": "API",
        "timestamp": "2026-10-01T01:00:04Z",
        "subject": {"kind": "trace", "id": TRACE},
    }
]


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _page(rows: list[dict]) -> bytes:
    return (json.dumps({"data": rows, "meta": {"cursor": None}}, sort_keys=True) + "\n").encode()


def _api_snapshot(path: Path) -> None:
    path.mkdir()
    (path / "observations.jsonl").write_bytes(_page(OBSERVATIONS))
    (path / "scores.jsonl").write_bytes(_page(SCORES))
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "trailgate.langfuse_api_snapshot.v1",
                "source": "langfuse",
                "observations_api": "v2",
                "scores_api": "v3",
                "observation_fields": ["core", "basic", "io", "metadata", "model"],
                "score_fields": ["details", "subject"],
                "query": {"fromStartTime": START, "toStartTime": END},
                "score_query": {"fromTimestamp": START, "toTimestamp": END},
                "complete": True,
            },
            sort_keys=True,
        )
        + "\n"
    )


def _blob_run(root: Path) -> Path:
    observations = [
        {
            "id": row["id"],
            "trace_id": row["traceId"],
            "project_id": row["projectId"],
            "parent_observation_id": row["parentObservationId"],
            "start_time": row["startTime"],
            "end_time": row["endTime"],
            "type": row["type"],
            "name": row["name"],
            "session_id": row["sessionId"],
            "metadata": row["metadata"],
            "input": row["input"],
            "output": row["output"],
        }
        for row in OBSERVATIONS
    ]
    scores = [
        {
            "id": "score-1",
            "project_id": PROJECT,
            "trace_id": TRACE,
            "name": "fictional-review",
            "data_type": "BOOLEAN",
            "value": 1,
            "source": "API",
            "timestamp": "2026-10-01T01:00:04Z",
        }
    ]
    files = []
    for table, rows in (("observations_v2", observations), ("scores", scores)):
        key = f"{PROJECT}/{table}/fictional.jsonl"
        path = root / key
        _jsonl(path, rows)
        files.append(
            {
                "key": key,
                "table": table,
                "fileType": "JSONL",
                "format": "jsonl-raw",
                "compressed": False,
                "contentType": "application/x-ndjson",
                "sizeBytes": path.stat().st_size,
                "rowCount": len(rows),
            }
        )
    manifest = root / PROJECT / "manifests" / "fictional.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "projectId": PROJECT,
                "exportSource": "EVENTS",
                "window": {"minTimestamp": START, "maxTimestamp": END},
                "maxTimestamp": END,
                "createdAt": "2026-10-02T00:10:00Z",
                "tables": ["observations_v2", "scores"],
                "files": files,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return manifest


def _semantic_rows(archive: dict, kind: str) -> list[dict]:
    excluded = {"present_fields", "source_ref"}
    return [
        {key: value for key, value in row.items() if key not in excluded} for row in archive[kind]
    ]


class SourceConformanceTests(unittest.TestCase):
    def test_offline_pull_and_blob_share_semantic_rows_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "saved-api"
            _api_snapshot(source)
            offline = root / "offline-archive"
            ingest_snapshot(source, offline)

            spec = PullSpec(
                base_url="http://127.0.0.1:3000",
                project_id=PROJECT,
                from_time=START,
                to_time=END,
            )
            pages = {"observations": _page(OBSERVATIONS), "scores": _page(SCORES)}
            pulled = root / "pulled"
            with patch(
                "trailgate.pull._request_page",
                side_effect=lambda spec, table, cursor, credentials: pages[table],
            ):
                pull_snapshot(spec, pulled, ("fictional-public", "fictional-secret"))
            pulled_archive = root / "pulled-archive"
            ingest_snapshot(pulled, pulled_archive)

            objects = root / "objects"
            manifest = _blob_run(objects)
            blob_archive = root / "blob-archive"
            ingest_blob_export(objects, manifest, blob_archive)

            archives = [read_archive(path) for path in (offline, pulled_archive, blob_archive)]
            for kind in ("observations", "scores"):
                expected = _semantic_rows(archives[0], kind)
                self.assertEqual(_semantic_rows(archives[1], kind), expected)
                self.assertEqual(_semantic_rows(archives[2], kind), expected)
            self.assertEqual(public_report(offline)["faithful_candidates"], 0)
            self.assertEqual(public_report(pulled_archive)["faithful_candidates"], 0)
            self.assertEqual(public_bulk_summary(blob_archive)["faithful_candidates"], 0)
            for archive in archives:
                for name, expected in archive["index"]["source_hashes"].items():
                    raw = archive["path"] / "raw" / name
                    self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
