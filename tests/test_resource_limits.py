"""Keep in-memory views bounded while allowing streamed diagnostics."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trailgate import archive, snapshot_input
from trailgate.cli import _inspect_trace
from trailgate.snapshot_input import SnapshotError, read_snapshot

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "snapshot"


class ResourceLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "archive"
        archive.ingest_snapshot(FIXTURE, self.path)

    def test_large_archive_fails_before_in_memory_loading(self) -> None:
        with patch.object(archive, "MAX_IN_MEMORY_OBSERVATIONS", 1):
            with self.assertRaisesRegex(SnapshotError, "in-memory view limit"):
                archive.read_archive(self.path)
        with patch.object(archive, "MAX_IN_MEMORY_ARCHIVE_BYTES", 1):
            with self.assertRaisesRegex(SnapshotError, "in-memory view limit"):
                archive.read_archive(self.path)

    def test_inspect_streams_and_marks_truncation(self) -> None:
        result = _inspect_trace(self.path, "trace-example", 2)
        self.assertEqual(result["total_observations"], 4)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(result["omitted_observations"], 2)

    def test_malformed_archive_count_fails_with_typed_error(self) -> None:
        index_file = self.path / "index.json"
        index = json.loads(index_file.read_text())
        index["observations"] = "many"
        index_file.write_text(json.dumps(index))
        with self.assertRaisesRegex(SnapshotError, "invalid archive observations count"):
            archive.read_archive(self.path)

    def test_declared_count_must_match_verified_rows(self) -> None:
        index_file = self.path / "index.json"
        index = json.loads(index_file.read_text())
        index["observations"] -= 1
        index_file.write_text(json.dumps(index))

        with self.assertRaisesRegex(SnapshotError, "row count differs"):
            archive.read_archive(self.path)
        with self.assertRaisesRegex(SnapshotError, "observations.jsonl count differs"):
            list(archive.iter_archive_rows(self.path, "observations.jsonl"))
        with self.assertRaisesRegex(SnapshotError, "observations.jsonl count differs"):
            _inspect_trace(self.path, "trace-example", 2)

    def test_archive_source_schema_must_match_archive_version(self) -> None:
        index_file = self.path / "index.json"
        index = json.loads(index_file.read_text())
        index["source_schema"] = "unknown.source"
        index_file.write_text(json.dumps(index))

        with self.assertRaisesRegex(SnapshotError, "invalid API archive source schema"):
            archive.read_archive(self.path)

    def test_saved_api_snapshot_rejects_oversized_input_before_parse(self) -> None:
        with patch.object(snapshot_input, "MAX_API_SNAPSHOT_BYTES", 1):
            with self.assertRaisesRegex(SnapshotError, "in-memory input limit"):
                read_snapshot(FIXTURE)


if __name__ == "__main__":
    unittest.main()
