from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from trailgate.capture import MAX_CAPTURE_EVENT_BYTES, CaptureWriter
from trailgate.capture_recovery import recover_capture
from trailgate.snapshot import SnapshotError


class CaptureRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_concurrent_writers_leave_complete_events(self) -> None:
        path = self.root / "capture.jsonl"

        def write_events(worker: int) -> None:
            writer = CaptureWriter(path)
            for number in range(20):
                capture_id = writer.begin_decision(
                    messages=[{"role": "user", "content": f"Fictional {worker}-{number}"}],
                    tools=[],
                )
                writer.finish_decision(
                    capture_id,
                    assistant_message={"role": "assistant", "content": "Done"},
                )

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write_events, range(4)))

        events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(events), 160)
        begins = {event["capture_id"] for event in events if event["kind"] == "begin"}
        finishes = {event["capture_id"] for event in events if event["kind"] == "finish"}
        self.assertEqual(len(begins), 80)
        self.assertEqual(begins, finishes)
        self.assertTrue(all(event["schema_version"] == "trailgate.capture.v1" for event in events))

    def test_recovery_copies_only_complete_events(self) -> None:
        source = self.root / "capture.jsonl"
        writer = CaptureWriter(source)
        writer.begin_decision(messages=[{"role": "user", "content": "Fictional"}], tools=[])
        original = source.read_bytes()
        with source.open("ab") as stream:
            stream.write(b'{"kind":"finish"')

        destination = self.root / "recovered.jsonl"
        result = recover_capture(source, destination)
        self.assertEqual(result["recovered_events"], 1)
        self.assertEqual(len(result["discarded_tail_sha256"]), 64)
        self.assertEqual(destination.read_bytes(), original)
        self.assertTrue(source.read_bytes().endswith(b'{"kind":"finish"'))
        with self.assertRaises(FileExistsError):
            recover_capture(source, destination)

    def test_recovery_rejects_corrupt_complete_line(self) -> None:
        source = self.root / "capture.jsonl"
        source.write_bytes(b'{"kind":"begin"}\nnot json\n')
        source.chmod(0o600)
        destination = self.root / "recovered.jsonl"
        with self.assertRaises(SnapshotError):
            recover_capture(source, destination)
        self.assertFalse(destination.exists())

    def test_event_size_limit_prevents_partial_write(self) -> None:
        source = self.root / "capture.jsonl"
        writer = CaptureWriter(source)
        with self.assertRaisesRegex(ValueError, "8 MiB"):
            writer.begin_decision(
                messages=[{"role": "user", "content": "x" * MAX_CAPTURE_EVENT_BYTES}],
                tools=[],
            )
        self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
