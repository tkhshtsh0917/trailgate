"""Produce a bounded structural summary of a bulk archive."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .archive import BULK_ARCHIVE_SCHEMA, _validate_index, iter_archive_rows
from .snapshot_input import SnapshotError, _read_json


def public_bulk_summary(path: str | Path) -> dict[str, Any]:
    """Report counts without loading or printing private observation content."""
    path = Path(path)
    index = _read_json(path / "index.json")
    if index.get("schema_version") != BULK_ARCHIVE_SCHEMA:
        raise SnapshotError("bulk summary requires a v2 archive")
    _validate_index(path, index)

    traces = set()
    generations = 0
    for row in iter_archive_rows(path, "observations.jsonl"):
        traces.add((row["project_id"], row["trace_id"]))
        if row["type"] == "GENERATION":
            generations += 1
    for _ in iter_archive_rows(path, "scores.jsonl"):
        pass

    return {
        "snapshot_id": index["snapshot_id"],
        "source_schema": index["source_schema"],
        "completeness": "manifest_all_files_verified",
        "traces": len(traces),
        "observations": index["observations"],
        "scores": index["scores"],
        "generations": generations,
        "faithful_candidates": 0,
        "rights_checked": False,
        "reason_counts": {"capture_not_imported": generations} if generations else {},
        "decisions": [],
        "omitted_decisions": generations,
    }
