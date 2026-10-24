"""Export a deterministic private dataset after independent eligibility gates."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .dataset_assessment import assess_dataset
from .dataset_policy import POLICY_SCHEMA
from .episode_graph import GRAPH_SCHEMA, build_episode_graph
from .private_io import _private_bytes
from .snapshot_input import SnapshotError

DATASET_SCHEMA = "trailgate.dataset.v1"


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        for row in rows:
            data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
            output.write(data)
            digest.update(data)
            count += 1
        output.flush()
        os.fsync(output.fileno())
    return count, digest.hexdigest()


def _sft_rows(
    decisions: list[dict[str, Any]],
    eligibility: dict[str, dict[str, Any]],
    snapshot_id: str,
    use: str,
    rights_hash: str,
    policy_hash: str,
) -> Iterable[dict[str, Any]]:
    for decision in decisions:
        selected = eligibility[decision["observation_id"]]
        if not selected["eligible_for_chat_sft"]:
            continue
        begin, finish = decision["_begin"], decision["_finish"]
        yield {
            "schema_version": "trailgate.chat_sft.v1",
            "messages": [*begin["messages"], finish["assistant_message"]],
            "tools": begin["tools"],
            "request_parameters": begin.get("request_parameters"),
            "target": "last_assistant_message",
            "source": {
                "snapshot_id": snapshot_id,
                "trace_id": decision["trace_id"],
                "observation_id": decision["observation_id"],
                "capture_id": decision["capture_id"],
            },
            "task_id": selected["split"]["task_id"],
            "split": "train",
            "use": use,
            "rights_sha256": rights_hash,
            "policy_sha256": policy_hash,
        }


def _episode_rows(
    graph: dict[str, Any], eligibility: dict[str, dict[str, Any]]
) -> Iterable[dict[str, Any]]:
    decisions_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    actions_by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    deliveries_by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scores_by_subject: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for decision in graph["decisions"]:
        decisions_by_episode[decision["episode_id"]].append(decision)
    for action in graph["actions"]:
        actions_by_decision[action["decision_id"]].append(action)
    for delivery in graph["deliveries"]:
        deliveries_by_action[delivery["action_id"]].append(delivery)
    for link in graph["score_links"]:
        if isinstance(link["subject_kind"], str) and isinstance(link["subject_id"], str):
            scores_by_subject[
                (link["project_id"], link["subject_kind"], link["subject_id"])
            ].append(link)

    for episode in graph["episodes"]:
        project_id = episode["scope"]["project_id"]
        decisions = decisions_by_episode[episode["id"]]
        actions = [
            action for decision in decisions for action in actions_by_decision[decision["id"]]
        ]
        deliveries = [
            delivery for action in actions for delivery in deliveries_by_action[action["id"]]
        ]
        score_links = [
            score
            for trace_id in episode["trace_ids"]
            for score in scores_by_subject[(project_id, "trace", trace_id)]
        ]
        if episode["scope"]["kind"] == "session":
            score_links.extend(scores_by_subject[(project_id, "session", episode["scope"]["id"])])
        score_links.extend(
            score
            for decision in decisions
            for score in scores_by_subject[(project_id, "observation", decision["observation_id"])]
        )
        score_links.extend(
            score
            for delivery in deliveries
            if delivery["tool_observation_id"]
            for score in scores_by_subject[
                (project_id, "observation", delivery["tool_observation_id"])
            ]
        )
        yield {
            "schema_version": GRAPH_SCHEMA,
            "eligibility": "diagnostic_only",
            "snapshot_id": graph["snapshot_id"],
            "episode": episode,
            "decisions": [
                {**item, "eligibility": eligibility[item["observation_id"]]} for item in decisions
            ],
            "actions": actions,
            "deliveries": deliveries,
            "score_links": score_links,
        }


def export_dataset(
    archive_path: str | Path,
    rights_path: str | Path,
    policy_path: str | Path,
    use: str,
    destination: str | Path,
) -> dict[str, Any]:
    """Require all training gates; retain rejected decisions in private episodes."""
    archive_path, destination = Path(archive_path), Path(destination)
    if destination.exists() or not destination.parent.is_dir():
        raise SnapshotError("dataset destination exists or its parent is missing")
    assessment = assess_dataset(archive_path, rights_path, policy_path, use)
    index = assessment["index"]
    report = assessment["report"]
    rights = assessment["rights"]
    rights_hash = assessment["rights_hash"]
    policy = assessment["policy"]
    policy_hash = assessment["policy_hash"]
    decisions = report["decisions"]
    eligibility = assessment["eligibility"]
    graph = build_episode_graph(archive_path)

    temporary = Path(tempfile.mkdtemp(prefix=".trailgate-dataset-", dir=destination.parent))
    try:
        os.chmod(temporary, 0o700)
        sft_count, sft_hash = _write_jsonl(
            temporary / "chat_sft.jsonl",
            _sft_rows(decisions, eligibility, report["snapshot_id"], use, rights_hash, policy_hash),
        )
        episode_count, episode_hash = _write_jsonl(
            temporary / "episodes.jsonl", _episode_rows(graph, eligibility)
        )
        reason_counts = assessment["reason_counts"]
        manifest = {
            "schema_version": DATASET_SCHEMA,
            "snapshot_id": report["snapshot_id"],
            "archive_schema": index["schema_version"],
            "source_hashes": index["source_hashes"],
            "transform": {"episode_graph": GRAPH_SCHEMA, "policy": POLICY_SCHEMA},
            "use": use,
            "rights": {
                "sha256": rights_hash,
                "attested_by": rights["attested_by"],
                "confirmed_at": rights["confirmed_at"],
            },
            "policy_sha256": policy_hash,
            "quality_method": policy["quality"]["method"],
            "privacy": policy["privacy"],
            "split_evidence": sorted(policy["splits"], key=lambda item: item["trace_id"]),
            "counts": {
                "decisions": len(decisions),
                "eligible_chat_sft": sft_count,
                "rejected_chat_sft": len(decisions) - sft_count,
                "episodes": episode_count,
            },
            "rejected_reasons": reason_counts,
            "output_hashes": {"chat_sft.jsonl": sft_hash, "episodes.jsonl": episode_hash},
            "views": {
                "chat_sft.jsonl": "gated_training_view",
                "episodes.jsonl": "diagnostic_only",
            },
        }
        _private_bytes(
            temporary / "manifest.json",
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n").encode(),
        )
        os.rename(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "snapshot_id": report["snapshot_id"],
        "eligible_chat_sft": sft_count,
        "rejected_chat_sft": len(decisions) - sft_count,
        "episodes": episode_count,
        "reason_counts": reason_counts,
    }
