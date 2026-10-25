"""Reject unexpected members in release archives."""

import base64
import csv
import hashlib
import io
import stat
import tarfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check_release_artifacts import (
    ROOT,
    _check_entry_points,
    _check_members,
    _check_metadata,
    _check_record,
    _check_sdist_container,
    _check_sdist_pyproject,
    _check_wheel_container,
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

    def test_wheel_container_rejects_unrecorded_comments(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as package:
            package.comment = b"fictional unrecorded comment"
            package.writestr("fictional.txt", b"public")
        with zipfile.ZipFile(archive_bytes) as package:
            with self.assertRaisesRegex(ValueError, "unreviewed ZIP metadata"):
                _check_wheel_container(package)

        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as package:
            member = zipfile.ZipInfo("fictional.txt")
            member.extra = b"\x01\x00\x00\x00"
            package.writestr(member, b"public")
        with zipfile.ZipFile(archive_bytes) as package:
            with self.assertRaisesRegex(ValueError, "unreviewed ZIP metadata"):
                _check_wheel_container(package)

    def test_sdist_container_rejects_unreviewed_metadata(self) -> None:
        member = tarfile.TarInfo("fictional.txt")
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "fictional.tar.gz"
            path.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 6)
            _check_sdist_container(path, [member], {})

            member.pax_headers = {"comment": "fictional unreviewed comment"}
            with self.assertRaisesRegex(ValueError, "unreviewed tar metadata"):
                _check_sdist_container(path, [member], {})

            member.pax_headers = {}
            with self.assertRaisesRegex(ValueError, "unreviewed tar metadata"):
                _check_sdist_container(path, [member], {"comment": "fictional comment"})

            member.linkname = "fictional unreviewed link name"
            with self.assertRaisesRegex(ValueError, "unreviewed tar metadata"):
                _check_sdist_container(path, [member], {})

            member.linkname = ""
            path.write_bytes(b"\x1f\x8b\x08\x10" + b"\x00" * 6)
            with self.assertRaisesRegex(ValueError, "unreviewed gzip metadata"):
                _check_sdist_container(path, [member], {})

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
