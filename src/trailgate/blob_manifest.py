"""Validate a locally mirrored Langfuse enriched-export run manifest."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .snapshot_input import SnapshotError, _read_json

BLOB_SOURCE_SCHEMA = "langfuse.blob_manifest.v1"
TABLES = {"observations_v2", "scores"}


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SnapshotError("blob manifest window needs timestamp strings")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError("invalid blob manifest timestamp") from exc
    if parsed.tzinfo is None:
        raise SnapshotError("blob manifest timestamps need timezones")
    return parsed


def object_file(root: Path, key: str) -> Path:
    relative = Path(key)
    if relative.is_absolute() or not key or ".." in relative.parts:
        raise SnapshotError("blob object key must be a safe relative path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise SnapshotError(f"blob manifest object is missing or outside object root: {key}")
    return path


def read_blob_manifest(object_root: Path, manifest_path: Path) -> dict[str, Any]:
    """Require the complete enriched run and every referenced local object."""
    object_root = Path(object_root).resolve()
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_relative_to(object_root) or not manifest_path.is_file():
        raise SnapshotError("blob manifest must be inside object root")
    manifest = _read_json(manifest_path)
    if manifest.get("version") != 1 or manifest.get("exportSource") != "EVENTS":
        raise SnapshotError("expected an enriched Langfuse blob manifest version 1")
    if not isinstance(manifest.get("projectId"), str) or not manifest["projectId"]:
        raise SnapshotError("blob manifest projectId is required")
    window = manifest.get("window")
    if not isinstance(window, dict) or _time(window.get("minTimestamp")) >= _time(
        window.get("maxTimestamp")
    ):
        raise SnapshotError("blob manifest window must have start < end")
    if manifest.get("maxTimestamp") != window["maxTimestamp"]:
        raise SnapshotError("blob manifest maxTimestamp differs from window")
    tables = manifest.get("tables")
    if (
        not isinstance(tables, list)
        or not all(isinstance(table, str) for table in tables)
        or set(tables) != TABLES
    ):
        raise SnapshotError("blob manifest must include observations_v2 and scores only")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise SnapshotError("blob manifest files are required")
    seen_keys = set()
    table_names = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise SnapshotError("blob manifest file entry must be an object")
        key, table = entry.get("key"), entry.get("table")
        if (
            not isinstance(key, str)
            or not isinstance(table, str)
            or table not in TABLES
            or key in seen_keys
        ):
            raise SnapshotError("invalid or duplicate blob manifest file key")
        seen_keys.add(key)
        table_names.add(table)
        file_type = entry.get("fileType")
        compressed = entry.get("compressed")
        expected_format = (
            {
                ("JSONL", False): "jsonl-raw",
                ("JSONL", True): "jsonl-gzip",
                ("PARQUET", False): "parquet",
            }.get((file_type, compressed))
            if isinstance(file_type, str) and isinstance(compressed, bool)
            else None
        )
        if entry.get("format") != expected_format or expected_format is None:
            raise SnapshotError("unsupported blob file format or compression")
        size = entry.get("sizeBytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SnapshotError("invalid blob file sizeBytes")
        count = entry.get("rowCount")
        if (
            file_type == "JSONL"
            and (not isinstance(count, int) or isinstance(count, bool) or count < 0)
        ) or (file_type == "PARQUET" and count is not None):
            raise SnapshotError("invalid blob file rowCount")
        path = object_file(object_root, key)
        if path.stat().st_size != size:
            raise SnapshotError(f"blob file size mismatch: {key}")
    if table_names != TABLES:
        raise SnapshotError("blob manifest files omit an enriched table")
    return manifest
