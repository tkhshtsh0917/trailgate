"""Stream enriched Langfuse blob files into a private, deterministic archive."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .archive import _validate_index
from .blob_manifest import BLOB_SOURCE_SCHEMA, object_file, read_blob_manifest
from .private_io import _private_bytes
from .snapshot_input import SnapshotError, _parse_json

BULK_ARCHIVE_SCHEMA = "trailgate.archive.v2"
MAX_BLOB_ROW_CHARS = 16 * 1024 * 1024


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_object(source: Path, destination: Path, expected_hash: str) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists():
        if _hash_file(destination) != expected_hash:
            raise SnapshotError("stored blob object changed")
        return

    fd, name = tempfile.mkstemp(prefix=".trailgate-copy-", dir=destination.parent)
    temporary = Path(name)
    try:
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if _hash_file(temporary) != expected_hash or _hash_file(source) != expected_hash:
            raise SnapshotError("blob source changed during import")
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _parquet_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _parquet_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_parquet_value(item) for item in value]
    return value


def _rows(path: Path, entry: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    if entry["fileType"] == "PARQUET":
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise SnapshotError("Parquet import requires the bulk-parquet extra") from exc
        table = parquet.ParquetFile(path)
        index = 0
        for batch in table.iter_batches(batch_size=16):
            for row in batch.to_pylist():
                if not isinstance(row, dict):
                    raise SnapshotError("Parquet record is not an object")
                yield index, _parquet_value(row)
                index += 1
        return

    opener = gzip.open if entry["compressed"] else open
    try:
        with opener(path, "rt", encoding="utf-8") as stream:
            line_no = 0
            while line := stream.readline(MAX_BLOB_ROW_CHARS + 1):
                line_no += 1
                if len(line) > MAX_BLOB_ROW_CHARS or not line.endswith("\n"):
                    raise SnapshotError("blob JSONL row exceeds the limit or is incomplete")
                if not line.strip():
                    raise SnapshotError("blank line in blob JSONL")
                row = _parse_json(line, f"{entry['key']}:{line_no}")
                if not isinstance(row, dict):
                    raise SnapshotError("blob JSONL record is not an object")
                yield line_no, row
    except (OSError, UnicodeError, EOFError) as exc:
        raise SnapshotError(f"cannot read blob file: {entry['key']}") from exc


def _source_ref(entry: dict[str, Any], index: int, row: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return {"file": entry["key"], "index": index, "sha256": digest}


def _observation(row: dict[str, Any], entry: dict[str, Any], index: int) -> dict[str, Any]:
    required = ("id", "trace_id", "project_id", "start_time", "type")
    if not all(isinstance(row.get(key), str) and row[key] for key in required):
        raise SnapshotError("blob observation is missing core fields")
    return {
        "id": row["id"],
        "trace_id": row["trace_id"],
        "project_id": row["project_id"],
        "parent_observation_id": row.get("parent_observation_id") or None,
        "start_time": row["start_time"],
        "end_time": row.get("end_time"),
        "type": row["type"],
        "name": row.get("name"),
        "session_id": row.get("session_id") or None,
        "metadata": row.get("metadata"),
        "input": row.get("input"),
        "output": row.get("output"),
        "present_fields": sorted(row),
        "source_ref": _source_ref(entry, index, row),
    }


def _score(row: dict[str, Any], entry: dict[str, Any], index: int) -> dict[str, Any]:
    required = ("id", "project_id", "name", "data_type", "source", "timestamp")
    if not all(isinstance(row.get(key), str) and row[key] for key in required):
        raise SnapshotError("blob score is missing core fields")
    subject = None
    for key, kind in (
        ("observation_id", "observation"),
        ("trace_id", "trace"),
        ("session_id", "session"),
        ("dataset_run_id", "dataset_run"),
    ):
        if isinstance(row.get(key), str) and row[key]:
            subject = {"kind": kind, "id": row[key]}
            break
    if subject is None:
        raise SnapshotError("blob score subject is unknown")
    value = (
        row.get("string_value") if row["data_type"] in {"CATEGORICAL", "TEXT"} else row.get("value")
    )
    if row["data_type"] == "BOOLEAN":
        if value not in (0, 1, False, True):
            raise SnapshotError("blob BOOLEAN score value must be 0 or 1")
        value = bool(value)
    elif row["data_type"] in {"CATEGORICAL", "TEXT"} and not isinstance(value, str):
        raise SnapshotError("blob string score value is missing")
    elif row["data_type"] == "NUMERIC" and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        raise SnapshotError("blob numeric score value is missing")
    elif row["data_type"] not in {"BOOLEAN", "CATEGORICAL", "TEXT", "NUMERIC"}:
        raise SnapshotError("unsupported blob score data_type")
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "name": row["name"],
        "value": value,
        "data_type": row["data_type"],
        "source": row["source"],
        "timestamp": row["timestamp"],
        "subject": subject,
        "present_fields": sorted(row),
        "source_ref": _source_ref(entry, index, row),
    }


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS records (table_name TEXT, id TEXT, sort_key TEXT, "
        "content_hash TEXT, row_json TEXT, PRIMARY KEY (table_name, id))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS processed (key TEXT PRIMARY KEY, sha256 TEXT, row_count INTEGER)"
    )
    connection.commit()
    return connection


def _process_file(
    connection: sqlite3.Connection,
    path: Path,
    entry: dict[str, Any],
    project_id: str,
    source_hash: str,
) -> None:
    stored = connection.execute(
        "SELECT sha256 FROM processed WHERE key=?", (entry["key"],)
    ).fetchone()
    if stored is not None:
        if stored[0] != source_hash:
            raise SnapshotError("processed blob object changed")
        return

    with connection:
        count = 0
        for index, row in _rows(path, entry):
            if row.get("project_id") != project_id:
                raise SnapshotError("blob row is outside manifest projectId")
            normalized = (
                _observation(row, entry, index)
                if entry["table"] == "observations_v2"
                else _score(row, entry, index)
            )
            content_hash = normalized["source_ref"]["sha256"]
            table_name = entry["table"]
            current = connection.execute(
                "SELECT content_hash FROM records WHERE table_name=? AND id=?",
                (table_name, normalized["id"]),
            ).fetchone()
            if current is not None and current[0] != content_hash:
                raise SnapshotError("same blob row ID has conflicting content")
            if current is None:
                sort_key = (
                    normalized["start_time"]
                    if table_name == "observations_v2"
                    else normalized["timestamp"]
                )
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
                    (
                        table_name,
                        normalized["id"],
                        sort_key,
                        content_hash,
                        json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    ),
                )
            count += 1
        if entry["rowCount"] is not None and count != entry["rowCount"]:
            raise SnapshotError("blob rowCount differs from file content")
        connection.execute(
            "INSERT INTO processed VALUES (?, ?, ?)", (entry["key"], source_hash, count)
        )


def _write_derived(connection: sqlite3.Connection, work: Path, table: str, name: str) -> str:
    fd, temp_name = tempfile.mkstemp(prefix=".trailgate-derived-", dir=work)
    temporary = Path(temp_name)
    target = work / name
    try:
        with os.fdopen(fd, "wb") as output:
            cursor = connection.execute(
                "SELECT row_json FROM records WHERE table_name=? ORDER BY sort_key, id", (table,)
            )
            for (row_json,) in cursor:
                output.write(row_json.encode() + b"\n")
            output.flush()
            os.fsync(output.fileno())
        digest = _hash_file(temporary)
        target.unlink(missing_ok=True)
        os.link(temporary, target)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def ingest_blob_export(
    object_root: Path, manifest_path: Path, destination: Path, *, resume: bool = False
) -> dict[str, Any]:
    """Import one complete enriched run; retain a resumable private spool on failure."""
    object_root, destination = Path(object_root).resolve(), Path(destination).absolute()
    manifest_path = Path(manifest_path).resolve()
    manifest = read_blob_manifest(object_root, manifest_path)
    if destination.exists() or not destination.parent.is_dir():
        raise SnapshotError("blob destination exists or its parent is missing")
    work = destination.with_name(f".{destination.name}.work")
    manifest_hash = _hash_file(manifest_path)
    if resume:
        if not work.is_dir():
            raise SnapshotError("no incomplete blob import checkpoint exists")
        state = json.loads((work / "state.json").read_text())
        if state != {"manifest_sha256": manifest_hash}:
            raise SnapshotError("blob manifest differs from checkpoint")
    else:
        work.mkdir(mode=0o700)
        _private_bytes(
            work / "state.json", (json.dumps({"manifest_sha256": manifest_hash}) + "\n").encode()
        )

    raw = work / "raw"
    raw.mkdir(mode=0o700, exist_ok=True)
    _copy_object(manifest_path, raw / "manifest.json", manifest_hash)
    database_path = work / "spool.sqlite"
    if not database_path.exists() and (work / "index.json").exists():
        index = json.loads((work / "index.json").read_text())
        expected_keys = {"manifest.json", *(entry["key"] for entry in manifest["files"])}
        if set(index.get("source_hashes", {})) != expected_keys:
            raise SnapshotError("finalized blob checkpoint source set differs from manifest")
        _validate_index(work, index)
        for entry in manifest["files"]:
            if (
                _hash_file(object_file(object_root, entry["key"]))
                != index["source_hashes"][entry["key"]]
            ):
                raise SnapshotError("blob source changed after checkpoint was finalized")
        os.rename(work, destination)
        return index

    connection = _database(database_path)
    try:
        source_hashes = {"manifest.json": manifest_hash}
        for entry in sorted(manifest["files"], key=lambda item: item["key"]):
            key = entry["key"]
            source = object_file(object_root, key)
            if source.stat().st_size != entry["sizeBytes"]:
                raise SnapshotError("blob file size differs from manifest")
            digest = _hash_file(source)
            copied = raw / key
            _copy_object(source, copied, digest)
            _process_file(connection, copied, entry, manifest["projectId"], digest)
            if _hash_file(source) != digest:
                raise SnapshotError("blob source changed after processing")
            source_hashes[key] = digest

        derived_hashes = {
            "observations.jsonl": _write_derived(
                connection, work, "observations_v2", "observations.jsonl"
            ),
            "scores.jsonl": _write_derived(connection, work, "scores", "scores.jsonl"),
        }
        observations = connection.execute(
            "SELECT COUNT(*) FROM records WHERE table_name='observations_v2'"
        ).fetchone()[0]
        scores = connection.execute(
            "SELECT COUNT(*) FROM records WHERE table_name='scores'"
        ).fetchone()[0]
        index = {
            "schema_version": BULK_ARCHIVE_SCHEMA,
            "source_schema": BLOB_SOURCE_SCHEMA,
            "source_hashes": source_hashes,
            "derived_hashes": derived_hashes,
            "snapshot_id": hashlib.sha256(
                json.dumps(source_hashes, sort_keys=True).encode()
            ).hexdigest(),
            "project_id": manifest["projectId"],
            "window": manifest["window"],
            "declared_complete": True,
            "observations": observations,
            "scores": scores,
            "unknown_observation_fields": [],
            "unknown_score_fields": [],
            "capture_available": False,
        }
        (work / "index.json").unlink(missing_ok=True)
        _private_bytes(work / "index.json", (json.dumps(index, sort_keys=True) + "\n").encode())
    finally:
        connection.close()

    database_path.unlink()
    os.rename(work, destination)
    return index
