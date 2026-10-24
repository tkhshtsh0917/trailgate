"""Materialize and verify a private archive from a validated snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .private_io import _private_bytes, _private_jsonl
from .snapshot_input import SNAPSHOT_SCHEMA, SnapshotError, _parse_json, _read_json, read_snapshot

ARCHIVE_SCHEMA = "trailgate.archive.v1"


def _copy_raw(source: Path, raw: Path, hashes: dict[str, str]) -> None:
    raw.mkdir(mode=0o700)
    for name, expected in hashes.items():
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise SnapshotError(f"source changed during import: {name}")
        _private_bytes(raw / name, data)


def _normalize_observation(item: dict[str, Any]) -> dict[str, Any]:
    row = item["row"]
    return {
        "id": row["id"],
        "trace_id": row["traceId"],
        "project_id": row["projectId"],
        "parent_observation_id": row.get("parentObservationId"),
        "start_time": row["startTime"],
        "end_time": row.get("endTime"),
        "type": row["type"],
        "name": row.get("name"),
        "session_id": row.get("sessionId"),
        "metadata": row.get("metadata"),
        "input": row.get("input"),
        "output": row.get("output"),
        "present_fields": sorted(row.keys()),
        "source_ref": item["source"],
    }


def _normalize_score(item: dict[str, Any]) -> dict[str, Any]:
    row = item["row"]
    return {
        "id": row["id"],
        "name": row["name"],
        "value": row["value"],
        "data_type": row["dataType"],
        "source": row["source"],
        "timestamp": row["timestamp"],
        "subject": row.get("subject"),
        "present_fields": sorted(row.keys()),
        "source_ref": item["source"],
    }


def _archive_index(
    parsed: dict[str, Any], derived_hashes: dict[str, str], observations: int, scores: int
) -> dict[str, Any]:
    return {
        "schema_version": ARCHIVE_SCHEMA,
        "source_schema": SNAPSHOT_SCHEMA,
        "source_hashes": parsed["hashes"],
        "derived_hashes": derived_hashes,
        "declared_complete": True,
        "observation_fields": parsed["manifest"]["observation_fields"],
        "score_fields": parsed["manifest"]["score_fields"],
        "unknown_observation_fields": parsed["unknown_observation_fields"],
        "unknown_score_fields": parsed["unknown_score_fields"],
        "observations": observations,
        "scores": scores,
        "snapshot_id": hashlib.sha256(
            json.dumps(parsed["hashes"], sort_keys=True).encode()
        ).hexdigest(),
    }


def ingest_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    """Create a private, immutable local archive; never overwrite an existing one."""
    parsed = read_snapshot(source)
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists():
        raise SnapshotError(f"destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise SnapshotError(f"destination parent does not exist: {destination.parent}")
    temp = Path(tempfile.mkdtemp(prefix=".trailgate-", dir=destination.parent))
    try:
        os.chmod(temp, 0o700)
        _copy_raw(source, temp / "raw", parsed["hashes"])

        observations = [_normalize_observation(item) for item in parsed["observations"]]
        # API pagination does not guarantee the order a model saw the trace.
        observations.sort(key=lambda row: (row["start_time"], row["id"]))
        scores = [_normalize_score(item) for item in parsed["scores"]]

        _private_jsonl(temp / "observations.jsonl", observations)
        _private_jsonl(temp / "scores.jsonl", scores)
        derived_hashes = {
            name: hashlib.sha256((temp / name).read_bytes()).hexdigest()
            for name in ("observations.jsonl", "scores.jsonl")
        }
        index = _archive_index(parsed, derived_hashes, len(observations), len(scores))
        _private_bytes(
            temp / "index.json", (json.dumps(index, indent=2, sort_keys=True) + "\n").encode()
        )
        os.rename(temp, destination)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return index


def _validate_index(path: Path, index: dict[str, Any]) -> None:
    if index.get("schema_version") != ARCHIVE_SCHEMA:
        raise SnapshotError(f"archive schema_version must be {ARCHIVE_SCHEMA}")
    source_hashes = index.get("source_hashes")
    derived_hashes = index.get("derived_hashes")
    allowed_source = {"manifest.json", "observations.jsonl", "scores.jsonl", "capture.jsonl"}
    if (
        not isinstance(source_hashes, dict)
        or not {"manifest.json", "observations.jsonl", "scores.jsonl"} <= source_hashes.keys()
        or not source_hashes.keys() <= allowed_source
        or not isinstance(derived_hashes, dict)
        or derived_hashes.keys() != {"observations.jsonl", "scores.jsonl"}
    ):
        raise SnapshotError("invalid archive hash manifest")
    calculated_snapshot_id = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True).encode()
    ).hexdigest()
    if index.get("snapshot_id") != calculated_snapshot_id:
        raise SnapshotError("snapshot_id does not match source hashes")
    for name, expected in source_hashes.items():
        file = path / "raw" / name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise SnapshotError(f"raw archive hash mismatch: {file}")
    for name, expected in derived_hashes.items():
        file = path / name
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise SnapshotError(f"derived archive hash mismatch: {file}")


def _archive_rows(path: Path, name: str) -> list[dict[str, Any]]:
    result = []
    file = path / name
    if not file.is_file():
        raise SnapshotError(f"missing {file}")

    try:
        with file.open("r", encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                item = _parse_json(line, f"{file}:{line_no}")
                if not isinstance(item, dict):
                    raise SnapshotError(f"{file}:{line_no}: expected an object")
                result.append(item)
    except UnicodeError as exc:
        raise SnapshotError(f"cannot read {file}: {exc}") from exc
    return result


def read_archive(path: Path) -> dict[str, Any]:
    path = Path(path)
    index = _read_json(path / "index.json")
    _validate_index(path, index)

    return {
        "path": path,
        "index": index,
        "observations": _archive_rows(path, "observations.jsonl"),
        "scores": _archive_rows(path, "scores.jsonl"),
    }
