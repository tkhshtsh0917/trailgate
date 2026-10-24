"""Write private local files without replacing an existing path."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _private_bytes(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def _private_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    data = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode()
    _private_bytes(path, data)
