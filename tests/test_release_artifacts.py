"""Reject unexpected members in release archives."""

import base64
import csv
import hashlib
import io
import stat
import tomllib
import unittest
import zipfile

from scripts.check_release_artifacts import (
    ROOT,
    _check_entry_points,
    _check_members,
    _check_metadata,
    _check_record,
    _check_sdist_pyproject,
    _expected_metadata_headers,
    _wheel_member_kind,
)


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

    def test_entry_points_match_reviewed_project(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        expected = b"[console_scripts]\ntrailgate = trailgate.cli:main\n\n"
        _check_entry_points(expected, project)

        with self.assertRaisesRegex(ValueError, "entry points"):
            _check_entry_points(
                b"[console_scripts]\ntrailgate = trailgate.cli:_parser\n\n", project
            )

    def test_metadata_matches_all_project_fields_and_readme(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        headers = _expected_metadata_headers(project)
        lines = [
            f"{name}: {value}" for (name, value), count in headers.items() for _ in range(count)
        ]
        metadata = ("\n".join(lines) + "\n\n").encode() + (ROOT / "README.md").read_bytes()
        _check_metadata(metadata, project)

        with self.assertRaisesRegex(ValueError, "metadata"):
            _check_metadata(
                metadata.replace(b"\n\n", b"\nRequires-Dist: fictional-review\n\n", 1), project
            )
        with self.assertRaisesRegex(ValueError, "description"):
            _check_metadata(metadata + b"fictional extra text", project)

    def test_record_hashes_and_size_match_members(self) -> None:
        member = "fictional/module.py"
        record = "fictional-1.0.dist-info/RECORD"
        content = b"fictional source\n"
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        output = io.StringIO(newline="")
        csv.writer(output, lineterminator="\n").writerows(
            [(member, "sha256=" + digest, str(len(content))), (record, "", "")]
        )

        package_bytes = io.BytesIO()
        with zipfile.ZipFile(package_bytes, "w") as package:
            package.writestr(member, content)
            package.writestr(record, output.getvalue())
        with zipfile.ZipFile(package_bytes) as package:
            _check_record(package, record, {member, record})

        bad_bytes = io.BytesIO()
        with zipfile.ZipFile(bad_bytes, "w") as package:
            package.writestr(member, b"changed source\n")
            package.writestr(record, output.getvalue())
        with zipfile.ZipFile(bad_bytes) as package:
            with self.assertRaisesRegex(ValueError, "digest or size"):
                _check_record(package, record, {member, record})

    def test_sdist_active_pyproject_must_match_source(self) -> None:
        source = b'[project]\nname = "fictional"\n'
        _check_sdist_pyproject(b'[project]\nname="fictional"\n', source)

        with self.assertRaisesRegex(ValueError, "active pyproject"):
            _check_sdist_pyproject(b'[project]\nname = "changed"\n', source)
        with self.assertRaisesRegex(ValueError, "active pyproject"):
            _check_sdist_pyproject(source + b"# fictional private comment\n", source)
