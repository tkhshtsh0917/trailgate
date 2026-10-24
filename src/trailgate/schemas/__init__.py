"""Load the bundled, versioned JSON Schema contracts."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

SCHEMA_FILES = (
    "api-snapshot-manifest-v1.json",
    "archive-index-v1-v2.json",
    "capture-event-v1.json",
    "chat-sft-v1.json",
    "dataset-manifest-v1.json",
    "dataset-policy-v1.json",
    "episode-graph-v1.json",
    "episode-row-v1.json",
    "use-manifest-v1.json",
)


def load_schema(name: str) -> dict[str, Any]:
    """Load a published schema by exact filename without accepting a path."""
    if name not in SCHEMA_FILES:
        raise ValueError(f"unknown Trailgate schema: {name}")
    return json.loads(files(__name__).joinpath(name).read_text(encoding="utf-8"))
