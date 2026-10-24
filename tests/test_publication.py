"""The release check must identify risky paths without inspecting their contents."""

import unittest
from pathlib import Path

from scripts.check_public_files import content_issues, private_path


class PublicationTests(unittest.TestCase):
    def test_private_path_names(self) -> None:
        self.assertTrue(private_path(Path("private-run/capture.jsonl")))
        self.assertTrue(private_path(Path("local-langfuse.env")))
        self.assertTrue(private_path(Path("data/raw.sqlite")))
        self.assertFalse(private_path(Path("examples/synthetic_snapshot/manifest.json")))

    def test_content_scan_reports_rules_without_payload(self) -> None:
        personal_path = "/" + "Users" + "/example/work/file.py"
        token = "sk-" + "example01234567890123456789"
        issues = content_issues(f"safe\n{personal_path}\n{token}\n")
        self.assertEqual(issues, [(2, "personal_absolute_path"), (3, "provider_token")])
