"""Keep source payload keys out of normal command diagnostics."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from trailgate.cli import main
from trailgate.decision_audit import public_report

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "snapshot"
MARKER = "fictional-private-key"


class CliPrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        shutil.copytree(FIXTURE, self.source)

    def _run(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_duplicate_nested_input_key_is_not_echoed(self) -> None:
        path = self.source / "observations.jsonl"
        page = json.loads(path.read_text())
        page["data"][0]["input"] = "__FICTIONAL_REPLACE__"
        text = json.dumps(page).replace(
            '"__FICTIONAL_REPLACE__"', '{"fictional-private-key":1,"fictional-private-key":2}'
        )
        path.write_text(text + "\n")

        status, stdout, stderr = self._run(
            "doctor", "--source", "langfuse", "--input", str(self.source)
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("duplicate JSON key", stderr)
        self.assertNotIn(MARKER, stderr)

    def test_unknown_field_names_remain_private_across_commands(self) -> None:
        path = self.source / "observations.jsonl"
        page = json.loads(path.read_text())
        page["data"][0][MARKER] = "fictional detail"
        path.write_text(json.dumps(page) + "\n")
        archive = self.root / "archive"

        commands = [
            ("doctor", "--source", "langfuse", "--input", str(self.source)),
            (
                "ingest",
                "langfuse-api-snapshot",
                str(self.source),
                "--out",
                str(archive),
            ),
            ("report", str(archive)),
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                status, stdout, stderr = self._run(*command)
                self.assertEqual(status, 0, stderr)
                self.assertNotIn(MARKER, stdout + stderr)
                result = json.loads(stdout)
                self.assertEqual(result["unknown_observation_field_count"], 1)
                self.assertNotIn("unknown_observation_fields", result)

        private_index = json.loads((archive / "index.json").read_text())
        self.assertIn(MARKER, private_index["unknown_observation_fields"])
        self.assertNotIn(MARKER, json.dumps(public_report(archive)))
