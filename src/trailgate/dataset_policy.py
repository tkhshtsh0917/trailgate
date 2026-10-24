"""Validate explicit selection, privacy, and split declarations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .snapshot_input import SnapshotError, _parse_json

POLICY_SCHEMA = "trailgate.dataset_policy.v1"
SPLITS = {"train", "validation", "test"}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def read_dataset_policy(path: Path, snapshot_id: str, use: str) -> tuple[dict[str, Any], str]:
    """Read once so the validated declaration and its hash describe the same bytes."""
    try:
        contents = Path(path).read_bytes()
        policy = _parse_json(contents.decode("utf-8"), str(path))
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"cannot read dataset policy: {path}") from exc
    if not isinstance(policy, dict) or policy.get("schema_version") != POLICY_SCHEMA:
        raise SnapshotError(f"dataset policy schema_version must be {POLICY_SCHEMA}")
    if policy.get("snapshot_id") != snapshot_id or policy.get("use") != use:
        raise SnapshotError("dataset policy snapshot_id or use differs from export")

    quality = policy.get("quality")
    if not isinstance(quality, dict) or quality.get("method") != "manual_observation_review":
        raise SnapshotError("dataset quality policy must declare manual_observation_review")
    if not _nonempty(quality.get("reviewed_by")):
        raise SnapshotError("dataset quality reviewed_by is required")
    approved = quality.get("approved_observation_ids")
    if not isinstance(approved, list) or not all(_nonempty(item) for item in approved):
        raise SnapshotError("quality approved_observation_ids must be a string list")
    if len(set(approved)) != len(approved):
        raise SnapshotError("quality approval IDs must be unique")

    privacy = policy.get("privacy")
    if not isinstance(privacy, dict) or privacy.get("method") != "manual_review":
        raise SnapshotError("privacy manual_review is required")
    if not _nonempty(privacy.get("reviewed_by")) or not _nonempty(privacy.get("confirmed_at")):
        raise SnapshotError("privacy review identity and date are required")
    if privacy.get("sensitive_data") != "absent" or privacy.get("redaction") != "none":
        raise SnapshotError(
            "training export requires reviewed absent sensitive data and redaction=none"
        )

    split_entries = policy.get("splits")
    if not isinstance(split_entries, list):
        raise SnapshotError("dataset splits must be a list")
    by_trace = {}
    by_task = {}
    for item in split_entries:
        if not isinstance(item, dict):
            raise SnapshotError("dataset split entry must be an object")
        trace_id, task_id, split = item.get("trace_id"), item.get("task_id"), item.get("split")
        if (
            not _nonempty(trace_id)
            or not _nonempty(task_id)
            or not isinstance(split, str)
            or split not in SPLITS
        ):
            raise SnapshotError("dataset split entry is invalid")
        if trace_id in by_trace:
            raise SnapshotError("trace has more than one split declaration")
        if task_id in by_task and by_task[task_id] != split:
            raise SnapshotError("task appears in multiple dataset splits")
        by_trace[trace_id] = {"task_id": task_id, "split": split}
        by_task[task_id] = split
    return policy, hashlib.sha256(contents).hexdigest()
