"""Bounded, read-only Langfuse API collection with per-page checkpoints."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .archive import _validate_index
from .private_io import _private_bytes
from .snapshot_input import (
    MAX_API_SNAPSHOT_BYTES,
    SNAPSHOT_SCHEMA,
    SnapshotError,
    _parse_json,
    _read_json,
    read_snapshot,
)

PULL_SCHEMA = "trailgate.langfuse_pull.v1"
MAX_API_PAGE_BYTES = 32 * 1024 * 1024
TABLES = ("observations", "scores")
ENDPOINTS = {
    "observations": "/api/public/v2/observations",
    "scores": "/api/public/v3/scores",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class PullSpec:
    base_url: str
    project_id: str
    from_time: str
    to_time: str
    observation_fields: tuple[str, ...] = ("core", "basic", "io", "metadata", "model")
    score_fields: tuple[str, ...] = ("details", "subject")
    trace_id: str | None = None
    limit: int = 100
    max_pages: int = 1000
    timeout_seconds: float = 20.0
    score_from_time: str | None = None
    score_to_time: str | None = None


def _score_bounds(spec: PullSpec) -> tuple[str, str]:
    return (
        spec.from_time if spec.score_from_time is None else spec.score_from_time,
        spec.to_time if spec.score_to_time is None else spec.score_to_time,
    )


def _parse_bound(value: str) -> datetime:
    if not isinstance(value, str):
        raise SnapshotError("invalid pull time range")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError("invalid pull time range") from exc
    if parsed.tzinfo is None:
        raise SnapshotError("pull time bounds require timezones")
    return parsed


def _validate_spec(spec: PullSpec) -> None:
    parsed = urllib.parse.urlsplit(spec.base_url)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
    ):
        raise SnapshotError("base_url must contain only scheme and host")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise SnapshotError("base_url must use HTTPS except for loopback")
    if not parsed.hostname or not spec.project_id:
        raise SnapshotError("base_url host and project_id are required")
    if spec.trace_id is not None and not spec.trace_id:
        raise SnapshotError("trace_id must be nonempty when supplied")
    if spec.trace_id and "subject" not in spec.score_fields:
        raise SnapshotError("trace-scoped pull requires score subject fields")
    if not 1 <= spec.limit <= 100 or not 1 <= spec.max_pages <= 100_000:
        raise SnapshotError("limit must be 1..100 and max_pages must be 1..100000")
    if not 0 < spec.timeout_seconds <= 120:
        raise SnapshotError("timeout_seconds must be in (0, 120]")
    for fields in (spec.observation_fields, spec.score_fields):
        if not fields or not all(re.fullmatch(r"[a-z_]+", field) for field in fields):
            raise SnapshotError("field groups must be nonempty names")
    if "core" not in spec.observation_fields:
        raise SnapshotError("observation fields must include core")
    start, end = _parse_bound(spec.from_time), _parse_bound(spec.to_time)
    score_from, score_to = _score_bounds(spec)
    if start >= end or _parse_bound(score_from) >= _parse_bound(score_to):
        raise SnapshotError("pull requires timezone-aware start < end")


def preview_pull(spec: PullSpec) -> dict[str, Any]:
    """Return scope and limits without reading credentials or contacting Langfuse."""
    _validate_spec(spec)
    return {
        "schema_version": PULL_SCHEMA,
        "base_url": spec.base_url,
        "project_id": spec.project_id,
        "from_time": spec.from_time,
        "to_time": spec.to_time,
        "score_from_time": _score_bounds(spec)[0],
        "score_to_time": _score_bounds(spec)[1],
        "trace_id": spec.trace_id,
        "observation_fields": list(spec.observation_fields),
        "score_fields": list(spec.score_fields),
        "limit": spec.limit,
        "max_pages": spec.max_pages,
        "model_calls": 0,
        "write_methods": [],
    }


def _request_page(
    spec: PullSpec, table: str, cursor: str | None, credentials: tuple[str, str]
) -> bytes:
    score_from, score_to = _score_bounds(spec)
    params = {
        "fields": ",".join(
            spec.observation_fields if table == "observations" else spec.score_fields
        ),
        "limit": str(spec.limit),
        "fromStartTime" if table == "observations" else "fromTimestamp": (
            spec.from_time if table == "observations" else score_from
        ),
        "toStartTime" if table == "observations" else "toTimestamp": (
            spec.to_time if table == "observations" else score_to
        ),
    }
    if spec.trace_id:
        params["traceId"] = spec.trace_id
    if cursor:
        params["cursor"] = cursor
    url = spec.base_url.rstrip("/") + ENDPOINTS[table] + "?" + urllib.parse.urlencode(params)
    auth = base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))

    for attempt in range(4):
        try:
            with opener.open(request, timeout=spec.timeout_seconds) as response:
                if response.status != 200:
                    raise SnapshotError(f"Langfuse {table} returned HTTP {response.status}")
                data = response.read(MAX_API_PAGE_BYTES + 1)
                if len(data) > MAX_API_PAGE_BYTES:
                    raise SnapshotError(f"Langfuse {table} page exceeds 32 MiB")
                return data
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise SnapshotError(f"Langfuse {table} returned HTTP {exc.code}") from exc
            retry_after = exc.headers.get("Retry-After")
            delay = (
                float(retry_after) if retry_after and retry_after.isdigit() else 0.25 * 2**attempt
            )
            time.sleep(min(delay, 5.0))
        except urllib.error.URLError as exc:
            raise SnapshotError(f"Langfuse {table} connection failed") from exc
    raise AssertionError("retry loop must return or raise")


def _page_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    files = sorted(directory.glob("*.json"))
    for index, file in enumerate(files):
        if not re.fullmatch(rf"{index:06d}\.[0-9a-f]{{64}}\.json", file.name):
            raise SnapshotError("pull checkpoint page sequence is incomplete")
        if _hash_file(file) != file.name.split(".")[1]:
            raise SnapshotError("pull checkpoint page hash mismatch")
    return files


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_private_bytes(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".trailgate-page-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _page_state(directory: Path) -> tuple[int, str | None, bool, set[str], set[str]]:
    files = _page_files(directory)
    seen_ids = set()
    seen_cursors = set()
    for index, file in enumerate(files):
        try:
            page = _parse_json(file.read_text(encoding="utf-8"), str(file))
        except (OSError, UnicodeError) as exc:
            raise SnapshotError(f"cannot read checkpoint page: {file}") from exc
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise SnapshotError("invalid pull checkpoint page")
        meta = page.get("meta")
        if not isinstance(meta, dict):
            raise SnapshotError("pull checkpoint page lacks meta")
        cursor = meta.get("cursor")
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise SnapshotError("invalid pull checkpoint cursor")
        if index < len(files) - 1 and cursor is None:
            raise SnapshotError("pull checkpoint has pages after terminal cursor")
        if cursor is not None:
            if cursor in seen_cursors:
                raise SnapshotError("repeated pull checkpoint cursor")
            seen_cursors.add(cursor)
        for row in page["data"]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise SnapshotError("invalid row in pull checkpoint")
            if row["id"] in seen_ids:
                raise SnapshotError("duplicate row in pull checkpoint")
            seen_ids.add(row["id"])
    last = files[-1] if files else None
    if last is None:
        return 0, None, False, seen_ids, seen_cursors
    page = _parse_json(last.read_text(encoding="utf-8"), str(last))
    cursor = page["meta"].get("cursor")
    return len(files), cursor, cursor is None, seen_ids, seen_cursors


def _collect_table(spec: PullSpec, root: Path, table: str, credentials: tuple[str, str]) -> int:
    directory = root / "pages" / table
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    count, cursor, complete, seen_ids, seen_cursors = _page_state(directory)
    saved_bytes = sum(
        page_file.stat().st_size
        for saved_table in TABLES
        for page_file in (root / "pages" / saved_table).glob("*.json")
    )
    while not complete:
        if count >= spec.max_pages:
            raise SnapshotError(f"{table} reached max_pages; checkpoint remains incomplete")
        raw = _request_page(spec, table, cursor, credentials)
        try:
            page = _parse_json(raw.decode("utf-8"), f"{table} page {count}")
        except UnicodeError as exc:
            raise SnapshotError(f"{table} page is not UTF-8") from exc
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise SnapshotError(f"{table} page lacks data array")
        meta = page.get("meta")
        next_cursor = meta.get("cursor") if isinstance(meta, dict) else None
        if not isinstance(meta, dict) or (
            next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor)
        ):
            raise SnapshotError(f"{table} page has invalid meta.cursor")
        if next_cursor is not None and next_cursor in seen_cursors:
            raise SnapshotError(f"{table} returned a repeated cursor")
        for row in page["data"]:
            if not isinstance(row, dict) or row.get("projectId") != spec.project_id:
                raise SnapshotError(f"{table} page has a row outside project_id")
            if spec.trace_id and table == "observations" and row.get("traceId") != spec.trace_id:
                raise SnapshotError("observation page has a row outside trace_id")
            if spec.trace_id and table == "scores":
                subject = row.get("subject")
                if not isinstance(subject, dict):
                    raise SnapshotError("trace-scoped score lacks subject")
                subject_trace = (
                    subject.get("id")
                    if subject.get("kind") == "trace"
                    else subject.get("traceId")
                    if subject.get("kind") == "observation"
                    else None
                )
                if subject_trace != spec.trace_id:
                    raise SnapshotError("score page has a row outside trace_id")
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id or row_id in seen_ids:
                raise SnapshotError(f"{table} page has missing or duplicate row ID")
            seen_ids.add(row_id)
        if saved_bytes + len(raw) > MAX_API_SNAPSHOT_BYTES:
            raise SnapshotError("pull exceeds the 64 MiB API snapshot limit")
        digest = hashlib.sha256(raw).hexdigest()
        _atomic_private_bytes(directory / f"{count:06d}.{digest}.json", raw)
        saved_bytes += len(raw)
        count += 1
        cursor = next_cursor
        complete = cursor is None
        if cursor is not None:
            seen_cursors.add(cursor)
    return count


def _materialize_pages(root: Path, table: str) -> None:
    target = root / f"{table}.jsonl"
    if target.exists():
        raise SnapshotError(f"snapshot file already exists: {target}")
    fd, name = tempfile.mkstemp(prefix=f".trailgate-{table}-", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            for file in _page_files(root / "pages" / table):
                output.write(file.read_bytes().rstrip(b"\r\n") + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_materialized(root: Path, table: str) -> None:
    """Require an existing JSONL to be exactly the saved page projection."""
    expected = hashlib.sha256()
    for file in _page_files(root / "pages" / table):
        expected.update(file.read_bytes().rstrip(b"\r\n") + b"\n")
    target = root / f"{table}.jsonl"
    if not target.is_file() or _hash_file(target) != expected.hexdigest():
        raise SnapshotError(f"{table} materialization differs from saved pages")


def _manifest(root: Path, spec: PullSpec, revision_of: str | None) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "source": "langfuse",
        "observations_api": "v2",
        "scores_api": "v3",
        "observation_fields": list(spec.observation_fields),
        "score_fields": list(spec.score_fields),
        "query": {"fromStartTime": spec.from_time, "toStartTime": spec.to_time},
        "score_query": {
            "fromTimestamp": _score_bounds(spec)[0],
            "toTimestamp": _score_bounds(spec)[1],
        },
        "complete": True,
        "collection": {
            "schema_version": PULL_SCHEMA,
            "collected_at": datetime.now(UTC).isoformat(),
            "completeness": "cursor_exhausted_at_collection_time",
            "project_id": spec.project_id,
            "trace_id": spec.trace_id,
            "revision_of_snapshot_id": revision_of,
            "pages": {table: len(_page_files(root / "pages" / table)) for table in TABLES},
        },
    }


def _validate_materialized(root: Path, manifest_bytes: bytes) -> dict[str, Any]:
    temporary = Path(tempfile.mkdtemp(prefix=".trailgate-validate-", dir=root))
    try:
        os.chmod(temporary, 0o700)
        for table in TABLES:
            os.link(root / f"{table}.jsonl", temporary / f"{table}.jsonl")
        _private_bytes(temporary / "manifest.json", manifest_bytes)
        return read_snapshot(temporary)
    finally:
        shutil.rmtree(temporary)


def _previous_snapshot_id(path: Path, spec: PullSpec) -> str:
    index = _read_json(path / "index.json")
    _validate_index(path, index)
    if index.get("source_schema") != SNAPSHOT_SCHEMA:
        raise SnapshotError("previous archive must be a Langfuse API snapshot")

    previous = _read_json(path / "raw" / "manifest.json")
    collection = previous.get("collection")
    if not isinstance(collection, dict) or collection.get("schema_version") != PULL_SCHEMA:
        raise SnapshotError("previous archive has no verified pull scope")
    matching = (
        collection.get("project_id") == spec.project_id
        and collection.get("trace_id") == spec.trace_id
        and previous.get("query") == {"fromStartTime": spec.from_time, "toStartTime": spec.to_time}
        and previous.get("observation_fields") == list(spec.observation_fields)
        and previous.get("score_fields") == list(spec.score_fields)
    )
    prior_scores = previous.get("score_query")
    if not isinstance(prior_scores, dict):
        raise SnapshotError("previous pull has invalid score scope")
    score_from, score_to = _score_bounds(spec)
    matching = matching and (
        prior_scores.get("fromTimestamp") == score_from
        and isinstance(prior_scores.get("toTimestamp"), str)
        and _parse_bound(prior_scores["toTimestamp"]) <= _parse_bound(score_to)
    )
    if not matching:
        raise SnapshotError("previous pull scope differs from requested revision")
    return index["snapshot_id"]


def pull_snapshot(
    spec: PullSpec,
    destination: str | Path,
    credentials: tuple[str, str],
    *,
    resume: bool = False,
    previous_archive: str | Path | None = None,
) -> dict[str, Any]:
    """Collect a new bounded snapshot; resume only the same incomplete scope."""
    _validate_spec(spec)
    if not all(isinstance(value, str) and value for value in credentials):
        raise SnapshotError("Langfuse public and secret keys are required")
    destination = Path(destination)
    if not destination.parent.is_dir():
        raise SnapshotError("pull destination parent does not exist")
    revision_of = (
        _previous_snapshot_id(Path(previous_archive), spec)
        if previous_archive is not None
        else None
    )
    saved_spec = asdict(spec)
    if spec.score_from_time is None:
        saved_spec.pop("score_from_time")
    if spec.score_to_time is None:
        saved_spec.pop("score_to_time")
    spec_record = {
        "schema_version": PULL_SCHEMA,
        "spec": saved_spec,
        "revision_of_snapshot_id": revision_of,
    }
    spec_bytes = (json.dumps(spec_record, sort_keys=True) + "\n").encode()
    if resume:
        if not destination.is_dir() or (destination / "manifest.json").exists():
            raise SnapshotError("resume requires an existing incomplete pull directory")
        if (destination / "pull.json").read_bytes() != spec_bytes:
            raise SnapshotError("pull scope differs from checkpoint")
    else:
        destination.mkdir(mode=0o700)
        _private_bytes(destination / "pull.json", spec_bytes)

    for table in TABLES:
        _collect_table(spec, destination, table, credentials)
    for table in TABLES:
        if (destination / f"{table}.jsonl").exists():
            _verify_materialized(destination, table)
        else:
            _materialize_pages(destination, table)

    manifest = _manifest(destination, spec, revision_of)
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    parsed = _validate_materialized(destination, manifest_bytes)
    _atomic_private_bytes(destination / "manifest.json", manifest_bytes)
    return {
        "snapshot_id": hashlib.sha256(
            json.dumps(parsed["hashes"], sort_keys=True).encode()
        ).hexdigest(),
        "observations": len(parsed["observations"]),
        "scores": len(parsed["scores"]),
        "completeness": manifest["collection"]["completeness"],
        "revision_of_snapshot_id": revision_of,
    }
