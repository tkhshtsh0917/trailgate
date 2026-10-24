from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trailgate import archive
from trailgate.audit import export_sft, public_report
from trailgate.capture import CaptureWriter
from trailgate.snapshot import SnapshotError, ingest_snapshot, read_snapshot

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "snapshot"
RIGHTS = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "rights.json"


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        shutil.copytree(FIXTURE, self.source)
        self.archive = self.root / "archive"

    def _rewrite_pages(self, filename: str, change) -> None:
        path = self.source / filename
        page = json.loads(path.read_text())
        change(page)
        path.write_text(json.dumps(page) + "\n")

    def _rewrite_capture(self, change) -> None:
        path = self.source / "capture.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        change(events)
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    def _rights_for(self, archive_path: Path) -> Path:
        rights = json.loads(RIGHTS.read_text())
        rights["snapshot_id"] = json.loads((archive_path / "index.json").read_text())["snapshot_id"]
        path = self.root / f"{archive_path.name}-rights.json"
        path.write_text(json.dumps(rights))
        return path

    def test_end_to_end_and_no_future_tool_result_in_earlier_prefix(self) -> None:
        index = ingest_snapshot(self.source, self.archive)
        self.assertEqual(index["observations"], 4)
        normalized = [
            json.loads(line)
            for line in (self.archive / "observations.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [row["id"] for row in normalized], ["root", "gen-search", "tool-search", "gen-answer"]
        )
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.archive / "raw" / "capture.jsonl").stat().st_mode), 0o600
        )
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 2)
        self.assertFalse(report["rights_checked"])
        output = self.root / "sft.jsonl"
        self.assertEqual(export_sft(self.archive, RIGHTS, "internal_training", output), 2)
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        search = next(row for row in rows if row["source"]["observation_id"] == "gen-search")
        answer = next(row for row in rows if row["source"]["observation_id"] == "gen-answer")
        self.assertNotIn("Synthetic evidence.", json.dumps(search["messages"]))
        self.assertIn("Synthetic evidence.", json.dumps(answer["messages"]))
        self.assertEqual(search["target"], "last_assistant_message")
        self.assertEqual(len(search["rights_ref"]["sha256"]), 64)
        self.assertEqual(search["rights_ref"]["snapshot_id"], index["snapshot_id"])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_malformed_chat_messages_do_not_become_candidates(self) -> None:
        bad_messages = [
            [],
            ["not a chat message"],
            [{"role": ["user"], "content": "Hello"}],
            [{"role": "user", "content": ["unsupported content part"]}],
            [{"role": "tool", "content": "Orphan result"}],
        ]
        for number, messages in enumerate(bad_messages):
            with self.subTest(messages=messages):

                def change(events, messages=messages):
                    begin = next(
                        event
                        for event in events
                        if event["capture_id"] == "cap-answer" and event["kind"] == "begin"
                    )
                    begin["messages"] = messages

                self._rewrite_capture(change)
                archive_path = self.root / f"bad-messages-{number}"
                ingest_snapshot(self.source, archive_path)
                report = public_report(archive_path)
                self.assertEqual(report["faithful_candidates"], 1)
                self.assertEqual(report["reason_counts"]["invalid_chat_messages"], 1)
                self.assertEqual(
                    export_sft(
                        archive_path,
                        self._rights_for(archive_path),
                        "internal_training",
                        self.root / f"bad-messages-{number}.jsonl",
                    ),
                    1,
                )

                shutil.copytree(FIXTURE, self.source, dirs_exist_ok=True)

    def test_malformed_chat_tool_schema_and_output_block_export(self) -> None:
        mutations = [
            (
                "bad-tool",
                "begin",
                "tools",
                "invalid_tool_schema",
                [{"type": "function", "function": {"name": "search", "parameters": "bad"}}],
            ),
            (
                "bad-call",
                "finish",
                "assistant_message",
                "invalid_chat_assistant_message",
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call-1", "type": "function", "function": {"name": "search"}}
                    ],
                },
            ),
            (
                "no-output",
                "finish",
                "assistant_message",
                "invalid_chat_assistant_message",
                {"role": "assistant"},
            ),
            (
                "duplicate-tool-name",
                "begin",
                "tools",
                "duplicate_tool_schema_name",
                [
                    {"type": "function", "function": {"name": "search"}},
                    {"type": "function", "function": {"name": "search"}},
                ],
            ),
        ]
        for name, kind, field, reason, value in mutations:
            with self.subTest(name=name):

                def change(events, kind=kind, field=field, value=value):
                    target = next(
                        event
                        for event in events
                        if event["capture_id"] == "cap-search" and event["kind"] == kind
                    )
                    target[field] = value

                self._rewrite_capture(change)
                archive_path = self.root / name
                ingest_snapshot(self.source, archive_path)
                report = public_report(archive_path)
                self.assertEqual(report["faithful_candidates"], 1)
                self.assertEqual(report["reason_counts"][reason], 1)
                self.assertEqual(
                    export_sft(
                        archive_path,
                        self._rights_for(archive_path),
                        "internal_training",
                        self.root / f"{name}.jsonl",
                    ),
                    1,
                )

                shutil.copytree(FIXTURE, self.source, dirs_exist_ok=True)

    def test_missing_capture_link_is_not_inferred_from_langfuse_input(self) -> None:
        def remove_link(page):
            next(row for row in page["data"] if row["id"] == "gen-search")["metadata"] = {}

        self._rewrite_pages("observations.jsonl", remove_link)
        ingest_snapshot(self.source, self.archive)
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 1)
        self.assertEqual(report["reason_counts"]["missing_capture_link"], 1)

    def test_missing_tool_result_blocks_tool_call_example(self) -> None:
        capture = self.source / "capture.jsonl"
        capture.write_text(
            "\n".join(
                line for line in capture.read_text().splitlines() if '"kind":"delivery"' not in line
            )
            + "\n"
        )
        ingest_snapshot(self.source, self.archive)
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 1)
        self.assertEqual(report["reason_counts"]["unmatched_tool_result"], 1)

    def test_malformed_tool_delivery_is_rejected_without_crashing(self) -> None:
        def change(events):
            delivery = next(event for event in events if event["kind"] == "delivery")
            delivery["observation_id"] = ["tool-search"]

        self._rewrite_capture(change)
        ingest_snapshot(self.source, self.archive)
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 1)
        self.assertEqual(report["reason_counts"]["unverified_tool_delivery"], 1)

    def test_tool_call_not_in_declared_schema_is_rejected(self) -> None:
        capture = self.source / "capture.jsonl"
        events = [json.loads(line) for line in capture.read_text().splitlines()]
        begin = next(
            event
            for event in events
            if event["capture_id"] == "cap-search" and event["kind"] == "begin"
        )
        begin["tools"] = []
        capture.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        ingest_snapshot(self.source, self.archive)
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 1)
        self.assertEqual(report["reason_counts"]["undeclared_tool_call"], 1)

    def test_capture_order_violation_is_rejected(self) -> None:
        capture = self.source / "capture.jsonl"
        events = [json.loads(line) for line in capture.read_text().splitlines()]
        finish = next(
            event
            for event in events
            if event["capture_id"] == "cap-search" and event["kind"] == "finish"
        )
        events.remove(finish)
        events.insert(0, finish)
        capture.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        ingest_snapshot(self.source, self.archive)
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 1)
        self.assertEqual(report["reason_counts"]["invalid_capture_order"], 1)

    def test_incomplete_cursor_snapshot_is_rejected(self) -> None:
        self._rewrite_pages("observations.jsonl", lambda page: page["meta"].update(cursor="more"))
        with self.assertRaisesRegex(SnapshotError, "unsaved next page"):
            read_snapshot(self.source)
        self.assertFalse(self.archive.exists())

    def test_non_utf8_snapshot_is_reported_as_snapshot_error(self) -> None:
        (self.source / "observations.jsonl").write_bytes(b"\xff\n")
        with self.assertRaisesRegex(SnapshotError, "cannot read"):
            read_snapshot(self.source)

    def test_non_finite_json_number_is_rejected(self) -> None:
        self._rewrite_pages("scores.jsonl", lambda page: page["data"][0].update(value=1e300))
        path = self.source / "scores.jsonl"
        path.write_text(path.read_text().replace("1e+300", "1e999"))
        with self.assertRaisesRegex(SnapshotError, "non-finite JSON number"):
            read_snapshot(self.source)

    def test_source_change_during_import_is_rejected_transactionally(self) -> None:
        original = archive.read_snapshot

        def read_then_change(source):
            parsed = original(source)
            with (self.source / "observations.jsonl").open("a") as stream:
                stream.write(" ")
            return parsed

        with patch.object(archive, "read_snapshot", side_effect=read_then_change):
            with self.assertRaisesRegex(SnapshotError, "source changed during import"):
                ingest_snapshot(self.source, self.archive)
        self.assertFalse(self.archive.exists())

    def test_duplicate_observation_id_is_rejected(self) -> None:
        def duplicate(page):
            page["data"].append(dict(page["data"][0]))

        self._rewrite_pages("observations.jsonl", duplicate)
        with self.assertRaisesRegex(SnapshotError, "duplicate id"):
            read_snapshot(self.source)

    def test_score_value_type_is_checked(self) -> None:
        self._rewrite_pages("scores.jsonl", lambda page: page["data"][0].update(value="true"))
        with self.assertRaisesRegex(SnapshotError, "value does not match dataType"):
            read_snapshot(self.source)

    def test_row_outside_declared_query_range_is_rejected(self) -> None:
        self._rewrite_pages(
            "observations.jsonl",
            lambda page: page["data"][0].update(startTime="2026-10-03T00:00:00Z"),
        )
        with self.assertRaisesRegex(SnapshotError, "outside declared query range"):
            read_snapshot(self.source)

    def test_rights_gate_blocks_export(self) -> None:
        ingest_snapshot(self.source, self.archive)
        rights = self.root / "rights.json"
        data = json.loads(RIGHTS.read_text())
        data["permissions"]["model_output"] = []
        rights.write_text(json.dumps(data))
        output = self.root / "blocked.jsonl"
        with self.assertRaisesRegex(SnapshotError, "model_output"):
            export_sft(self.archive, rights, "internal_training", output)
        self.assertFalse(output.exists())

    def test_rights_manifest_is_bound_to_the_snapshot(self) -> None:
        def change(events):
            begin = next(
                event
                for event in events
                if event["capture_id"] == "cap-search" and event["kind"] == "begin"
            )
            begin["messages"][1]["content"] = "A different fictional task."

        self._rewrite_capture(change)
        ingest_snapshot(self.source, self.archive)
        output = self.root / "blocked.jsonl"
        with self.assertRaisesRegex(SnapshotError, "snapshot_id does not match archive"):
            export_sft(self.archive, RIGHTS, "internal_training", output)
        self.assertFalse(output.exists())

    def test_rights_manifest_without_snapshot_id_is_rejected(self) -> None:
        ingest_snapshot(self.source, self.archive)
        rights = self.root / "rights.json"
        data = json.loads(RIGHTS.read_text())
        del data["snapshot_id"]
        rights.write_text(json.dumps(data))
        output = self.root / "blocked.jsonl"
        with self.assertRaisesRegex(SnapshotError, "rights snapshot_id"):
            export_sft(self.archive, rights, "internal_training", output)
        self.assertFalse(output.exists())

    def test_archive_tampering_is_detected_before_report(self) -> None:
        ingest_snapshot(self.source, self.archive)
        raw = self.archive / "raw" / "observations.jsonl"
        raw.write_text(raw.read_text() + " ")
        with self.assertRaisesRegex(SnapshotError, "raw archive hash mismatch"):
            public_report(self.archive)

    def test_capture_writer_keeps_explicit_empty_toolset(self) -> None:
        path = self.root / "capture.jsonl"
        writer = CaptureWriter(path)
        capture_id = writer.begin_decision(
            messages=[{"role": "user", "content": "Hello"}], tools=[]
        )
        writer.finish_decision(
            capture_id,
            assistant_message={"role": "assistant", "content": "Hi"},
            provider_response={"id": "response-1", "usage": {"completion_tokens": 2}},
        )
        events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(events[0]["tools"], [])
        self.assertEqual(events[1]["provider_response"]["id"], "response-1")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_aborted_capture_is_rejected_without_fabricating_output(self) -> None:
        def change(events):
            events[:] = [
                event
                for event in events
                if not (event["capture_id"] == "cap-answer" and event["kind"] == "finish")
            ]
            events.append({"kind": "abort", "capture_id": "cap-answer", "reason_code": "timeout"})

        self._rewrite_capture(change)
        ingest_snapshot(self.source, self.archive)
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 1)
        self.assertEqual(report["reason_counts"]["aborted_decision"], 1)

    def test_responses_capture_is_audited_but_not_chat_sft_exported(self) -> None:
        capture = self.source / "capture.jsonl"
        events = [json.loads(line) for line in capture.read_text().splitlines()]
        for event in events:
            if event["capture_id"] != "cap-search":
                continue
            if event["kind"] == "begin":
                event["provider_format"] = "openai_responses"
                event["request_parameters"] = {"model": "example", "store": False}
                event["tools"] = [
                    {"type": "function", **tool["function"]} for tool in event["tools"]
                ]
            elif event["kind"] == "finish":
                event["provider_response"] = {"output": [{"type": "function_call"}]}
        capture.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        ingest_snapshot(self.source, self.archive)
        report = public_report(self.archive)
        self.assertEqual(report["faithful_candidates"], 2)
        self.assertEqual(
            [row["sft_projection_supported"] for row in report["decisions"]], [False, True]
        )
        self.assertEqual(
            export_sft(
                self.archive,
                self._rights_for(self.archive),
                "internal_training",
                self.root / "sft.jsonl",
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
