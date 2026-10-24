"""Opt-in local recording of the state actually supplied to a model.

The helper never calls a model or sends captured content to Langfuse. The
application attaches the returned ID to its Langfuse generation metadata.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - exercised on non-POSIX hosts
    fcntl = None

CAPTURE_SCHEMA = "trailgate.capture.v1"
MAX_CAPTURE_EVENT_BYTES = 8 * 1024 * 1024


class CaptureWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.parent.is_dir():
            raise ValueError(f"capture parent does not exist: {self.path.parent}")
        if self.path.exists() and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
            raise PermissionError(f"capture file must be private (0600): {self.path}")

    def _append(self, event: dict[str, Any]) -> None:
        if fcntl is None:
            raise OSError("capture writing requires POSIX file locking")
        event["schema_version"] = CAPTURE_SCHEMA
        event["recorded_at"] = datetime.now(UTC).isoformat()
        data = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode()
        if len(data) > MAX_CAPTURE_EVENT_BYTES:
            raise ValueError("capture event exceeds the 8 MiB limit")

        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(self.path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
                raise PermissionError(f"capture file must be private (0600): {self.path}")

            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError("capture append did not advance")
                offset += written
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def begin_decision(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        provider_format: str = "chat_completions",
        request_parameters: dict[str, Any] | None = None,
    ) -> str:
        """Call immediately before inference; [] is a known empty toolset."""
        if not isinstance(messages, list) or not isinstance(tools, list):
            raise TypeError("messages and tools must be lists")
        if provider_format not in {"chat_completions", "openai_responses"}:
            raise ValueError("unsupported provider_format")
        if request_parameters is not None and not isinstance(request_parameters, dict):
            raise TypeError("request_parameters must be an object")

        capture_id = uuid.uuid4().hex
        event = {
            "kind": "begin",
            "capture_id": capture_id,
            "messages": messages,
            "tools": tools,
            "provider_format": provider_format,
        }
        if request_parameters is not None:
            event["request_parameters"] = request_parameters
        self._append(event)
        return capture_id

    def finish_decision(
        self,
        capture_id: str,
        *,
        assistant_message: dict[str, Any],
        provider_response: dict[str, Any] | None = None,
    ) -> None:
        if not capture_id:
            raise ValueError("capture_id is required")
        if not isinstance(assistant_message, dict) or assistant_message.get("role") != "assistant":
            raise ValueError("assistant_message must be a role=assistant object")
        if provider_response is not None and not isinstance(provider_response, dict):
            raise TypeError("provider_response must be an object")

        event = {"kind": "finish", "capture_id": capture_id, "assistant_message": assistant_message}
        if provider_response is not None:
            event["provider_response"] = provider_response
        self._append(event)

    def abort_decision(self, capture_id: str, *, reason_code: str) -> None:
        """Mark an interrupted inference without fabricating an assistant response."""
        if not capture_id or not isinstance(reason_code, str) or not reason_code:
            raise ValueError("capture_id and reason_code are required")
        self._append({"kind": "abort", "capture_id": capture_id, "reason_code": reason_code})

    def delivered_tool_result(
        self,
        capture_id: str,
        *,
        call_id: str,
        observation_id: str,
        value: Any,
        truncated: bool = False,
    ) -> None:
        """Record the actual result handed to the model, after host processing."""
        if not capture_id or not call_id or not observation_id or not isinstance(truncated, bool):
            raise ValueError("call_id, observation_id and boolean truncated are required")

        self._append(
            {
                "kind": "delivery",
                "capture_id": capture_id,
                "call_id": call_id,
                "observation_id": observation_id,
                "value": value,
                "truncated": truncated,
            }
        )
