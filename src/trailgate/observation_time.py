"""Validate observation timestamps before claiming a completed decision."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .errors import SnapshotError


def _parse_time(value: Any, label: str, *, require_timezone: bool) -> datetime:
    if (
        not isinstance(value, str)
        or not value.strip()
        or not any(separator in value for separator in ("T", " "))
    ):
        raise SnapshotError(f"{label}: expected a nonempty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError(f"{label}: invalid timestamp") from exc
    if require_timezone and parsed.tzinfo is None:
        raise SnapshotError(f"{label}: timezone is required")
    return parsed


def validate_observation_times(
    start: Any, end: Any, label: str, *, require_timezone: bool
) -> datetime:
    """Allow an open observation, but validate any reported completion time."""
    started = _parse_time(start, f"{label} start", require_timezone=require_timezone)
    if end is None:
        return started

    ended = _parse_time(end, f"{label} end", require_timezone=require_timezone)
    if (started.tzinfo is None) != (ended.tzinfo is None) or ended < started:
        raise SnapshotError(f"{label}: end precedes start or has incompatible timezone")
    return started
