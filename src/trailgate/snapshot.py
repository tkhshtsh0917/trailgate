"""Strict, offline Langfuse API snapshot import.

The input is saved *response pages*, not Langfuse's UI or blob export format.
The original bytes are retained so later adapters can be re-run without loss.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA = "trailgate.langfuse_api_snapshot.v1"
ARCHIVE_SCHEMA = "trailgate.archive.v1"
OBSERVATION_FIELDS = {
    "id",
    "traceId",
    "startTime",
    "endTime",
    "projectId",
    "parentObservationId",
    "type",
    "name",
    "level",
    "statusMessage",
    "version",
    "environment",
    "bookmarked",
    "public",
    "userId",
    "sessionId",
    "isRootObservation",
    "input",
    "output",
    "metadata",
    "model",
    "internalModelId",
    "modelParameters",
    "modelId",
    "inputPrice",
    "outputPrice",
    "totalPrice",
    "latency",
    "timeToFirstToken",
}
SCORE_FIELDS = {
    "id",
    "projectId",
    "name",
    "value",
    "dataType",
    "source",
    "timestamp",
    "environment",
    "createdAt",
    "updatedAt",
    "comment",
    "configId",
    "metadata",
    "subject",
    "authorUserId",
    "queueId",
}


class SnapshotError(ValueError):
    """The snapshot cannot be interpreted without guessing."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(text: str, label: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise SnapshotError(f"non-JSON numeric constant: {value}")

    def parse_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise SnapshotError("non-finite JSON number")
        return number

    try:
        return json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=reject_constant,
            parse_float=parse_float,
        )
    except (json.JSONDecodeError, SnapshotError) as exc:
        raise SnapshotError(f"{label}: {exc}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = _parse_json(path.read_text(encoding="utf-8"), str(path))
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotError(f"{path}: expected a JSON object")
    return value


def _jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    if not path.is_file():
        raise SnapshotError(f"missing {path}")
    items: list[tuple[int, dict[str, Any]]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    raise SnapshotError(f"{path}:{line_no}: blank line")
                item = _parse_json(line, f"{path}:{line_no}")
                if not isinstance(item, dict):
                    raise SnapshotError(f"{path}:{line_no}: expected a JSON object")
                items.append((line_no, item))
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"cannot read {path}: {exc}") from exc
    if not items:
        raise SnapshotError(f"{path}: expected at least one response page")
    return items


def _pages(
    path: Path, required: set[str], known: set[str]
) -> tuple[list[dict[str, Any]], set[str]]:
    pages = _jsonl(path)
    rows: list[dict[str, Any]] = []
    unknown: set[str] = set()
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    for page_idx, (line_no, page) in enumerate(pages):
        data, meta = page.get("data"), page.get("meta")
        if not isinstance(data, list) or not isinstance(meta, dict):
            raise SnapshotError(f"{path}:{line_no}: expected data array and meta object")
        cursor = meta.get("cursor")
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise SnapshotError(f"{path}:{line_no}: invalid meta.cursor")
        if page_idx < len(pages) - 1 and cursor is None:
            raise SnapshotError(f"{path}:{line_no}: page ends before the next saved page")
        if page_idx == len(pages) - 1 and cursor is not None:
            raise SnapshotError(f"{path}:{line_no}: snapshot has an unsaved next page")
        if cursor is not None:
            if cursor in seen_cursors:
                raise SnapshotError(f"{path}:{line_no}: repeated cursor")
            seen_cursors.add(cursor)
        for row_idx, row in enumerate(data):
            label = f"{path}:{line_no}:data[{row_idx}]"
            if not isinstance(row, dict):
                raise SnapshotError(f"{label}: expected an object")
            absent = sorted(required - row.keys())
            if absent:
                raise SnapshotError(f"{label}: missing core fields {absent}")
            if not isinstance(row["id"], str) or not row["id"]:
                raise SnapshotError(f"{label}: id must be a nonempty string")
            if row["id"] in seen_ids:
                raise SnapshotError(f"{label}: duplicate id {row['id']}")
            seen_ids.add(row["id"])
            unknown.update(row.keys() - known)
            rows.append(
                {
                    "row": row,
                    "source": {
                        "file": path.name,
                        "line": line_no,
                        "index": row_idx,
                        "sha256": hashlib.sha256(
                            json.dumps(
                                row, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                            ).encode()
                        ).hexdigest(),
                    },
                }
            )
    return rows, unknown


def read_snapshot(source: Path) -> dict[str, Any]:
    """Validate a complete snapshot without contacting Langfuse."""
    source = Path(source)
    manifest = _read_json(source / "manifest.json")
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA:
        raise SnapshotError(f"manifest schema_version must be {SNAPSHOT_SCHEMA}")
    if manifest.get("source") != "langfuse" or manifest.get("observations_api") != "v2":
        raise SnapshotError("snapshot must declare Langfuse Observations API v2")
    if manifest.get("scores_api") != "v3":
        raise SnapshotError("snapshot must declare Langfuse Scores API v3")
    if manifest.get("complete") is not True:
        raise SnapshotError("manifest must declare complete=true after all cursor pages are saved")
    ranges: dict[str, tuple[datetime, datetime]] = {}
    for key, start_key, end_key in (
        ("query", "fromStartTime", "toStartTime"),
        ("score_query", "fromTimestamp", "toTimestamp"),
    ):
        query = manifest.get(key)
        if not isinstance(query, dict):
            raise SnapshotError(f"manifest {key} must be an object with a bounded time range")
        try:
            start = datetime.fromisoformat(query[start_key].replace("Z", "+00:00"))
            end = datetime.fromisoformat(query[end_key].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError) as exc:
            raise SnapshotError(f"manifest {key} has an invalid time range") from exc
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise SnapshotError(f"manifest {key} must have timezone-aware start < end")
        ranges[key] = (start, end)
    observation_fields = manifest.get("observation_fields")
    score_fields = manifest.get("score_fields")
    if not isinstance(observation_fields, list) or not all(
        isinstance(x, str) for x in observation_fields
    ):
        raise SnapshotError("observation_fields must be a list of field-group names")
    if not isinstance(score_fields, list) or not all(isinstance(x, str) for x in score_fields):
        raise SnapshotError("score_fields must be a list of field-group names")
    observations, unknown_observation_fields = _pages(
        source / "observations.jsonl",
        {"id", "traceId", "startTime", "projectId", "type"},
        OBSERVATION_FIELDS,
    )
    scores, unknown_score_fields = _pages(
        source / "scores.jsonl",
        {"id", "projectId", "name", "value", "dataType", "source", "timestamp"},
        SCORE_FIELDS,
    )
    for item in observations:
        row = item["row"]
        if not all(
            isinstance(row[key], str) and row[key]
            for key in ("traceId", "startTime", "projectId", "type")
        ):
            raise SnapshotError(f"observation {row['id']}: invalid core field")
        try:
            started = datetime.fromisoformat(row["startTime"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise SnapshotError(f"observation {row['id']}: invalid startTime") from exc
        if started.tzinfo is None or not ranges["query"][0] <= started < ranges["query"][1]:
            raise SnapshotError(f"observation {row['id']}: outside declared query range")
    for item in scores:
        row = item["row"]
        if not all(
            isinstance(row[key], str) and row[key]
            for key in ("projectId", "name", "dataType", "source", "timestamp")
        ):
            raise SnapshotError(f"score {row['id']}: invalid core field")
        try:
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise SnapshotError(f"score {row['id']}: invalid timestamp") from exc
        if (
            timestamp.tzinfo is None
            or not ranges["score_query"][0] <= timestamp < ranges["score_query"][1]
        ):
            raise SnapshotError(f"score {row['id']}: outside declared score_query range")
        value = row["value"]
        data_type = row["dataType"]
        valid_value = {
            "NUMERIC": isinstance(value, (int, float)) and not isinstance(value, bool),
            "BOOLEAN": isinstance(value, bool),
            "CATEGORICAL": isinstance(value, str),
            "TEXT": isinstance(value, str),
            "CORRECTION": isinstance(value, str),
        }.get(data_type, False)
        if not valid_value:
            raise SnapshotError(f"score {row['id']}: value does not match dataType {data_type}")
    inputs = ["manifest.json", "observations.jsonl", "scores.jsonl"]
    if (source / "capture.jsonl").exists():
        # Event relationships are checked by the auditor; JSON syntax is checked now.
        _jsonl(source / "capture.jsonl")
        inputs.append("capture.jsonl")
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in inputs}
    return {
        "manifest": manifest,
        "observations": observations,
        "scores": scores,
        "unknown_observation_fields": sorted(unknown_observation_fields),
        "unknown_score_fields": sorted(unknown_score_fields),
        "hashes": hashes,
    }


def _private_bytes(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def _private_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    data = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode()
    _private_bytes(path, data)


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
        raw = temp / "raw"
        raw.mkdir(mode=0o700)
        for name in parsed["hashes"]:
            _private_bytes(raw / name, (source / name).read_bytes())
            if hashlib.sha256((raw / name).read_bytes()).hexdigest() != parsed["hashes"][name]:
                raise SnapshotError(f"source changed during import: {name}")
        observations = []
        for item in parsed["observations"]:
            row = item["row"]
            observations.append(
                {
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
            )
        # API pagination does not guarantee the order a model saw the trace.
        observations.sort(key=lambda row: (row["start_time"], row["id"]))
        scores = []
        for item in parsed["scores"]:
            row = item["row"]
            scores.append(
                {
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
            )
        _private_jsonl(temp / "observations.jsonl", observations)
        _private_jsonl(temp / "scores.jsonl", scores)
        derived_hashes = {
            name: hashlib.sha256((temp / name).read_bytes()).hexdigest()
            for name in ("observations.jsonl", "scores.jsonl")
        }
        index = {
            "schema_version": ARCHIVE_SCHEMA,
            "source_schema": SNAPSHOT_SCHEMA,
            "source_hashes": parsed["hashes"],
            "derived_hashes": derived_hashes,
            "declared_complete": True,
            "observation_fields": parsed["manifest"]["observation_fields"],
            "score_fields": parsed["manifest"]["score_fields"],
            "unknown_observation_fields": parsed["unknown_observation_fields"],
            "unknown_score_fields": parsed["unknown_score_fields"],
            "observations": len(observations),
            "scores": len(scores),
            "snapshot_id": hashlib.sha256(
                json.dumps(parsed["hashes"], sort_keys=True).encode()
            ).hexdigest(),
        }
        _private_bytes(
            temp / "index.json", (json.dumps(index, indent=2, sort_keys=True) + "\n").encode()
        )
        os.rename(temp, destination)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return index


def read_archive(path: Path) -> dict[str, Any]:
    path = Path(path)
    index = _read_json(path / "index.json")
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

    def rows(name: str) -> list[dict[str, Any]]:
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

    return {
        "path": path,
        "index": index,
        "observations": rows("observations.jsonl"),
        "scores": rows("scores.jsonl"),
    }
