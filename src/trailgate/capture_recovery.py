"""Copy complete capture events after an interrupted append."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .capture import MAX_CAPTURE_EVENT_BYTES
from .snapshot_input import SnapshotError, _parse_json


def recover_capture(source: str | Path, destination: str | Path) -> dict[str, int | str | None]:
    """Keep complete JSONL lines; report but never modify an incomplete final line."""
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise SnapshotError(f"capture file does not exist: {source}")
    if stat.S_IMODE(source.stat().st_mode) & 0o077:
        raise PermissionError(f"capture file must be private (0600): {source}")
    if not destination.parent.is_dir():
        raise SnapshotError(f"destination parent does not exist: {destination.parent}")

    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    recovered = 0
    tail_hash = None
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
            line_no = 0
            while line := stream.readline(MAX_CAPTURE_EVENT_BYTES + 1):
                line_no += 1
                if len(line) > MAX_CAPTURE_EVENT_BYTES:
                    raise SnapshotError("capture event exceeds the 8 MiB limit")
                if not line.endswith(b"\n"):
                    tail_hash = hashlib.sha256(line).hexdigest()
                    break
                try:
                    event = _parse_json(line.decode("utf-8"), f"{source}:{line_no}")
                except UnicodeError as exc:
                    raise SnapshotError(f"{source}:{line_no}: invalid UTF-8") from exc
                if not isinstance(event, dict):
                    raise SnapshotError(f"{source}:{line_no}: expected an event object")
                output.write(line)
                recovered += 1
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {"recovered_events": recovered, "discarded_tail_sha256": tail_hash}
