"""Decision-level trainability audit and conservative SFT projection."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .snapshot import SnapshotError, _jsonl, _private_jsonl, _read_json, read_archive

RIGHTS_SCHEMA = "trailgate.use_manifest.v1"
USES = {"internal_training", "external_training"}


def _capture_events(archive: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    path = archive["path"] / "raw" / "capture.jsonl"
    if not path.exists():
        return {}
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line_no, event in _jsonl(path):
        capture_id = event.get("capture_id")
        if not isinstance(capture_id, str) or not capture_id:
            raise SnapshotError(f"{path}:{line_no}: capture_id is required")
        if event.get("kind") not in {"begin", "finish", "delivery"}:
            raise SnapshotError(f"{path}:{line_no}: unknown capture event kind")
        events[capture_id].append(event)
    return events


def audit(path: str | Path) -> dict[str, Any]:
    archive = read_archive(Path(path))
    captures = _capture_events(archive)
    observations = archive["observations"]
    obs_by_id = {row["id"]: row for row in observations}
    linked: Counter[str] = Counter()
    for row in observations:
        if row.get("type") == "GENERATION":
            metadata = row.get("metadata")
            if isinstance(metadata, dict):
                capture_id = metadata.get("trailgate_capture_id")
                if isinstance(capture_id, str) and capture_id:
                    linked[capture_id] += 1

    decisions: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for row in observations:
        if row.get("type") != "GENERATION":
            continue
        reasons: list[str] = []
        if row.get("end_time") is None:
            reasons.append("incomplete_generation")
        metadata = row.get("metadata")
        capture_id = metadata.get("trailgate_capture_id") if isinstance(metadata, dict) else None
        begin = finish = None
        deliveries: list[dict[str, Any]] = []
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
                provider_format = begin.get("provider_format", "chat_completions")
                if provider_format not in {"chat_completions", "openai_responses"}:
                    reasons.append("unsupported_provider_format")
                if provider_format == "openai_responses" and not isinstance(
                    begin.get("request_parameters"), dict
                ):
                    reasons.append("missing_provider_request_parameters")
                if not isinstance(begin.get("messages"), list):
                    reasons.append("unknown_model_visible_messages")
                if not isinstance(begin.get("tools"), list):
                    reasons.append("unknown_tool_schema")
            if len(finishes) != 1:
                reasons.append("missing_or_duplicate_model_output")
            else:
                finish = finishes[0]
                if (
                    begin is not None
                    and begin.get("provider_format") == "openai_responses"
                    and not isinstance(finish.get("provider_response"), dict)
                ):
                    reasons.append("missing_raw_provider_response")
                assistant = finish.get("assistant_message")
                if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
                    reasons.append("invalid_model_output")
                else:
                    tool_calls = assistant.get("tool_calls", [])
                    if not isinstance(tool_calls, list):
                        reasons.append("invalid_tool_calls")
                    else:
                        call_ids = []
                        declared_tools: set[str] = set()
                        if begin is not None and isinstance(begin.get("tools"), list):
                            for tool in begin["tools"]:
                                if not isinstance(tool, dict):
                                    reasons.append("invalid_tool_schema")
                                    continue
                                function = tool.get("function", tool)
                                if not isinstance(function, dict) or not isinstance(
                                    function.get("name"), str
                                ):
                                    reasons.append("invalid_tool_schema")
                                    continue
                                declared_tools.add(function["name"])
                        for call in tool_calls:
                            if (
                                not isinstance(call, dict)
                                or not isinstance(call.get("id"), str)
                                or not call["id"]
                            ):
                                reasons.append("invalid_tool_calls")
                                break
                            call_ids.append(call["id"])
                            function = call.get("function")
                            if (
                                not isinstance(function, dict)
                                or function.get("name") not in declared_tools
                            ):
                                reasons.append("undeclared_tool_call")
                        if len(set(call_ids)) != len(call_ids):
                            reasons.append("duplicate_tool_call_id")
                        for call_id in call_ids:
                            matched = [
                                event for event in deliveries if event.get("call_id") == call_id
                            ]
                            if len(matched) != 1:
                                reasons.append("unmatched_tool_result")
                                continue
                            delivery = matched[0]
                            tool_observation = obs_by_id.get(delivery.get("observation_id"))
                            if (
                                "value" not in delivery
                                or tool_observation is None
                                or tool_observation.get("type") != "TOOL"
                                or tool_observation.get("trace_id") != row["trace_id"]
                            ):
                                reasons.append("unverified_tool_delivery")
                        if any(event.get("call_id") not in call_ids for event in deliveries):
                            reasons.append("unexpected_tool_delivery")
            if begin is not None and finish is not None:
                if events.index(begin) >= events.index(finish):
                    reasons.append("invalid_capture_order")
                if any(events.index(event) <= events.index(finish) for event in deliveries):
                    reasons.append("invalid_capture_order")
        reasons = list(dict.fromkeys(reasons))
        reason_counts.update(reasons)
        decisions.append(
            {
                "observation_id": row["id"],
                "trace_id": row["trace_id"],
                "capture_id": capture_id,
                "candidate": not reasons,
                "provider_format": begin.get("provider_format", "chat_completions")
                if begin
                else None,
                "sft_projection_supported": bool(
                    begin and begin.get("provider_format", "chat_completions") == "chat_completions"
                ),
                "reasons": reasons,
                "source_ref": row["source_ref"],
                # Internal fields are used by export and omitted from report JSON.
                "_begin": begin,
                "_finish": finish,
            }
        )
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


def _check_rights(path: Path, use: str) -> dict[str, Any]:
    if use not in USES:
        raise SnapshotError(f"use must be one of {sorted(USES)}")
    rights = _read_json(path)
    if rights.get("schema_version") != RIGHTS_SCHEMA:
        raise SnapshotError(f"rights schema_version must be {RIGHTS_SCHEMA}")
    if not isinstance(rights.get("attested_by"), str) or not rights["attested_by"]:
        raise SnapshotError("rights attested_by is required")
    if not isinstance(rights.get("confirmed_at"), str) or not rights["confirmed_at"]:
        raise SnapshotError("rights confirmed_at is required")
    permissions = rights.get("permissions")
    if not isinstance(permissions, dict):
        raise SnapshotError("rights permissions object is required")
    for component in ("task_input", "model_output", "tool_result"):
        allowed = permissions.get(component)
        if not isinstance(allowed, list) or use not in allowed:
            raise SnapshotError(f"rights do not allow {use} for {component}")
    return rights


def export_sft(archive_path: Path, rights_path: Path, use: str, output: Path) -> int:
    """Export only capture-backed decisions with explicit use attestation."""
    rights_path = Path(rights_path)
    rights = _check_rights(rights_path, use)
    rights_hash = hashlib.sha256(rights_path.read_bytes()).hexdigest()
    report = audit(archive_path)
    output = Path(output)
    if output.exists():
        raise SnapshotError(f"output already exists: {output}")
    if not output.parent.is_dir():
        raise SnapshotError(f"output parent does not exist: {output.parent}")
    rows = []
    for decision in report["decisions"]:
        if not decision["candidate"] or not decision["sft_projection_supported"]:
            continue
        begin, finish = decision["_begin"], decision["_finish"]
        rows.append(
            {
                "messages": [*begin["messages"], finish["assistant_message"]],
                "tools": begin["tools"],
                "source": {
                    "snapshot_id": report["snapshot_id"],
                    "trace_id": decision["trace_id"],
                    "observation_id": decision["observation_id"],
                    "capture_id": decision["capture_id"],
                },
                "target": "last_assistant_message",
                "use": use,
                "rights_ref": {
                    "sha256": rights_hash,
                    "attested_by": rights["attested_by"],
                    "confirmed_at": rights["confirmed_at"],
                },
            }
        )
    _private_jsonl(output, rows)
    return len(rows)


def export_steps(archive_path: Path, output: Path) -> int:
    """Export diagnostic decision records without model content."""
    report = public_report(archive_path)
    output = Path(output)
    if output.exists():
        raise SnapshotError(f"output already exists: {output}")
    if not output.parent.is_dir():
        raise SnapshotError(f"output parent does not exist: {output.parent}")
    _private_jsonl(output, report["decisions"])
    return len(report["decisions"])
