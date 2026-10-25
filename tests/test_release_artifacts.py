"""Reject unexpected members in release archives."""

import stat
import unittest
import zipfile

from scripts.check_release_artifacts import _check_members, _wheel_member_kind


class ReleaseArtifactTests(unittest.TestCase):
    def test_sdist_members_require_exact_allowlist(self) -> None:
        expected = {"fictional-1.0/README.md"}
        entries = [
            ("fictional-1.0", "directory"),
            ("fictional-1.0/README.md", "file"),
        ]
        _check_members(entries, expected, "sdist")

        with self.assertRaisesRegex(ValueError, "allowlist"):
            _check_members([*entries, ("fictional-1.0/unreviewed.txt", "file")], expected, "sdist")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            _check_members([*entries, ("fictional-1.0/extra", "directory")], expected, "sdist")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _check_members([*entries, entries[-1]], expected, "sdist")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            _check_members([entries[0], (entries[1][0], "symlink")], expected, "sdist")
        with self.assertRaisesRegex(ValueError, "invalid file member names"):
            _check_members([entries[0], (entries[1][0] + "/", "file")], expected, "sdist")

    def test_wheel_members_reject_extra_metadata(self) -> None:
        expected = {"trailgate/a.py", "trailgate-1.0.0.dist-info/METADATA"}
        entries = [
            ("trailgate/", "directory"),
            ("trailgate/a.py", "file"),
            ("trailgate-1.0.0.dist-info/", "directory"),
            ("trailgate-1.0.0.dist-info/METADATA", "file"),
        ]
        _check_members(entries, expected, "wheel")

        with self.assertRaisesRegex(ValueError, "allowlist"):
            _check_members(
                [*entries, ("trailgate-1.0.0.dist-info/unreviewed.txt", "file")],
                expected,
                "wheel",
            )

    def test_wheel_member_kind_rejects_symlink(self) -> None:
        member = zipfile.ZipInfo("trailgate-1.0.0.dist-info/METADATA")
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assertEqual(_wheel_member_kind(member), "other")
