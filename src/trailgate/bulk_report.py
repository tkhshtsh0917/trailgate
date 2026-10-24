"""Produce a bounded structural summary of a bulk archive."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from .archive import BULK_ARCHIVE_SCHEMA, _validate_index, iter_archive_rows
from .snapshot_input import SnapshotError, _read_json


def _observation_counts(path: Path) -> tuple[int, int]:
    """Count distinct traces exactly with a private disk-backed index."""
    with tempfile.TemporaryDirectory(prefix="trailgate-trace-count-") as directory:
        database = Path(directory) / "traces.sqlite"
        try:
            with closing(sqlite3.connect(database)) as connection:
                os.chmod(database, 0o600)
                connection.execute("PRAGMA journal_mode=OFF")
                connection.execute("PRAGMA synchronous=OFF")
                connection.execute("PRAGMA cache_size=-2048")
                connection.execute(
                    "CREATE TABLE traces (project_id TEXT, trace_id TEXT, "
                    "PRIMARY KEY (project_id, trace_id)) WITHOUT ROWID"
                )

                generations = 0
                with connection:
                    for row in iter_archive_rows(path, "observations.jsonl"):
                        connection.execute(
                            "INSERT OR IGNORE INTO traces VALUES (?, ?)",
                            (row["project_id"], row["trace_id"]),
                        )
                        if row["type"] == "GENERATION":
                            generations += 1
                traces = connection.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        except sqlite3.Error as exc:
            raise SnapshotError("bulk trace counter failed") from exc
    return traces, generations


def public_bulk_summary(path: str | Path) -> dict[str, Any]:
    """Report counts without loading or printing private observation content."""
    path = Path(path)
    index = _read_json(path / "index.json")
    if index.get("schema_version") != BULK_ARCHIVE_SCHEMA:
        raise SnapshotError("bulk summary requires a v2 archive")
    _validate_index(path, index)

    traces, generations = _observation_counts(path)
    for _ in iter_archive_rows(path, "scores.jsonl"):
        pass

    return {
        "snapshot_id": index["snapshot_id"],
        "source_schema": index["source_schema"],
        "completeness": "manifest_all_files_verified",
        "traces": traces,
        "observations": index["observations"],
        "scores": index["scores"],
        "generations": generations,
        "faithful_candidates": 0,
        "rights_checked": False,
        "reason_counts": {"capture_not_imported": generations} if generations else {},
        "decisions": [],
        "omitted_decisions": generations,
    }
