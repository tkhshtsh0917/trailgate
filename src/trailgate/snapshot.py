"""Compatibility imports for the original snapshot and archive entry points."""

from .archive import ARCHIVE_SCHEMA, ingest_snapshot, read_archive
from .snapshot_input import (
    SNAPSHOT_SCHEMA,
    SnapshotError,
    read_snapshot,
)

__all__ = [
    "ARCHIVE_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "SnapshotError",
    "ingest_snapshot",
    "read_archive",
    "read_snapshot",
]
