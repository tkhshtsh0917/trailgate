from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trailgate import bulk_archive as module
from trailgate.archive import read_archive
from trailgate.blob_manifest import read_blob_manifest
from trailgate.bulk_archive import ingest_blob_export
from trailgate.bulk_report import public_bulk_summary
from trailgate.snapshot_input import SnapshotError


class BulkArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = self.root / "objects"
        self.objects.mkdir()
        self.manifest_path = self.objects / "project-example" / "manifests" / "run.json"
        self.manifest_path.parent.mkdir(parents=True)
        self.obs = [
            {
                "id": "gen-1",
                "trace_id": "trace-1",
                "project_id": "project-example",
                "start_time": "2026-10-01 00:00:00.000000",
                "end_time": "2026-10-01 00:00:01.000000",
                "type": "GENERATION",
                "parent_observation_id": "",
                "session_id": "session-1",
                "metadata": {"fictional": True},
            },
            {
                "id": "tool-1",
                "trace_id": "trace-1",
                "project_id": "project-example",
                "start_time": "2026-10-01 00:00:01.000000",
                "end_time": "2026-10-01 00:00:02.000000",
                "type": "TOOL",
                "parent_observation_id": "gen-1",
                "session_id": "session-1",
            },
        ]
        self.scores = [
            {
                "id": "score-1",
                "project_id": "project-example",
                "trace_id": "trace-1",
                "name": "fictional-review",
                "data_type": "BOOLEAN",
                "value": 1,
                "source": "API",
                "timestamp": "2026-10-01 00:00:03.000000",
                "string_value": None,
            }
        ]
        self._write_manifest()

    def _write_manifest(self, *, gzip_scores: bool = False) -> None:
        entries = []
        for table, rows, compressed in (
            ("observations_v2", self.obs, False),
            ("scores", self.scores, gzip_scores),
        ):
            key = f"project-example/{table}/run.jsonl" + (".gz" if compressed else "")
            path = self.objects / key
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ("".join(json.dumps(row) + "\n" for row in rows)).encode()
            path.write_bytes(gzip.compress(data, mtime=0) if compressed else data)
            entries.append(
                {
                    "key": key,
                    "table": table,
                    "fileType": "JSONL",
                    "format": "jsonl-gzip" if compressed else "jsonl-raw",
                    "compressed": compressed,
                    "contentType": "application/x-ndjson",
                    "sizeBytes": path.stat().st_size,
                    "rowCount": len(rows),
                }
            )
        manifest = {
            "version": 1,
            "projectId": "project-example",
            "exportSource": "EVENTS",
            "window": {
                "minTimestamp": "2026-10-01T00:00:00Z",
                "maxTimestamp": "2026-10-02T00:00:00Z",
            },
            "maxTimestamp": "2026-10-02T00:00:00Z",
            "createdAt": "2026-10-02T00:20:00Z",
            "tables": ["observations_v2", "scores"],
            "files": entries,
        }
        self.manifest_path.write_text(json.dumps(manifest) + "\n")

    def test_complete_enriched_manifest_and_gzip_stream(self) -> None:
        self._write_manifest(gzip_scores=True)
        archive_path = self.root / "archive"
        index = ingest_blob_export(self.objects, self.manifest_path, archive_path)
        self.assertEqual(index["schema_version"], "trailgate.archive.v2")
        self.assertEqual(index["observations"], 2)
        self.assertEqual(index["scores"], 1)
        archive = read_archive(archive_path)
        self.assertEqual(archive["scores"][0]["value"], True)
        self.assertEqual(archive["scores"][0]["subject"], {"kind": "trace", "id": "trace-1"})
        report = public_bulk_summary(archive_path)
        self.assertEqual(report["generations"], 1)
        self.assertEqual(report["reason_counts"], {"capture_not_imported": 1})

        source_key = "project-example/observations_v2/run.jsonl"
        self.assertEqual(
            index["source_hashes"][source_key],
            hashlib.sha256((self.objects / source_key).read_bytes()).hexdigest(),
        )

    def test_bulk_summary_rejects_count_mismatch(self) -> None:
        archive_path = self.root / "archive"
        ingest_blob_export(self.objects, self.manifest_path, archive_path)
        index_file = archive_path / "index.json"
        index = json.loads(index_file.read_text())
        index["scores"] = 0
        index_file.write_text(json.dumps(index))

        with self.assertRaisesRegex(SnapshotError, "scores.jsonl count differs"):
            public_bulk_summary(archive_path)

    def test_bulk_summary_counts_distinct_traces(self) -> None:
        second = {**self.obs[0], "id": "gen-2", "trace_id": "trace-2"}
        self.obs.append(second)
        self._write_manifest()

        archive_path = self.root / "archive"
        ingest_blob_export(self.objects, self.manifest_path, archive_path)
        report = public_bulk_summary(archive_path)
        self.assertEqual(report["traces"], 2)
        self.assertEqual(report["generations"], 2)

    def test_optional_parquet_stream(self) -> None:
        try:
            import pyarrow as arrow
            import pyarrow.parquet as parquet
        except ImportError:
            self.skipTest("bulk-parquet extra is not installed")

        manifest = json.loads(self.manifest_path.read_text())
        for entry, rows in zip(manifest["files"], (self.obs, self.scores), strict=True):
            key = entry["key"].replace(".jsonl", ".parquet")
            file = self.objects / key
            parquet.write_table(arrow.Table.from_pylist(rows), file)
            entry.update(
                key=key,
                fileType="PARQUET",
                format="parquet",
                compressed=False,
                contentType="application/vnd.apache.parquet",
                sizeBytes=file.stat().st_size,
                rowCount=None,
            )
        self.manifest_path.write_text(json.dumps(manifest))
        index = ingest_blob_export(self.objects, self.manifest_path, self.root / "archive")
        self.assertEqual((index["observations"], index["scores"]), (2, 1))

    def test_invalid_blob_end_time_is_rejected(self) -> None:
        for index, end_time in enumerate(("", "not-a-time", "2026-09-30 00:00:00.000000")):
            with self.subTest(end_time=end_time):
                self.obs[0]["end_time"] = end_time
                self._write_manifest()
                with self.assertRaises(SnapshotError):
                    ingest_blob_export(
                        self.objects, self.manifest_path, self.root / f"archive-{index}"
                    )

    def test_missing_file_or_incomplete_manifest_fails_before_destination(self) -> None:
        manifest = json.loads(self.manifest_path.read_text())
        manifest["files"] = manifest["files"][:1]
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(SnapshotError, "omit an enriched table"):
            read_blob_manifest(self.objects, self.manifest_path)
        self.assertFalse((self.root / "archive").exists())

    def test_changed_source_is_rejected_on_resume(self) -> None:
        destination = self.root / "archive"
        original = module._process_file
        calls = 0

        def interrupt(*args):
            nonlocal calls
            calls += 1
            original(*args)
            if calls == 1:
                raise RuntimeError("fictional interruption")

        with patch.object(module, "_process_file", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                ingest_blob_export(self.objects, self.manifest_path, destination)
        self.assertFalse(destination.exists())
        source = self.objects / "project-example" / "observations_v2" / "run.jsonl"
        source.write_bytes(source.read_bytes() + b" ")
        with self.assertRaisesRegex(SnapshotError, "size mismatch"):
            ingest_blob_export(self.objects, self.manifest_path, destination, resume=True)

    def test_resume_matches_uninterrupted_hashes(self) -> None:
        destination = self.root / "archive"
        original = module._process_file
        calls = 0

        def interrupt(*args):
            nonlocal calls
            original(*args)
            calls += 1
            if calls == 1:
                raise RuntimeError("fictional interruption")

        with patch.object(module, "_process_file", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                ingest_blob_export(self.objects, self.manifest_path, destination)
        resumed = ingest_blob_export(self.objects, self.manifest_path, destination, resume=True)
        fresh = ingest_blob_export(self.objects, self.manifest_path, self.root / "fresh")
        self.assertEqual(resumed["snapshot_id"], fresh["snapshot_id"])
        self.assertEqual(resumed["derived_hashes"], fresh["derived_hashes"])

    def test_finalized_checkpoint_rechecks_same_size_source_change(self) -> None:
        destination = self.root / "archive"
        finished = self.root / "finished"
        ingest_blob_export(self.objects, self.manifest_path, finished)
        os.rename(finished, self.root / ".archive.work")

        source = self.objects / "project-example" / "observations_v2" / "run.jsonl"
        original = source.read_bytes()
        source.write_bytes(original.replace(b"gen-1", b"gen-2"))
        with self.assertRaisesRegex(SnapshotError, "changed after checkpoint"):
            ingest_blob_export(self.objects, self.manifest_path, destination, resume=True)

        source.write_bytes(original)
        resumed = ingest_blob_export(self.objects, self.manifest_path, destination, resume=True)
        self.assertEqual(resumed["observations"], 2)


if __name__ == "__main__":
    unittest.main()
