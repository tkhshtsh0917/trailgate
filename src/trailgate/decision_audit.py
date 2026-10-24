"""Audit capture fidelity for each Langfuse generation."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .archive import read_archive
from .chat_shape import valid_message, valid_tool_call, valid_tool_schema
from .snapshot_input import SnapshotError, _jsonl


def _capture_events(archive: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    path = archive["path"] / "raw" / "capture.jsonl"
    if not path.exists():
        return {}
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line_no, event in _jsonl(path):
        capture_id = event.get("capture_id")
        if not isinstance(capture_id, str) or not capture_id:
            raise SnapshotError(f"{path}:{line_no}: capture_id is required")
        kind = event.get("kind")
        if not isinstance(kind, str) or kind not in {"begin", "finish", "delivery"}:
            raise SnapshotError(f"{path}:{line_no}: unknown capture event kind")
        events[capture_id].append(event)
    return events


def _capture_links(observations: list[dict[str, Any]]) -> Counter[str]:
    linked: Counter[str] = Counter()
    for row in observations:
        if row.get("type") != "GENERATION":
            continue
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            capture_id = metadata.get("trailgate_capture_id")
            if isinstance(capture_id, str) and capture_id:
                linked[capture_id] += 1
    return linked


def _validate_begin(begin: dict[str, Any], reasons: list[str]) -> None:
    provider_format = begin.get("provider_format", "chat_completions")
    if not isinstance(provider_format, str) or provider_format not in {
        "chat_completions",
        "openai_responses",
    }:
        reasons.append("unsupported_provider_format")
    if provider_format == "openai_responses" and not isinstance(
        begin.get("request_parameters"), dict
    ):
        reasons.append("missing_provider_request_parameters")
    messages = begin.get("messages")
    if not isinstance(messages, list):
        reasons.append("unknown_model_visible_messages")
    elif provider_format == "chat_completions" and (
        not messages or not all(valid_message(message) for message in messages)
    ):
        reasons.append("invalid_chat_messages")
    if not isinstance(begin.get("tools"), list):
        reasons.append("unknown_tool_schema")


def _declared_tools(begin: dict[str, Any] | None, reasons: list[str]) -> set[str]:
    names: set[str] = set()
    if begin is None or not isinstance(begin.get("tools"), list):
        return names

    provider_format = begin.get("provider_format", "chat_completions")
    duplicate = False
    for tool in begin["tools"]:
        if provider_format == "chat_completions" and not valid_tool_schema(tool):
            reasons.append("invalid_tool_schema")
            continue
        if not isinstance(tool, dict):
            reasons.append("invalid_tool_schema")
            continue
        function = tool.get("function", tool)
        if (
            not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
            or not function["name"]
        ):
            reasons.append("invalid_tool_schema")
            continue
        if function["name"] in names:
            duplicate = True
        names.add(function["name"])
    if duplicate:
        reasons.append("duplicate_tool_schema_name")
    return names


def _validate_delivery(
    call_id: str,
    deliveries: list[dict[str, Any]],
    observations_by_id: dict[str, dict[str, Any]],
    trace_id: str,
    reasons: list[str],
) -> None:
    matched = [event for event in deliveries if event.get("call_id") == call_id]
    if len(matched) != 1:
        reasons.append("unmatched_tool_result")
        return

    delivery = matched[0]
    observation_id = delivery.get("observation_id")
    tool_observation = (
        observations_by_id.get(observation_id) if isinstance(observation_id, str) else None
    )
    if (
        "value" not in delivery
        or tool_observation is None
        or tool_observation.get("type") != "TOOL"
        or tool_observation.get("trace_id") != trace_id
    ):
        reasons.append("unverified_tool_delivery")


def _validate_tool_calls(
    assistant: dict[str, Any],
    begin: dict[str, Any] | None,
    deliveries: list[dict[str, Any]],
    observations_by_id: dict[str, dict[str, Any]],
    trace_id: str,
    reasons: list[str],
) -> None:
    tool_calls = assistant.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        reasons.append("invalid_tool_calls")
        return

    call_ids = []
    declared_tools = _declared_tools(begin, reasons)
    for call in tool_calls:
        if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
            reasons.append("invalid_tool_calls")
            break
        if (
            begin is not None
            and begin.get("provider_format", "chat_completions") == "chat_completions"
        ):
            if not valid_tool_call(call):
                reasons.append("invalid_tool_calls")
                continue
        call_ids.append(call["id"])
        function = call.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or name not in declared_tools:
            reasons.append("undeclared_tool_call")

    if len(set(call_ids)) != len(call_ids):
        reasons.append("duplicate_tool_call_id")
    for call_id in call_ids:
        _validate_delivery(call_id, deliveries, observations_by_id, trace_id, reasons)
    if any(event.get("call_id") not in call_ids for event in deliveries):
        reasons.append("unexpected_tool_delivery")


def _validate_finish(
    finish: dict[str, Any],
    begin: dict[str, Any] | None,
    deliveries: list[dict[str, Any]],
    observations_by_id: dict[str, dict[str, Any]],
    trace_id: str,
    reasons: list[str],
) -> None:
    if (
        begin is not None
        and begin.get("provider_format") == "openai_responses"
        and not isinstance(finish.get("provider_response"), dict)
    ):
        reasons.append("missing_raw_provider_response")

    assistant = finish.get("assistant_message")
    if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
        reasons.append("invalid_model_output")
        return
    if (
        begin is not None
        and begin.get("provider_format", "chat_completions") == "chat_completions"
        and not valid_message(assistant, assistant_only=True)
    ):
        reasons.append("invalid_chat_assistant_message")
    _validate_tool_calls(assistant, begin, deliveries, observations_by_id, trace_id, reasons)


def _decision(
    row: dict[str, Any],
    captures: dict[str, list[dict[str, Any]]],
    linked: Counter[str],
    observations_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[str] = []
    if row.get("end_time") is None:
        reasons.append("incomplete_generation")

    metadata = row.get("metadata")
    capture_id = metadata.get("trailgate_capture_id") if isinstance(metadata, dict) else None
    begin = finish = None
    if not isinstance(capture_id, str) or not capture_id:
        reasons.append("missing_capture_link")
    else:
        if linked[capture_id] != 1:
            reasons.append("ambiguous_capture_link")
        events = captures.get(capture_id, [])
        begins = [event for event in events if event["kind"] == "begin"]
        finishes = [event for event in events if event["kind"] == "finish"]
        deliveries = [event for event in events if event["kind"] == "delivery"]

        if len(begins) != 1:
            reasons.append("missing_or_duplicate_model_visible_input")
        else:
            begin = begins[0]
            _validate_begin(begin, reasons)
        if len(finishes) != 1:
            reasons.append("missing_or_duplicate_model_output")
        else:
            finish = finishes[0]
            _validate_finish(
                finish, begin, deliveries, observations_by_id, row["trace_id"], reasons
            )

        if begin is not None and finish is not None:
            if events.index(begin) >= events.index(finish):
                reasons.append("invalid_capture_order")
            if any(events.index(event) <= events.index(finish) for event in deliveries):
                reasons.append("invalid_capture_order")

    reasons = list(dict.fromkeys(reasons))
    return {
        "observation_id": row["id"],
        "trace_id": row["trace_id"],
        "capture_id": capture_id,
        "candidate": not reasons,
        "provider_format": begin.get("provider_format", "chat_completions") if begin else None,
        "sft_projection_supported": bool(
            begin and begin.get("provider_format", "chat_completions") == "chat_completions"
        ),
        "reasons": reasons,
        "source_ref": row["source_ref"],
        # Internal fields are used by export and omitted from report JSON.
        "_begin": begin,
        "_finish": finish,
    }


def audit(path: str | Path) -> dict[str, Any]:
    archive = read_archive(Path(path))
    observations = archive["observations"]
    captures = _capture_events(archive)
    linked = _capture_links(observations)
    observations_by_id = {row["id"]: row for row in observations}

    decisions = [
        _decision(row, captures, linked, observations_by_id)
        for row in observations
        if row.get("type") == "GENERATION"
    ]
    reason_counts: Counter[str] = Counter()
    for decision in decisions:
        reason_counts.update(decision["reasons"])

    return {
        "snapshot_id": archive["index"]["snapshot_id"],
        "traces": len({row["trace_id"] for row in observations}),
        "observations": len(observations),
        "scores": len(archive["scores"]),
        "generations": len(decisions),
        "faithful_candidates": sum(item["candidate"] for item in decisions),
        "rights_checked": False,
        "unknown_observation_fields": archive["index"]["unknown_observation_fields"],
        "unknown_score_fields": archive["index"]["unknown_score_fields"],
        "reason_counts": dict(sorted(reason_counts.items())),
        "decisions": decisions,
    }


def public_report(path: str | Path) -> dict[str, Any]:
    report = audit(path)
    report["decisions"] = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in report["decisions"]
    ]
    return report
