"""Validate published contracts against fictional records and built artifacts."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from trailgate.capture import CaptureWriter
from trailgate.dataset_export import export_dataset
from trailgate.episode_graph import build_episode_graph
from trailgate.schemas import SCHEMA_FILES, load_schema
from trailgate.snapshot import ingest_snapshot

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic"


class SchemaTests(unittest.TestCase):
    def test_every_bundled_schema_is_valid_and_names_are_confined(self) -> None:
        for name in SCHEMA_FILES:
            with self.subTest(name=name):
                Draft202012Validator.check_schema(load_schema(name))

        with self.assertRaises(ValueError):
            load_schema("../pyproject.toml")

    def test_fictional_fixture_and_generated_views_match_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = FIXTURE / "snapshot"
            archive = root / "archive"
            dataset = root / "dataset"
            capture = root / "capture.jsonl"

            snapshot_id = ingest_snapshot(source, archive)["snapshot_id"]
            declarations = []
            for name in ("rights", "policy"):
                data = json.loads((FIXTURE / f"{name}.json").read_text())
                data["snapshot_id"] = snapshot_id
                path = root / f"{name}.json"
                path.write_text(json.dumps(data))
                declarations.append(path)

            writer = CaptureWriter(capture)
            capture_id = writer.begin_decision(
                messages=[{"role": "user", "content": "Fictional"}], tools=[]
            )
            writer.finish_decision(
                capture_id, assistant_message={"role": "assistant", "content": "Done"}
            )
            writer.delivered_tool_result(
                capture_id, call_id="call", observation_id="tool", value="ok"
            )
            writer.abort_decision(capture_id, reason_code="fictional_abort")

            cases = [
                (
                    "api-snapshot-manifest-v1.json",
                    json.loads((source / "manifest.json").read_text()),
                ),
                ("archive-index-v1-v2.json", json.loads((archive / "index.json").read_text())),
                ("use-manifest-v1.json", json.loads(declarations[0].read_text())),
                ("dataset-policy-v1.json", json.loads(declarations[1].read_text())),
                ("episode-graph-v1.json", build_episode_graph(archive)),
            ]
            cases.extend(
                ("capture-event-v1.json", json.loads(line))
                for line in capture.read_text().splitlines()
            )

            export_dataset(archive, *declarations, "internal_training", dataset)
            cases.append(
                ("dataset-manifest-v1.json", json.loads((dataset / "manifest.json").read_text()))
            )
            cases.extend(
                ("chat-sft-v1.json", json.loads(line))
                for line in (dataset / "chat_sft.jsonl").read_text().splitlines()
            )
            cases.extend(
                ("episode-row-v1.json", json.loads(line))
                for line in (dataset / "episodes.jsonl").read_text().splitlines()
            )

            for name, value in cases:
                with self.subTest(name=name):
                    Draft202012Validator(load_schema(name)).validate(value)

    def test_schema_rejects_incorrect_contract_fields(self) -> None:
        cases = [
            ("use-manifest-v1.json", "permissions", {}),
            ("dataset-policy-v1.json", "use", "unspecified"),
            ("api-snapshot-manifest-v1.json", "complete", False),
        ]
        examples = {
            "use-manifest-v1.json": FIXTURE / "rights.json",
            "dataset-policy-v1.json": FIXTURE / "policy.json",
            "api-snapshot-manifest-v1.json": FIXTURE / "snapshot" / "manifest.json",
        }
        for name, key, bad_value in cases:
            with self.subTest(name=name):
                original = json.loads(examples[name].read_text())
                changed = copy.deepcopy(original)
                changed[key] = bad_value
                self.assertFalse(Draft202012Validator(load_schema(name)).is_valid(changed))


if __name__ == "__main__":
    unittest.main()
