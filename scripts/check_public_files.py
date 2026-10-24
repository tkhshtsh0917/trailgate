"""Check publication candidates without opening files whose names suggest secrets."""

from __future__ import annotations

import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_NAMES = {".gitignore", ".python-version", "LICENSE", "uv.lock"}
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".py", ".toml", ".yaml", ".yml"}
EXCLUDED_PARTS = {".git", ".venv", "build", "dist", "__pycache__"}
SENSITIVE_CONTENT = {
    "personal_absolute_path": re.compile(r"/(?:Users|home)/[^/\s<>]+/"),
    "private_key_header": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    "provider_token": re.compile(r"\b(?:sk-|gh[pousr]_)[A-Za-z0-9_-]{16,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9_-]{20,}", re.IGNORECASE),
}


def private_path(path: Path) -> bool:
    """Reject sensitive paths before reading their contents."""
    for part in path.parts:
        name = part.lower()
        if (
            name in EXCLUDED_PARTS
            or name.startswith("private-")
            or name.startswith(".env")
            or name.endswith(".env")
            or name.endswith((".key", ".pem", ".p12", ".sqlite", ".db", ".zip"))
            or name.endswith(".local.json")
        ):
            return True
    return False


def content_issues(content: str) -> list[tuple[int, str]]:
    """Return rule names and line numbers, never matched content."""
    issues = []
    for number, line in enumerate(content.splitlines(), 1):
        issues.extend(
            (number, name) for name, pattern in SENSITIVE_CONTENT.items() if pattern.search(line)
        )
    return issues


def candidate_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return sorted({Path(item.decode()) for item in result.stdout.split(b"\0") if item})


def artifact_paths() -> list[tuple[str, str]]:
    """Inspect build member names only; never extract or print member contents."""
    members = []
    for artifact in sorted((ROOT / "dist").glob("*.tar.gz")):
        with tarfile.open(artifact) as package:
            members.extend((artifact.name, item.name) for item in package.getmembers())
    for artifact in sorted((ROOT / "dist").glob("*.whl")):
        with zipfile.ZipFile(artifact) as package:
            members.extend((artifact.name, name) for name in package.namelist())
    return members


def _candidate_issues(candidates: list[Path]) -> list[str]:
    failures = []
    for relative in candidates:
        if private_path(relative):
            failures.append(f"{relative}: sensitive path must not be published")
            continue
        path = ROOT / relative
        if path.is_symlink():
            failures.append(f"{relative}: symlink requires manual review")
            continue
        if not path.is_file():
            continue
        if relative.name not in TEXT_NAMES and relative.suffix not in TEXT_SUFFIXES:
            failures.append(f"{relative}: unknown file type requires manual review")
            continue
        if path.stat().st_size > 2_000_000:
            failures.append(f"{relative}: file exceeds the public-text review limit")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            failures.append(f"{relative}: not UTF-8 text")
            continue
        failures.extend(f"{relative}:{number}: {name}" for number, name in content_issues(content))
    return failures


def _artifact_issues() -> list[str]:
    failures = []
    for artifact, member in artifact_paths():
        if private_path(Path(member)):
            failures.append(f"{artifact}: sensitive archive member path")
    return failures


def main() -> int:
    candidates = candidate_paths()
    failures = [*_candidate_issues(candidates), *_artifact_issues()]

    if failures:
        print("Public-file check failed:", file=sys.stderr)
        for item in failures:
            print(item, file=sys.stderr)
        return 1
    print(f"Public-file check passed: {len(candidates)} candidate paths reviewed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
