from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_dependency_safety import check_lockfile


class DependencySafetyTests(unittest.TestCase):
    def test_current_lockfile_has_no_vulnerable_brotli_decoder(self) -> None:
        lockfile = Path(__file__).resolve().parents[1] / "uv.lock"
        self.assertEqual(check_lockfile(lockfile), [])

    def test_vulnerable_and_fixed_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lockfile = Path(directory) / "uv.lock"
            lockfile.write_text(
                '[[package]]\nname = "Brotli"\nversion = "1.2.0"\n'
                '[[package]]\nname = "brotlicffi"\nversion = "1.2.0.0"\n'
            )
            self.assertEqual(len(check_lockfile(lockfile)), 2)

            lockfile.write_text(
                '[[package]]\nname = "Brotli"\nversion = "1.2.1"\n'
                '[[package]]\nname = "brotlicffi"\nversion = "1.2.1.0"\n'
            )
            self.assertEqual(check_lockfile(lockfile), [])

            lockfile.write_text('[[package]]\nname = "brotli"\nversion = "unknown"\n')
            with self.assertRaisesRegex(ValueError, "unrecognized Brotli version"):
                check_lockfile(lockfile)


if __name__ == "__main__":
    unittest.main()
