"""Check that built packages contain the reviewed source and schema files."""

from __future__ import annotations

import argparse
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from trailgate import __version__
from trailgate.schemas import SCHEMA_FILES

ROOT = Path(__file__).resolve().parents[1]


def _source_files() -> dict[str, bytes]:
    return {
        str(path.relative_to(ROOT / "src")): path.read_bytes()
        for path in (ROOT / "src" / "trailgate").rglob("*")
        if path.is_file() and path.suffix in {".py", ".json"}
    }


def check_artifacts(wheel: Path, sdist: Path) -> None:
    """Require matching version metadata and byte-identical bundled package files."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if __version__ != version:
        raise ValueError("package __version__ differs from pyproject.toml")
    expected = _source_files()
    schema_prefix = "trailgate/schemas/"
    if not all(schema_prefix + name in expected for name in SCHEMA_FILES):
        raise ValueError("a published schema is missing from the source package")

    with zipfile.ZipFile(wheel) as package:
        members = {name for name in package.namelist() if not name.endswith("/")}
        wheel_source = {name for name in members if name.startswith("trailgate/")}
        if wheel_source != expected.keys():
            raise ValueError("wheel package members differ from reviewed source files")
        if any(
            not name.startswith(("trailgate/", f"trailgate-{version}.dist-info/"))
            for name in members
        ):
            raise ValueError("wheel contains an unexpected top-level member")
        for name, source in expected.items():
            if package.read(name) != source:
                raise ValueError(f"wheel source bytes differ: {name}")

        metadata_name = f"trailgate-{version}.dist-info/METADATA"
        if metadata_name not in members:
            raise ValueError("wheel metadata path does not match project version")
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
    with tarfile.open(sdist) as package:
        files = {member.name: member for member in package.getmembers() if member.isfile()}
        if any(not name.startswith(prefix) for name in files):
            raise ValueError("sdist member has an unexpected root")
        package_files = {
            name.removeprefix(prefix + "src/")
            for name in files
            if name.startswith(prefix + "src/trailgate/")
        }
        if package_files != expected.keys():
            raise ValueError("sdist package members differ from reviewed source files")
        for name, source in expected.items():
            member = files.get(prefix + "src/" + name)
            if member is None:
                raise ValueError(f"sdist package member missing: {name}")
            stream = package.extractfile(member)
            if stream is None or stream.read() != source:
                raise ValueError(f"sdist source bytes differ: {name}")
        public_files = [ROOT / name for name in ("README.md", "README.ja.md", "LICENSE")]
        public_files.extend((ROOT / "docs").glob("*.md"))
        for source_path in public_files:
            name = str(source_path.relative_to(ROOT))
            member = files.get(prefix + name)
            if member is None:
                raise ValueError(f"sdist public documentation missing: {name}")
            stream = package.extractfile(member)
            if stream is None or stream.read() != source_path.read_bytes():
                raise ValueError(f"sdist public documentation differs: {name}")


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
