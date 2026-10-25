"""Check release archive members, metadata, and bundled source bytes."""

from __future__ import annotations

import argparse
import stat
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

from trailgate import __version__
from trailgate.schemas import SCHEMA_FILES

ROOT = Path(__file__).resolve().parents[1]


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
        _check_members(
            [(item.filename, _wheel_member_kind(item)) for item in package.infolist()],
            wheel_files,
            "wheel",
        )
        for name, source in expected.items():
            if package.read(name) != source:
                raise ValueError(f"wheel source bytes differ: {name}")

        metadata_name = f"{dist_info}/METADATA"
        metadata = BytesParser().parsebytes(package.read(metadata_name))
        python_requirement = metadata["Requires-Python"].replace(" ", "")
        if metadata["Version"] != version or python_requirement != project[
            "requires-python"
        ].replace(" ", ""):
            raise ValueError("wheel metadata differs from pyproject.toml")
        if metadata.get_all("Requires-Dist") and any(
            "jsonschema" in item for item in metadata.get_all("Requires-Dist")
        ):
            raise ValueError("development-only schema validator leaked into runtime dependencies")

    prefix = f"trailgate-{version}/"
    public_files = [ROOT / name for name in ("README.md", "README.ja.md", "LICENSE")]
    public_files.extend((ROOT / "docs").glob("*.md"))
    sdist_files = {prefix + "src/" + name for name in expected}
    sdist_files.update(prefix + str(path.relative_to(ROOT)) for path in public_files)
    sdist_files.update(
        prefix + name for name in ("PKG-INFO", "pyproject.toml", "pyproject.toml.orig")
    )
    with tarfile.open(sdist) as package:
        members = package.getmembers()
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
        if (
            package.extractfile(files[prefix + "pyproject.toml.orig"]).read()
            != (ROOT / "pyproject.toml").read_bytes()
        ):
            raise ValueError("sdist original pyproject differs from reviewed source")


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
    print("Release artifact check passed: source and schemas match both packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
