"""Export diagnostic records and rights-attested Chat SFT examples."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .decision_audit import audit, public_report
from .private_io import _private_jsonl
from .snapshot_input import SnapshotError, _parse_json

RIGHTS_SCHEMA = "trailgate.use_manifest.v1"
USES = {"internal_training", "external_training"}


def _check_rights(path: Path, use: str, snapshot_id: str) -> tuple[dict[str, Any], str]:
    if use not in USES:
        raise SnapshotError(f"use must be one of {sorted(USES)}")
    try:
        contents = path.read_bytes()
        rights = _parse_json(contents.decode("utf-8"), str(path))
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"cannot read {path}: {exc}") from exc
    if not isinstance(rights, dict):
        raise SnapshotError(f"{path}: expected a JSON object")
    if rights.get("schema_version") != RIGHTS_SCHEMA:
        raise SnapshotError(f"rights schema_version must be {RIGHTS_SCHEMA}")
    attested_snapshot_id = rights.get("snapshot_id")
    if not isinstance(attested_snapshot_id, str) or not re.fullmatch(
        r"[0-9a-f]{64}", attested_snapshot_id
    ):
        raise SnapshotError("rights snapshot_id must be a SHA-256 hex digest")
    if attested_snapshot_id != snapshot_id:
        raise SnapshotError("rights snapshot_id does not match archive")
    if not isinstance(rights.get("attested_by"), str) or not rights["attested_by"]:
        raise SnapshotError("rights attested_by is required")
    if not isinstance(rights.get("confirmed_at"), str) or not rights["confirmed_at"]:
        raise SnapshotError("rights confirmed_at is required")
    permissions = rights.get("permissions")
    if not isinstance(permissions, dict):
        raise SnapshotError("rights permissions object is required")
    for component in ("task_input", "model_output", "tool_result"):
        allowed = permissions.get(component)
        if not isinstance(allowed, list) or use not in allowed:
            raise SnapshotError(f"rights do not allow {use} for {component}")
    return rights, hashlib.sha256(contents).hexdigest()


def export_sft(archive_path: Path, rights_path: Path, use: str, output: Path) -> int:
    """Export only capture-backed decisions with explicit use attestation."""
    rights_path = Path(rights_path)
    report = audit(archive_path)
    rights, rights_hash = _check_rights(rights_path, use, report["snapshot_id"])
    output = Path(output)
    if output.exists():
        raise SnapshotError(f"output already exists: {output}")
    if not output.parent.is_dir():
        raise SnapshotError(f"output parent does not exist: {output.parent}")
    rows = []
    for decision in report["decisions"]:
        if not decision["candidate"] or not decision["sft_projection_supported"]:
            continue
        begin, finish = decision["_begin"], decision["_finish"]
        rows.append(
            {
                "messages": [*begin["messages"], finish["assistant_message"]],
                "tools": begin["tools"],
                "source": {
                    "snapshot_id": report["snapshot_id"],
                    "trace_id": decision["trace_id"],
                    "observation_id": decision["observation_id"],
                    "capture_id": decision["capture_id"],
                },
                "target": "last_assistant_message",
                "use": use,
                "rights_ref": {
                    "sha256": rights_hash,
                    "snapshot_id": report["snapshot_id"],
                    "attested_by": rights["attested_by"],
                    "confirmed_at": rights["confirmed_at"],
                },
            }
        )
    _private_jsonl(output, rows)
    return len(rows)


def export_steps(archive_path: Path, output: Path) -> int:
    """Export diagnostic decision records without model content."""
    report = public_report(archive_path)
    output = Path(output)
    if output.exists():
        raise SnapshotError(f"output already exists: {output}")
    if not output.parent.is_dir():
        raise SnapshotError(f"output parent does not exist: {output.parent}")
    _private_jsonl(output, report["decisions"])
    return len(report["decisions"])
