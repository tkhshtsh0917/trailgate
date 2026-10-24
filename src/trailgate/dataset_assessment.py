"""Assess dataset eligibility without writing training examples."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .archive import _validate_index, read_archive
from .dataset_policy import read_dataset_policy
from .decision_audit import audit
from .exports import _check_rights
from .snapshot_input import SnapshotError, _read_json


def _decision_gates(
    decision: dict[str, Any],
    approved: set[str],
    splits: dict[str, dict[str, str]],
    isolated_traces: set[tuple[str, str]],
) -> dict[str, Any]:
    split = splits.get(decision["trace_id"])
    gates = {
        "structural": decision["candidate"],
        "chat_format": decision["sft_projection_supported"],
        "quality_manual_selection": decision["observation_id"] in approved,
        "rights_attested": True,
        "privacy_reviewed": True,
        "split_recorded": split is not None,
        "training_split": bool(split and split["split"] == "train"),
        "session_scope_isolated": (decision["_project_id"], decision["trace_id"])
        in isolated_traces,
    }
    reasons = [f"structural:{reason}" for reason in decision["reasons"]]
    reasons.extend(f"gate:{name}" for name, passed in gates.items() if not passed)
    return {
        "observation_id": decision["observation_id"],
        "trace_id": decision["trace_id"],
        "gates": gates,
        "eligible_for_chat_sft": all(gates.values()),
        "reasons": reasons,
        "split": split,
    }


def _isolated_traces(
    observations: list[dict[str, Any]], splits: dict[str, dict[str, str]]
) -> set[tuple[str, str]]:
    """Reject known session peers with missing or different task/split evidence."""
    sessions: dict[tuple[str, str], set[str]] = {}
    trace_sessions: dict[tuple[str, str], set[tuple[str, str]]] = {}
    trace_projects: dict[str, set[str]] = {}
    traces: set[tuple[str, str]] = set()
    for row in observations:
        scope = (row["project_id"], row["trace_id"])
        traces.add(scope)
        trace_projects.setdefault(row["trace_id"], set()).add(row["project_id"])
        if isinstance(row.get("session_id"), str) and row["session_id"]:
            session = (row["project_id"], row["session_id"])
            sessions.setdefault(session, set()).add(row["trace_id"])
            trace_sessions.setdefault(scope, set()).add(session)

    isolated = set()
    for scope in traces:
        project_id, trace_id = scope
        own_split = splits.get(trace_id)
        if len(trace_projects[trace_id]) != 1 or own_split is None:
            continue
        if all(
            all(
                len(trace_projects[peer]) == 1 and splits.get(peer) == own_split
                for peer in sessions[session]
            )
            for session in trace_sessions.get((project_id, trace_id), ())
        ):
            isolated.add(scope)
    return isolated


def assess_dataset(
    archive_path: str | Path,
    rights_path: str | Path,
    policy_path: str | Path,
    use: str,
) -> dict[str, Any]:
    """Validate declarations and compute one shared eligibility decision."""
    archive_path = Path(archive_path)
    index = _read_json(archive_path / "index.json")
    _validate_index(archive_path, index)
    report = audit(archive_path)
    rights, rights_hash = _check_rights(Path(rights_path), use, report["snapshot_id"])
    policy, policy_hash = read_dataset_policy(Path(policy_path), report["snapshot_id"], use)

    decisions = report["decisions"]
    known_ids = {item["observation_id"] for item in decisions}
    approved = set(policy["quality"]["approved_observation_ids"])
    if not approved <= known_ids:
        raise SnapshotError("quality approval references an unknown generation")
    splits = {item["trace_id"]: item for item in policy["splits"]}
    observations = read_archive(archive_path)["observations"]
    isolated_traces = _isolated_traces(observations, splits)
    evaluations = [_decision_gates(item, approved, splits, isolated_traces) for item in decisions]
    reason_counts: Counter[str] = Counter()
    for item in evaluations:
        if not item["eligible_for_chat_sft"]:
            reason_counts.update(item["reasons"])

    eligible_count = sum(item["eligible_for_chat_sft"] for item in evaluations)
    return {
        "index": index,
        "report": report,
        "rights": rights,
        "rights_hash": rights_hash,
        "policy": policy,
        "policy_hash": policy_hash,
        "evaluations": evaluations,
        "eligibility": {item["observation_id"]: item for item in evaluations},
        "eligible_count": eligible_count,
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def evaluate_dataset(
    archive_path: str | Path,
    rights_path: str | Path,
    policy_path: str | Path,
    use: str,
    *,
    max_decisions: int = 100,
) -> dict[str, Any]:
    """Return a bounded content-free eligibility preview."""
    if not 0 <= max_decisions <= 1000:
        raise SnapshotError("max_decisions must be between 0 and 1000")
    assessment = assess_dataset(archive_path, rights_path, policy_path, use)
    evaluations = assessment["evaluations"]
    return {
        "snapshot_id": assessment["report"]["snapshot_id"],
        "use": use,
        "rights_attested": True,
        "privacy_reviewed": True,
        "quality_method": assessment["policy"]["quality"]["method"],
        "decisions": len(evaluations),
        "eligible_chat_sft": assessment["eligible_count"],
        "rejected_chat_sft": len(evaluations) - assessment["eligible_count"],
        "rejected_reasons": assessment["reason_counts"],
        "decision_gates": evaluations[:max_decisions],
        "omitted_decisions": max(0, len(evaluations) - max_decisions),
    }
