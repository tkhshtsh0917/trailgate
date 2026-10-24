"""Build a deterministic graph of observed decisions and tool deliveries."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from .archive import read_archive
from .decision_audit import audit
from .snapshot_input import _jsonl

GRAPH_SCHEMA = "trailgate.episode_graph.v1"


def _node_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join((kind, *parts)).encode()).hexdigest()[:24]
    return f"tg:v1:{kind}:{digest}"


def _scope_by_trace(
    observations: list[dict[str, Any]],
) -> dict[tuple[str, str], tuple[str, str, str]]:
    grouped: dict[tuple[str, str], set[str | None]] = defaultdict(set)
    for row in observations:
        grouped[(row["project_id"], row["trace_id"])].add(row["session_id"])

    scopes = {}
    for (project_id, trace_id), sessions in grouped.items():
        if len(sessions) == 1 and None not in sessions and "" not in sessions:
            scopes[(project_id, trace_id)] = (project_id, "session", next(iter(sessions)))
        else:
            scopes[(project_id, trace_id)] = (project_id, "trace", trace_id)
    return scopes


def _capture_lines(path: Path) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    capture_path = path / "raw" / "capture.jsonl"
    if not capture_path.exists():
        return {}

    events: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for line_no, event in _jsonl(capture_path):
        capture_id = event.get("capture_id")
        if isinstance(capture_id, str) and capture_id:
            events[capture_id].append((line_no, event))
    return events


def _episode_groups(
    observations: list[dict[str, Any]],
    scopes: dict[tuple[str, str], tuple[str, str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in observations:
        grouped[scopes[(row["project_id"], row["trace_id"])]].add(row["trace_id"])

    return [
        {
            "id": _node_id("episode", *scope),
            "scope": {"project_id": scope[0], "kind": scope[1], "id": scope[2]},
            "trace_ids": sorted(trace_ids),
            "boundary": "unknown",
            "decision_ids": [],
        }
        for scope, trace_ids in sorted(grouped.items())
    ]


def _actions(
    decision_id: str,
    capture_id: str,
    project_id: str,
    trace_id: str,
    events: list[tuple[int, dict[str, Any]]],
    observations_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    finishes = [(line, event) for line, event in events if event.get("kind") == "finish"]
    deliveries = [(line, event) for line, event in events if event.get("kind") == "delivery"]
    if len(finishes) != 1:
        return [], []

    assistant = finishes[0][1].get("assistant_message")
    calls = assistant.get("tool_calls", []) if isinstance(assistant, dict) else []
    if not isinstance(calls, list):
        return [], []

    actions = []
    delivered = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            continue
        action_id = _node_id("action", decision_id, str(index), call_id)
        matched = [(line, event) for line, event in deliveries if event.get("call_id") == call_id]
        delivery_id = None
        if len(matched) == 1:
            line, event = matched[0]
            observed_id = event.get("observation_id")
            observed = observations_by_id.get(observed_id) if isinstance(observed_id, str) else None
            verified = bool(
                observed
                and observed["type"] == "TOOL"
                and observed["trace_id"] == trace_id
                and observed["project_id"] == project_id
            )
            delivery_id = _node_id("delivery", capture_id, str(line))
            delivered.append(
                {
                    "id": delivery_id,
                    "action_id": action_id,
                    "capture_line": line,
                    "call_id": call_id,
                    "tool_observation_id": observed_id if verified else None,
                    "source_ref": observed["source_ref"] if verified else None,
                    "value": event.get("value"),
                    "value_recorded": "value" in event,
                    "provenance": "capture_delivered_value",
                    "backend_output_provenance": "langfuse_tool_observation"
                    if verified
                    else "unknown",
                }
            )
        function = call.get("function")
        actions.append(
            {
                "id": action_id,
                "decision_id": decision_id,
                "index": index,
                "call_id": call_id,
                "tool_name": function.get("name") if isinstance(function, dict) else None,
                "arguments": function.get("arguments") if isinstance(function, dict) else None,
                "delivery_id": delivery_id,
                "retry_relation": "unknown",
                "state": (
                    "verified_delivery"
                    if delivery_id and verified and "value" in matched[0][1]
                    else "unverified_delivery"
                    if delivery_id
                    else "missing_result"
                ),
            }
        )
    return actions, delivered


def build_episode_graph(path: str | Path) -> dict[str, Any]:
    """Preserve captured prefixes; never infer them from observation order."""
    archive = read_archive(Path(path))
    report = audit(path)
    observations = archive["observations"]
    observations_by_id = {row["id"]: row for row in observations}
    scopes = _scope_by_trace(observations)
    episodes = _episode_groups(observations, scopes)
    episode_by_scope = {
        (item["scope"]["project_id"], item["scope"]["kind"], item["scope"]["id"]): item
        for item in episodes
    }
    capture_events = _capture_lines(archive["path"])
    decisions = []
    actions = []
    deliveries = []
    used_tool_ids = set()

    for audited in report["decisions"]:
        row = observations_by_id[audited["observation_id"]]
        decision_id = _node_id("decision", row["project_id"], row["id"])
        episode = episode_by_scope[scopes[(row["project_id"], row["trace_id"])]]
        episode["decision_ids"].append(decision_id)
        events = capture_events.get(audited["capture_id"], [])
        beginnings = [(line, event) for line, event in events if event.get("kind") == "begin"]
        endings = [(line, event) for line, event in events if event.get("kind") == "finish"]
        aborts = [(line, event) for line, event in events if event.get("kind") == "abort"]
        begin_line = beginnings[0][0] if len(beginnings) == 1 else None
        finish_line = endings[0][0] if len(endings) == 1 else None

        local_actions, local_deliveries = (
            _actions(
                decision_id,
                audited["capture_id"],
                row["project_id"],
                row["trace_id"],
                events,
                observations_by_id,
            )
            if isinstance(audited["capture_id"], str)
            else ([], [])
        )
        actions.extend(local_actions)
        deliveries.extend(local_deliveries)
        used_tool_ids.update(
            item["tool_observation_id"]
            for item in local_deliveries
            if item["tool_observation_id"] is not None
        )
        decisions.append(
            {
                "id": decision_id,
                "episode_id": episode["id"],
                "trace_id": row["trace_id"],
                "observation_id": row["id"],
                "source_ref": row["source_ref"],
                "capture_id": audited["capture_id"],
                "begin_line": begin_line,
                "finish_line": finish_line,
                "visible_prefix": audited["_begin"].get("messages") if audited["_begin"] else None,
                "visible_prefix_provenance": "capture_begin" if begin_line else "unknown",
                "provider_request_parameters": (
                    audited["_begin"].get("request_parameters") if audited["_begin"] else None
                ),
                "provider_response": (
                    audited["_finish"].get("provider_response") if audited["_finish"] else None
                ),
                "history_relation": "unknown",
                "context_compaction": "unknown",
                "terminal_state": (
                    "aborted" if aborts else "finished" if finish_line else "unknown"
                ),
                "provider_format": audited["provider_format"],
                "structural_candidate": audited["candidate"],
                "reasons": audited["reasons"],
                "action_ids": [item["id"] for item in local_actions],
            }
        )

    score_links = []
    for score in archive["scores"]:
        subject = score["subject"]
        kind = subject.get("kind") if isinstance(subject, dict) else None
        subject_id = subject.get("id") if isinstance(subject, dict) else None
        score_project = score.get("project_id")
        resolved = False
        if kind == "observation" and isinstance(subject_id, str):
            target = observations_by_id.get(subject_id)
            resolved = bool(
                target and (score_project is None or target["project_id"] == score_project)
            )
        elif kind == "trace" and isinstance(subject_id, str):
            matches = [
                key
                for key in scopes
                if key[1] == subject_id and (score_project is None or key[0] == score_project)
            ]
            resolved = len(matches) == 1
        score_links.append(
            {
                "score_id": score["id"],
                "subject_kind": kind,
                "subject_id": subject_id,
                "resolved": resolved,
                "source_ref": score["source_ref"],
            }
        )

    observation_links = []
    for row in observations:
        parent_id = row["parent_observation_id"]
        if not isinstance(parent_id, str) or not parent_id:
            continue
        parent = observations_by_id.get(parent_id)
        valid = bool(
            parent
            and parent["project_id"] == row["project_id"]
            and parent["trace_id"] == row["trace_id"]
        )
        observation_links.append(
            {
                "child_id": row["id"],
                "parent_id": parent_id,
                "resolved": valid,
                "relation": "langfuse_observation_parent",
            }
        )

    trace_nodes = [
        {
            "id": _node_id("trace", *key),
            "project_id": key[0],
            "trace_id": key[1],
            "episode_id": episode_by_scope[scope]["id"],
        }
        for key, scope in sorted(scopes.items())
    ]

    return {
        "schema_version": GRAPH_SCHEMA,
        "snapshot_id": report["snapshot_id"],
        "episodes": episodes,
        "traces": trace_nodes,
        "decisions": decisions,
        "actions": actions,
        "deliveries": deliveries,
        "observation_links": observation_links,
        "score_links": score_links,
        "orphan_tool_observation_ids": sorted(
            row["id"]
            for row in observations
            if row["type"] == "TOOL" and row["id"] not in used_tool_ids
        ),
        "raw_capture_order": sorted(
            (
                {"line": line, "capture_id": capture_id, "kind": event.get("kind")}
                for capture_id, events in capture_events.items()
                for line, event in events
            ),
            key=lambda item: item["line"],
        ),
        "order": {
            "raw_capture": "capture_line",
            "observations": "start_time_then_id",
            "causal": "explicit_links_only",
        },
    }
