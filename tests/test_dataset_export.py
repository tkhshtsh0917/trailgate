from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from trailgate.dataset_export import export_dataset
from trailgate.snapshot import SnapshotError, ingest_snapshot

SYNTHETIC = Path(__file__).resolve().parents[1] / "examples" / "synthetic"


class DatasetExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        shutil.copytree(SYNTHETIC / "snapshot", self.source)
        self.archive = self.root / "archive"
        self.rights = self.root / "rights.json"
        self.policy = self.root / "policy.json"

    def _prepare(self, *, mutate_capture=None) -> None:
        if mutate_capture is not None:
            path = self.source / "capture.jsonl"
            events = [json.loads(line) for line in path.read_text().splitlines()]
            mutate_capture(events)
            path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        snapshot_id = ingest_snapshot(self.source, self.archive)["snapshot_id"]
        for original, destination in (
            (SYNTHETIC / "rights.json", self.rights),
            (SYNTHETIC / "policy.json", self.policy),
        ):
            data = json.loads(original.read_text())
            data["snapshot_id"] = snapshot_id
            destination.write_text(json.dumps(data))

    def _rewrite_policy(self, change) -> None:
        policy = json.loads(self.policy.read_text())
        change(policy)
        self.policy.write_text(json.dumps(policy))

    def test_gated_dataset_is_deterministic_and_has_lineage(self) -> None:
        self._prepare()
        one, two = self.root / "dataset-one", self.root / "dataset-two"
        result = export_dataset(self.archive, self.rights, self.policy, "internal_training", one)
        export_dataset(self.archive, self.rights, self.policy, "internal_training", two)
        self.assertEqual(result["eligible_chat_sft"], 2)
        for name in ("chat_sft.jsonl", "episodes.jsonl", "manifest.json"):
            self.assertEqual((one / name).read_bytes(), (two / name).read_bytes())

        manifest = json.loads((one / "manifest.json").read_text())
        self.assertEqual(manifest["counts"]["episodes"], 1)
        self.assertEqual(manifest["views"]["episodes.jsonl"], "diagnostic_only")
        self.assertEqual(manifest["split_evidence"][0]["task_id"], "fictional-task")
        episode = json.loads((one / "episodes.jsonl").read_text())
        self.assertEqual(episode["eligibility"], "diagnostic_only")
        rows = [json.loads(line) for line in (one / "chat_sft.jsonl").read_text().splitlines()]
        self.assertEqual(
            {row["source"]["observation_id"] for row in rows}, {"gen-search", "gen-answer"}
        )
        search = next(row for row in rows if row["source"]["observation_id"] == "gen-search")
        self.assertNotIn("Synthetic evidence.", json.dumps(search["messages"]))

    def test_chat_request_parameters_are_retained_when_recorded(self) -> None:
        def add_parameters(events):
            begin = next(
                event
                for event in events
                if event["capture_id"] == "cap-search" and event["kind"] == "begin"
            )
            begin["request_parameters"] = {"tool_choice": "required"}

        self._prepare(mutate_capture=add_parameters)
        destination = self.root / "dataset"
        export_dataset(self.archive, self.rights, self.policy, "internal_training", destination)
        rows = [
            json.loads(line) for line in (destination / "chat_sft.jsonl").read_text().splitlines()
        ]
        search = next(row for row in rows if row["source"]["observation_id"] == "gen-search")
        self.assertEqual(search["request_parameters"], {"tool_choice": "required"})

    def test_score_does_not_replace_explicit_quality_selection(self) -> None:
        self._prepare()
        self._rewrite_policy(lambda data: data["quality"].update(approved_observation_ids=[]))
        destination = self.root / "dataset"
        result = export_dataset(
            self.archive, self.rights, self.policy, "internal_training", destination
        )
        self.assertEqual(result["eligible_chat_sft"], 0)
        self.assertEqual(result["reason_counts"]["gate:quality_manual_selection"], 2)
        self.assertEqual((destination / "chat_sft.jsonl").read_bytes(), b"")

    def test_session_score_stays_with_generic_episode(self) -> None:
        scores = self.source / "scores.jsonl"
        page = json.loads(scores.read_text())
        page["data"][0]["subject"] = {"kind": "session", "id": "session-example"}
        scores.write_text(json.dumps(page) + "\n")
        self._prepare()

        destination = self.root / "dataset"
        export_dataset(self.archive, self.rights, self.policy, "internal_training", destination)
        episode = json.loads((destination / "episodes.jsonl").read_text())
        self.assertEqual(episode["score_links"][0]["subject_kind"], "session")
        self.assertTrue(episode["score_links"][0]["resolved"])

    def test_other_project_session_score_is_not_attached(self) -> None:
        scores = self.source / "scores.jsonl"
        page = json.loads(scores.read_text())
        page["data"][0]["subject"] = {"kind": "session", "id": "session-example"}
        page["data"][0]["projectId"] = "another-fictional-project"
        scores.write_text(json.dumps(page) + "\n")
        self._prepare()

        destination = self.root / "dataset"
        export_dataset(self.archive, self.rights, self.policy, "internal_training", destination)
        episode = json.loads((destination / "episodes.jsonl").read_text())
        self.assertEqual(episode["score_links"], [])

    def test_missing_split_or_delivery_rejects_only_affected_decisions(self) -> None:
        def remove_delivery(events):
            events[:] = [event for event in events if event["kind"] != "delivery"]

        self._prepare(mutate_capture=remove_delivery)
        self._rewrite_policy(lambda data: data["splits"].clear())
        result = export_dataset(
            self.archive, self.rights, self.policy, "internal_training", self.root / "dataset"
        )
        self.assertEqual(result["eligible_chat_sft"], 0)
        self.assertEqual(result["reason_counts"]["gate:split_recorded"], 2)
        self.assertEqual(result["reason_counts"]["structural:unmatched_tool_result"], 1)

    def test_sensitive_data_or_split_leakage_blocks_export(self) -> None:
        self._prepare()
        self._rewrite_policy(lambda data: data["privacy"].update(sensitive_data="present"))
        destination = self.root / "dataset"
        with self.assertRaisesRegex(SnapshotError, "reviewed absent sensitive data"):
            export_dataset(self.archive, self.rights, self.policy, "internal_training", destination)
        self.assertFalse(destination.exists())

        self._rewrite_policy(lambda data: data["privacy"].update(sensitive_data="absent"))
        self._rewrite_policy(
            lambda data: data["splits"].append(
                {"trace_id": "other-trace", "task_id": "fictional-task", "split": "test"}
            )
        )
        with self.assertRaisesRegex(SnapshotError, "task appears in multiple"):
            export_dataset(self.archive, self.rights, self.policy, "internal_training", destination)
        self.assertFalse(destination.exists())

    def test_responses_payload_remains_in_generic_episode_only(self) -> None:
        def responses(events):
            for event in events:
                if event["capture_id"] != "cap-search":
                    continue
                if event["kind"] == "begin":
                    event["provider_format"] = "openai_responses"
                    event["request_parameters"] = {"model": "fictional", "store": False}
                    event["tools"] = [
                        {"type": "function", **tool["function"]} for tool in event["tools"]
                    ]
                elif event["kind"] == "finish":
                    event["provider_response"] = {"output": [{"type": "fictional_item"}]}

        self._prepare(mutate_capture=responses)
        destination = self.root / "dataset"
        result = export_dataset(
            self.archive, self.rights, self.policy, "internal_training", destination
        )
        self.assertEqual(result["eligible_chat_sft"], 1)
        episodes = [
            json.loads(line) for line in (destination / "episodes.jsonl").read_text().splitlines()
        ]
        decisions = episodes[0]["decisions"]
        response = next(item for item in decisions if item["observation_id"] == "gen-search")
        self.assertEqual(response["provider_response"]["output"][0]["type"], "fictional_item")
        self.assertFalse(response["eligibility"]["eligible_for_chat_sft"])


if __name__ == "__main__":
    unittest.main()
