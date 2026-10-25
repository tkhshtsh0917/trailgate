"""Enforce offline safety floors for Brotli decoders in the project lockfile."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

MINIMUM_VERSIONS = {"brotli": (1, 2, 1), "brotlicffi": (1, 2, 1, 0)}


def _version_parts(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not parts or not all(part.isascii() and part.isdecimal() for part in parts):
        raise ValueError("unrecognized Brotli version in lockfile")
    return tuple(int(part) for part in parts)


def check_lockfile(path: Path) -> list[str]:
    """Reject locked decoders below the CVE-2026-111497 fixed releases."""
    packages = tomllib.loads(path.read_text(encoding="utf-8")).get("package")
    if not isinstance(packages, list):
        raise ValueError("lockfile has no package list")

    findings = []
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise ValueError("invalid lockfile package entry")
        name = package["name"].lower()
        if name not in MINIMUM_VERSIONS:
            continue
        version = package.get("version")
        if not isinstance(version, str) or _version_parts(version) < MINIMUM_VERSIONS[name]:
            findings.append(f"{name} requires a fixed release for CVE-2026-111497")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lockfile", type=Path, default=Path(__file__).resolve().parents[1] / "uv.lock"
    )
    args = parser.parse_args()

    try:
        findings = check_lockfile(args.lockfile)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        parser.exit(2, f"dependency safety check failed: {exc}\n")
    if findings:
        parser.exit(1, "\n".join(findings) + "\n")
    print("Dependency safety check passed: no vulnerable Brotli decoder is locked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
