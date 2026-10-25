"""Validate saved Langfuse API response pages.

The input is saved *response pages*, not Langfuse's UI or blob export format.
The original bytes are retained so later adapters can be re-run without loss.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import SnapshotError
from .observation_time import validate_observation_times

SNAPSHOT_SCHEMA = "trailgate.langfuse_api_snapshot.v1"
MAX_API_SNAPSHOT_BYTES = 64 * 1024 * 1024
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


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("duplicate JSON key")
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


def _time_range(
    manifest: dict[str, Any], key: str, start_key: str, end_key: str
) -> tuple[datetime, datetime]:
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
    return start, end


def _validate_manifest(manifest: dict[str, Any]) -> dict[str, tuple[datetime, datetime]]:
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA:
        raise SnapshotError(f"manifest schema_version must be {SNAPSHOT_SCHEMA}")
    if manifest.get("source") != "langfuse" or manifest.get("observations_api") != "v2":
        raise SnapshotError("snapshot must declare Langfuse Observations API v2")
    if manifest.get("scores_api") != "v3":
        raise SnapshotError("snapshot must declare Langfuse Scores API v3")
    if manifest.get("complete") is not True:
        raise SnapshotError("manifest must declare complete=true after all cursor pages are saved")

    observation_fields = manifest.get("observation_fields")
    score_fields = manifest.get("score_fields")
    if not isinstance(observation_fields, list) or not all(
        isinstance(x, str) for x in observation_fields
    ):
        raise SnapshotError("observation_fields must be a list of field-group names")
    if not isinstance(score_fields, list) or not all(isinstance(x, str) for x in score_fields):
        raise SnapshotError("score_fields must be a list of field-group names")

    return {
        "query": _time_range(manifest, "query", "fromStartTime", "toStartTime"),
        "score_query": _time_range(manifest, "score_query", "fromTimestamp", "toTimestamp"),
    }


def _validate_observations(
    observations: list[dict[str, Any]], query_range: tuple[datetime, datetime]
) -> None:
    for item in observations:
        row = item["row"]
        if not all(
            isinstance(row[key], str) and row[key]
            for key in ("traceId", "startTime", "projectId", "type")
        ):
            raise SnapshotError(f"observation {row['id']}: invalid core field")
        started = validate_observation_times(
            row["startTime"], row.get("endTime"), f"observation {row['id']}", require_timezone=True
        )
        if not query_range[0] <= started < query_range[1]:
            raise SnapshotError(f"observation {row['id']}: outside declared query range")


def _validate_scores(scores: list[dict[str, Any]], query_range: tuple[datetime, datetime]) -> None:
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
        if timestamp.tzinfo is None or not query_range[0] <= timestamp < query_range[1]:
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


def read_snapshot(source: Path) -> dict[str, Any]:
    """Validate a complete snapshot without contacting Langfuse."""
    source = Path(source)
    names = ("manifest.json", "observations.jsonl", "scores.jsonl", "capture.jsonl")
    total_bytes = sum((source / name).stat().st_size for name in names if (source / name).is_file())
    if total_bytes > MAX_API_SNAPSHOT_BYTES:
        raise SnapshotError("API snapshot exceeds the 64 MiB in-memory input limit")
    manifest = _read_json(source / "manifest.json")
    ranges = _validate_manifest(manifest)

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
    _validate_observations(observations, ranges["query"])
    _validate_scores(scores, ranges["score_query"])

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
