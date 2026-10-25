"""Check release archive members, metadata, and bundled source bytes."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import stat
import tarfile
import tomllib
import zipfile
from collections import Counter
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from trailgate import __version__
from trailgate.schemas import SCHEMA_FILES

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_WHEEL = (
    b"Wheel-Version: 1.0\nGenerator: uv 0.12.10\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
)


def _source_files() -> dict[str, bytes]:
    return {
        str(path.relative_to(ROOT / "src")): path.read_bytes()
        for path in (ROOT / "src" / "trailgate").rglob("*")
        if path.is_file() and path.suffix in {".py", ".json"}
    }


def _expected_directories(files: set[str]) -> set[str]:
    return {
        str(parent)
        for name in files
        for parent in PurePosixPath(name).parents
        if str(parent) != "."
    }


def _check_members(entries: list[tuple[str, str]], expected_files: set[str], artifact: str) -> None:
    """Reject duplicate, unexpected, or special archive members before reading them."""
    if any(kind == "file" and name.endswith("/") for name, kind in entries):
        raise ValueError(f"{artifact} has invalid file member names")

    names = [name.rstrip("/") for name, _ in entries]
    if len(names) != len(set(names)):
        raise ValueError(f"{artifact} has duplicate members")
    if any(kind not in {"file", "directory"} for _, kind in entries):
        raise ValueError(f"{artifact} has unsupported member types")

    files = {name.rstrip("/") for name, kind in entries if kind == "file"}
    directories = {name.rstrip("/") for name, kind in entries if kind == "directory"}
    if files != expected_files or directories != _expected_directories(expected_files):
        raise ValueError(f"{artifact} members differ from the reviewed allowlist")


def _wheel_member_kind(member: zipfile.ZipInfo) -> str:
    mode = stat.S_IFMT(member.external_attr >> 16)
    if member.is_dir():
        return "directory" if mode in {0, stat.S_IFDIR} else "other"
    return "file" if mode in {0, stat.S_IFREG} else "other"


def _check_wheel_container(package: zipfile.ZipFile) -> None:
    """Reject untracked ZIP comment and extra fields outside wheel RECORD."""
    if package.comment or any(item.comment or item.extra for item in package.infolist()):
        raise ValueError("wheel has unreviewed ZIP metadata")


def _check_sdist_container(
    path: Path, members: list[tarfile.TarInfo], global_pax: dict[str, str]
) -> None:
    """Reject gzip and tar metadata that can carry unreviewed text."""
    with path.open("rb") as stream:
        header = stream.read(10)
    if len(header) != 10 or header[:3] != b"\x1f\x8b\x08" or header[3] != 0:
        raise ValueError("sdist has unreviewed gzip metadata")
    if global_pax or any(
        item.pax_headers or item.uname or item.gname or item.linkname for item in members
    ):
        raise ValueError("sdist has unreviewed tar metadata")


def _expected_metadata_headers(project: dict) -> Counter[tuple[str, str]]:
    if project.get("readme") != "README.md" or len(project.get("authors", [])) != 1:
        raise ValueError("release metadata verifier needs one author and README.md")

    author = project["authors"][0]
    headers = [
        ("metadata-version", "2.4"),
        ("name", project["name"]),
        ("version", project["version"]),
        ("summary", project["description"]),
        ("keywords", ",".join(project["keywords"])),
        ("author", author["name"]),
        ("author-email", f"{author['name']} <{author['email']}>"),
        ("license-expression", project["license"]),
        (
            "requires-python",
            ", ".join(part.strip() for part in project["requires-python"].split(",")),
        ),
        ("description-content-type", "text/markdown"),
    ]
    headers.extend(("license-file", name) for name in project["license-files"])
    headers.extend(("requires-dist", name) for name in project.get("dependencies", []))
    for extra, dependencies in project.get("optional-dependencies", {}).items():
        headers.append(("provides-extra", extra))
        headers.extend(("requires-dist", f"{name} ; extra == '{extra}'") for name in dependencies)
    headers.extend(("project-url", f"{name}, {url}") for name, url in project["urls"].items())
    return Counter(headers)


def _check_metadata(data: bytes, project: dict) -> None:
    """Bind every wheel metadata field and its description to reviewed project files."""
    _, separator, description = data.partition(b"\n\n")
    if not separator or description != (ROOT / "README.md").read_bytes():
        raise ValueError("wheel metadata description differs from README.md")

    metadata = BytesParser().parsebytes(data)
    headers = Counter((name.lower(), value) for name, value in metadata.items())
    if headers != _expected_metadata_headers(project):
        raise ValueError("wheel metadata differs from pyproject.toml")


def _check_entry_points(data: bytes, project: dict) -> None:
    scripts = project.get("scripts", {})
    expected = "\n".join(
        ["[console_scripts]", *(f"{name} = {target}" for name, target in scripts.items()), "", ""]
    ).encode()
    if data != expected:
        raise ValueError("wheel entry points differ from pyproject.toml")


def _check_record(package: zipfile.ZipFile, record_name: str, files: set[str]) -> None:
    try:
        rows = list(csv.reader(io.StringIO(package.read(record_name).decode("utf-8"))))
    except (csv.Error, UnicodeError) as exc:
        raise ValueError("invalid wheel RECORD") from exc
    if len(rows) != len(files) or any(len(row) != 3 for row in rows):
        raise ValueError("wheel RECORD member list differs from package")

    recorded = {row[0] for row in rows}
    if recorded != files or len(recorded) != len(rows):
        raise ValueError("wheel RECORD member list differs from package")
    for name, digest, size in rows:
        if name == record_name:
            if digest or size:
                raise ValueError("wheel RECORD self-entry is invalid")
            continue
        content = package.read(name)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        if digest != "sha256=" + encoded or size != str(len(content)):
            raise ValueError("wheel RECORD digest or size differs from package")


def _check_sdist_pyproject(data: bytes, source: bytes) -> None:
    """Allow formatting changes from uv while rejecting changed settings and comments."""
    if b"#" in data or tomllib.loads(data.decode("utf-8")) != tomllib.loads(source.decode("utf-8")):
        raise ValueError("sdist active pyproject differs from reviewed source")


def check_artifacts(wheel: Path, sdist: Path) -> None:
    """Require expected members, metadata, and byte-identical bundled source files."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if __version__ != version:
        raise ValueError("package __version__ differs from pyproject.toml")
    expected = _source_files()
    schema_prefix = "trailgate/schemas/"
    if not all(schema_prefix + name in expected for name in SCHEMA_FILES):
        raise ValueError("a published schema is missing from the source package")

    dist_info = f"trailgate-{version}.dist-info"
    wheel_files = set(expected) | {
        f"{dist_info}/{name}"
        for name in ("METADATA", "WHEEL", "entry_points.txt", "RECORD", "licenses/LICENSE")
    }
    with zipfile.ZipFile(wheel) as package:
        _check_wheel_container(package)
        _check_members(
            [(item.filename, _wheel_member_kind(item)) for item in package.infolist()],
            wheel_files,
            "wheel",
        )
        for name, source in expected.items():
            if package.read(name) != source:
                raise ValueError(f"wheel source bytes differ: {name}")

        metadata_name = f"{dist_info}/METADATA"
        metadata_bytes = package.read(metadata_name)
        _check_metadata(metadata_bytes, project)
        _check_entry_points(package.read(f"{dist_info}/entry_points.txt"), project)
        if package.read(f"{dist_info}/WHEEL") != EXPECTED_WHEEL:
            raise ValueError("wheel build metadata differs from reviewed toolchain")
        if package.read(f"{dist_info}/licenses/LICENSE") != (ROOT / "LICENSE").read_bytes():
            raise ValueError("wheel license differs from reviewed source")
        _check_record(package, f"{dist_info}/RECORD", wheel_files)

    prefix = f"trailgate-{version}/"
    public_files = [ROOT / name for name in ("README.md", "README.ja.md", "LICENSE")]
    public_files.extend((ROOT / "docs").glob("*.md"))
    public_files.extend((ROOT / "docs" / "ja").glob("*.md"))
    public_files.extend((ROOT / "assets").glob("*.svg"))
    sdist_files = {prefix + "src/" + name for name in expected}
    sdist_files.update(prefix + str(path.relative_to(ROOT)) for path in public_files)
    sdist_files.update(
        prefix + name for name in ("PKG-INFO", "pyproject.toml", "pyproject.toml.orig")
    )
    with tarfile.open(sdist) as package:
        members = package.getmembers()
        _check_sdist_container(sdist, members, package.pax_headers)
        _check_members(
            [
                (
                    member.name,
                    "file" if member.isfile() else "directory" if member.isdir() else "other",
                )
                for member in members
            ],
            sdist_files,
            "sdist",
        )
        files = {member.name: member for member in members if member.isfile()}
        for name, source in expected.items():
            member = files.get(prefix + "src/" + name)
            if member is None:
                raise ValueError(f"sdist package member missing: {name}")
            stream = package.extractfile(member)
            if stream is None or stream.read() != source:
                raise ValueError(f"sdist source bytes differ: {name}")
        for source_path in public_files:
            name = str(source_path.relative_to(ROOT))
            member = files.get(prefix + name)
            if member is None:
                raise ValueError(f"sdist public documentation missing: {name}")
            stream = package.extractfile(member)
            if stream is None or stream.read() != source_path.read_bytes():
                raise ValueError(f"sdist public documentation differs: {name}")
        source_pyproject = (ROOT / "pyproject.toml").read_bytes()
        if package.extractfile(files[prefix + "pyproject.toml.orig"]).read() != source_pyproject:
            raise ValueError("sdist original pyproject differs from reviewed source")
        _check_sdist_pyproject(
            package.extractfile(files[prefix + "pyproject.toml"]).read(), source_pyproject
        )
        if package.extractfile(files[prefix + "PKG-INFO"]).read() != metadata_bytes:
            raise ValueError("sdist PKG-INFO differs from reviewed wheel metadata")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    wheel = args.wheel or ROOT / "dist" / f"trailgate-{version}-py3-none-any.whl"
    sdist = args.sdist or ROOT / "dist" / f"trailgate-{version}.tar.gz"
    try:
        check_artifacts(wheel, sdist)
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"release artifact check failed: {exc}\n")
    print("Release artifact check passed: members, source, metadata, and RECORD match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
