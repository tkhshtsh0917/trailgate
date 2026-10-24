"""Validate the text and function-call Chat subset supported by SFT export."""

from __future__ import annotations

from typing import Any


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def valid_tool_call(call: Any) -> bool:
    if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
        return False
    function = call.get("function")
    return bool(
        _nonempty_string(call.get("id"))
        and call.get("type") == "function"
        and isinstance(function, dict)
        and set(function) == {"name", "arguments"}
        and _nonempty_string(function.get("name"))
        and isinstance(function.get("arguments"), str)
    )


def valid_message(message: Any, *, assistant_only: bool = False) -> bool:
    if not isinstance(message, dict):
        return False

    role = message.get("role")
    allowed_roles = (
        {"assistant"} if assistant_only else {"system", "developer", "user", "assistant", "tool"}
    )
    if not isinstance(role, str) or role not in allowed_roles:
        return False

    allowed_keys = {"role", "content", "name", "tool_call_id", "tool_calls"}
    if set(message) - allowed_keys:
        return False
    if "name" in message and not _nonempty_string(message["name"]):
        return False

    if role == "tool":
        return bool(
            set(message) <= {"role", "content", "tool_call_id"}
            and _nonempty_string(message.get("tool_call_id"))
            and isinstance(message.get("content"), str)
        )

    if "tool_call_id" in message:
        return False
    if role != "assistant":
        return "tool_calls" not in message and isinstance(message.get("content"), str)

    calls = message.get("tool_calls", [])
    if not isinstance(calls, list) or not all(valid_tool_call(call) for call in calls):
        return False
    if len({call["id"] for call in calls}) != len(calls):
        return False

    content = message.get("content")
    return isinstance(content, str) or (content is None and bool(calls))


def valid_tool_schema(tool: Any) -> bool:
    if not isinstance(tool, dict) or set(tool) != {"type", "function"}:
        return False
    function = tool.get("function")
    if tool.get("type") != "function" or not isinstance(function, dict):
        return False
    if set(function) - {"name", "description", "parameters", "strict"}:
        return False
    if not _nonempty_string(function.get("name")):
        return False
    if "description" in function and not isinstance(function["description"], str):
        return False
    if "strict" in function and not isinstance(function["strict"], bool):
        return False

    parameters = function.get("parameters")
    if parameters is None:
        return "parameters" not in function and function.get("strict") is not True
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        return False
    if "properties" in parameters and not isinstance(parameters["properties"], dict):
        return False
    required = parameters.get("required", [])
    return (
        isinstance(required, list)
        and all(_nonempty_string(name) for name in required)
        and len(set(required)) == len(required)
    )
