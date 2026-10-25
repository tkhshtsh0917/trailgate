"""Keep arbitrary source field names out of normal diagnostics."""

from __future__ import annotations

from typing import Any

FIELD_COUNTS = {
    "unknown_observation_fields": "unknown_observation_field_count",
    "unknown_score_fields": "unknown_score_field_count",
}


def summarize_unknown_fields(result: dict[str, Any]) -> dict[str, Any]:
    """Replace untrusted field-name lists with counts without changing the source."""
    summary = result.copy()
    for field, count_field in FIELD_COUNTS.items():
        if field in summary:
            summary[count_field] = len(summary.pop(field))
    return summary
