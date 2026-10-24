"""Two difficult MuSiQue train tasks with stateless Responses tool calling.

This is a diagnostic teacher arm. It captures the provider-native input items,
including encrypted reasoning items needed for replay, in private local files.
The resulting decisions are not exported as Chat-format SFT examples.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from live_langfuse_smoke import _save_snapshot
from live_musique_pilot import (
    INDEX_SHA256,
    LOCAL_LANGFUSE_URL,
    SOURCE_SHA256,
    TOOLS,
    FrozenSearch,
    _gold_string_present,
    _load_tasks,
    _private_json,
    _reconcile_score,
    _sha256,
)

from trailgate.audit import public_report
from trailgate.capture import CaptureWriter
from trailgate.snapshot import ingest_snapshot

MODEL = "gpt-6.1-sol"
REASONING_EFFORT = "xhigh"
MAX_OUTPUT_TOKENS = 4096
MAX_PROMPT_BYTES = 64_000
MAX_ESTIMATED_USD = 0.5
INPUT_USD_PER_MILLION = 2.0
OUTPUT_USD_PER_MILLION = 10.0
RESPONSE_TOOLS = [{"type": "function", **tool["function"]} for tool in TOOLS]


@dataclass(frozen=True)
class Arm:
    name: str
    task_positions: tuple[int, ...]
    max_model_calls: int
    search_limit: int
    read_limit: int
    grep_limit: int
    displayed_total: int


STANDARD = Arm("standard", (2, 4), 6, 2, 2, 1, 15)
EXPANDED_FOUR_HOP = Arm("expanded_four_hop", (4,), 10, 4, 4, 2, 30)
ALL_SIX = Arm("all_six", (0, 1, 2, 3, 4, 5), 6, 2, 2, 1, 15)
BRIDGE_CHECK_SIX = Arm("bridge_check_six", (0, 1, 2, 3, 4, 5), 6, 2, 2, 1, 15)


def _system_prompt(arm: Arm) -> str:
    prompt = (
        "You are a retrieval subagent. Start with search, use read when a snippet is insufficient, "
        "and answer only from tool evidence. Give a short answer with cited document IDs. "
        f"If evidence is insufficient, say so. You have at most {arm.search_limit} searches, "
        f"{arm.read_limit} reads, {arm.grep_limit} grep, and {arm.max_model_calls} model turns."
    )
    if arm.name == "bridge_check_six":
        prompt += (
            " Before answering, verify that retrieved evidence supports each relation linking "
            "the question's entities to the answer. Do not substitute a related but different "
            "relation. If a required link is ambiguous, use a remaining tool call to check it; "
            "otherwise state that evidence is insufficient."
        )
    return prompt


def _estimated_usd(totals: dict[str, int]) -> float:
    input_tokens = totals["input_tokens"] + totals["reserved_input_tokens"]
    output_tokens = totals["output_tokens"] + totals["reserved_output_tokens"]
    return (
        input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000


def _canonical_assistant(response: Any) -> tuple[dict[str, Any], list[Any]]:
    calls = [item for item in response.output if item.type == "function_call"]
    assistant: dict[str, Any] = {"role": "assistant", "content": response.output_text or ""}
    if calls:
        assistant["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ]
    return assistant, calls


def _run_task(
    client: Any,
    langfuse: Any,
    task: dict[str, Any],
    db_path: Path,
    out: Path,
    totals: dict[str, int],
    arm: Arm,
) -> dict[str, Any]:
    out.mkdir(mode=0o700)
    capture = CaptureWriter(out / "capture.jsonl")
    search = FrozenSearch(
        db_path,
        search_limit=arm.search_limit,
        read_limit=arm.read_limit,
        grep_limit=arm.grep_limit,
        displayed_total=arm.displayed_total,
    )
    input_items: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(arm)},
        {"role": "user", "content": task["question"]},
    ]
    expected_ids: set[str] = set()
    answer = ""
    stop_reason = "max_model_calls"
    calls_made = 0
    reasoning_tokens = 0
    started = datetime.now(UTC)
    try:
        with langfuse.start_as_current_observation(
            as_type="agent",
            name="trailgate-musique-responses-xhigh",
            input={"task_id": task["id"], "question": task["question"]},
            metadata={
                "corpus": "musique-ans-train-frozen-fts",
                "purpose": "diagnostic_only",
                "provider_format": "openai_responses",
                "arm": arm.name,
            },
        ) as root:
            expected_ids.add(root.id)
            trace_id = root.trace_id
            for turn in range(arm.max_model_calls):
                input_bytes = len(json.dumps(input_items, ensure_ascii=False).encode())
                if input_bytes > MAX_PROMPT_BYTES:
                    stop_reason = "provider_input_byte_limit"
                    break
                worst_next = (
                    2 * MAX_PROMPT_BYTES * INPUT_USD_PER_MILLION
                    + MAX_OUTPUT_TOKENS * OUTPUT_USD_PER_MILLION
                ) / 1_000_000
                if _estimated_usd(totals) + worst_next >= MAX_ESTIMATED_USD:
                    stop_reason = "estimated_cost_ceiling"
                    break
                request_parameters = {
                    "model": MODEL,
                    "reasoning": {"effort": REASONING_EFFORT},
                    "tool_choice": "required"
                    if turn == 0
                    else ("none" if turn == arm.max_model_calls - 1 else "auto"),
                    "parallel_tool_calls": False,
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                    "store": False,
                    "include": ["reasoning.encrypted_content"],
                }
                capture_id = capture.begin_decision(
                    messages=input_items,
                    tools=RESPONSE_TOOLS,
                    provider_format="openai_responses",
                    request_parameters=request_parameters,
                )
                with langfuse.start_as_current_observation(
                    as_type="generation",
                    name=f"responses-retrieval-decision-{turn + 1}",
                    model=MODEL,
                    input={
                        "item_types": [item.get("type", item.get("role")) for item in input_items],
                        "bytes": input_bytes,
                    },
                    metadata={
                        "trailgate_capture_id": capture_id,
                        "provider_format": "openai_responses",
                    },
                    model_parameters={
                        "max_output_tokens": MAX_OUTPUT_TOKENS,
                        "reasoning_effort": REASONING_EFFORT,
                    },
                ) as generation:
                    expected_ids.add(generation.id)
                    response = client.responses.create(
                        input=input_items,
                        tools=RESPONSE_TOOLS,
                        **request_parameters,
                    )
                    calls_made += 1
                    assistant, tool_calls = _canonical_assistant(response)
                    capture.finish_decision(
                        capture_id,
                        assistant_message=assistant,
                        provider_response=response.model_dump(mode="json"),
                    )
                    if response.usage is not None:
                        totals["input_tokens"] += response.usage.input_tokens
                        totals["output_tokens"] += response.usage.output_tokens
                        details = response.usage.output_tokens_details
                        reasoning_tokens += (
                            details.reasoning_tokens if details and details.reasoning_tokens else 0
                        )
                        generation.update(
                            usage_details={
                                "input": response.usage.input_tokens,
                                "output": response.usage.output_tokens,
                            }
                        )
                    else:
                        totals["reserved_input_tokens"] += 2 * MAX_PROMPT_BYTES
                        totals["reserved_output_tokens"] += MAX_OUTPUT_TOKENS
                        totals["missing_usage_calls"] += 1
                    generation.update(
                        output={"assistant_projection": assistant, "status": response.status}
                    )
                if not tool_calls:
                    answer = assistant["content"]
                    stop_reason = (
                        "answered"
                        if response.status == "completed"
                        else f"response_{response.status}"
                    )
                    break
                if len(tool_calls) != 1:
                    raise RuntimeError(
                        "parallel function calls were returned despite parallel_tool_calls=False"
                    )
                call = tool_calls[0]
                result = search.execute(call.name, call.arguments)
                delivered = json.dumps(result, ensure_ascii=False)
                with langfuse.start_as_current_observation(
                    as_type="tool", name=call.name, input={"arguments": call.arguments}
                ) as observation:
                    expected_ids.add(observation.id)
                    observation.update(output=result)
                capture.delivered_tool_result(
                    capture_id,
                    call_id=call.call_id,
                    observation_id=observation.id,
                    value=delivered,
                    truncated=bool(
                        result.get("truncated_right") or result.get("truncated_by_display_budget")
                    ),
                )
                # Stateless replay must retain returned reasoning and function-call
                # items exactly, then append the actual tool result.
                input_items = [
                    *input_items,
                    *(item.model_dump(mode="json", exclude_none=True) for item in response.output),
                    {"type": "function_call_output", "call_id": call.call_id, "output": delivered},
                ]
            root.update(output={"answer": answer, "stop_reason": stop_reason})
    finally:
        search.close()
        langfuse.flush()

    support_titles = {p["title"] for p in task["paragraphs"] if p["is_supporting"]}
    weak_match = _gold_string_present(answer, task["answer"], task.get("answer_aliases") or [])
    score_id = str(uuid.uuid4())
    langfuse.create_score(
        name="gold_string_present_weak",
        value=1.0 if weak_match else 0.0,
        data_type="NUMERIC",
        trace_id=trace_id,
        score_id=score_id,
        comment="Weak exact-string diagnostic; not a semantic correctness or training reward judgment.",
    )
    langfuse.flush()
    ended = datetime.now(UTC)
    _private_json(
        out / "run_state.json",
        {
            "trace_id": trace_id,
            "expected_observation_ids": sorted(expected_ids),
            "score_id": score_id,
            "started": started.isoformat(),
            "ended": ended.isoformat(),
        },
    )
    snapshot = _save_snapshot(langfuse, trace_id, expected_ids, started, ended, out)
    archive = out / "archive"
    ingest_snapshot(snapshot, archive)
    report = public_report(archive)
    result = {
        "task_id": task["id"],
        "trace_id": trace_id,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "arm": arm.name,
        "calls": calls_made,
        "stop_reason": stop_reason,
        "reasoning_tokens": reasoning_tokens,
        "tool_counts": search.counts,
        "generations": report["generations"],
        "faithful_candidates": report["faithful_candidates"],
        "sft_projection_supported_count": sum(
            row["sft_projection_supported"] for row in report["decisions"]
        ),
        "observations": report["observations"],
        "scores_in_snapshot": report["scores"],
        "score_id": score_id,
        "gold_string_present_weak": weak_match,
        "support_titles_seen": len(support_titles & search.searched_titles),
        "support_titles_total": len(support_titles),
        "support_titles_read": len(support_titles & search.read_titles),
        "answer": answer,
        "gold_answer": task["answer"],
        "rights_status": "not_attested; diagnostic only",
    }
    _private_json(out / "summary.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--task-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--expanded-four-hop",
        action="store_true",
        help="Run the frozen 4-hop failure with larger tool budgets",
    )
    mode.add_argument("--all-six", action="store_true", help="Run all six frozen difficulty cells")
    mode.add_argument(
        "--bridge-check-six",
        action="store_true",
        help="Run six cells with a generic relation-evidence check instruction",
    )
    args = parser.parse_args()
    out = args.out.absolute()
    if out.exists() or not out.parent.is_dir():
        parser.error("--out must be a new directory under an existing parent")
    if _sha256(args.archive) != SOURCE_SHA256 or _sha256(args.db) != INDEX_SHA256:
        raise RuntimeError("MuSiQue ZIP or frozen index hash differs from the pilot manifest")
    arm = (
        EXPANDED_FOUR_HOP
        if args.expanded_four_hop
        else BRIDGE_CHECK_SIX
        if args.bridge_check_six
        else ALL_SIX
        if args.all_six
        else STANDARD
    )
    tasks = _load_tasks(args.archive, args.task_manifest)
    tasks = [tasks[position] for position in arm.task_positions]
    os.environ["LANGFUSE_BASE_URL"] = LOCAL_LANGFUSE_URL
    from langfuse import get_client
    from openai import OpenAI

    langfuse = get_client()
    if not langfuse.auth_check():
        raise RuntimeError("local Langfuse authentication failed; no model call made")
    client = OpenAI(max_retries=0, timeout=180.0)
    out.mkdir(mode=0o700)
    _private_json(
        out / "manifest.json",
        {
            "source": "MuSiQue-Ans official train",
            "source_sha256": SOURCE_SHA256,
            "index_sha256": INDEX_SHA256,
            "task_ids": [task["id"] for task in tasks],
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "arm": arm.__dict__,
            "provider_format": "openai_responses",
            "store": False,
            "max_model_calls_per_task": arm.max_model_calls,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_prompt_bytes": MAX_PROMPT_BYTES,
            "max_estimated_usd": MAX_ESTIMATED_USD,
            "input_usd_per_million": INPUT_USD_PER_MILLION,
            "output_usd_per_million": OUTPUT_USD_PER_MILLION,
            "rights_status": "diagnostic_only",
        },
    )
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reserved_input_tokens": 0,
        "reserved_output_tokens": 0,
        "missing_usage_calls": 0,
    }
    results = []
    for task in tasks:
        result = _run_task(client, langfuse, task, args.db, out / task["id"], totals, arm)
        results.append(result)
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "task_id",
                        "trace_id",
                        "calls",
                        "reasoning_tokens",
                        "tool_counts",
                        "faithful_candidates",
                        "gold_string_present_weak",
                    )
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    for result in results:
        if result["scores_in_snapshot"] == 0:
            try:
                result["scores_after_reconciliation"] = _reconcile_score(
                    langfuse, out / result["task_id"]
                )
            except RuntimeError as exc:
                result["scores_after_reconciliation"] = 0
                result["score_reconciliation_error"] = str(exc)
        else:
            result["scores_after_reconciliation"] = result["scores_in_snapshot"]
    summary = {
        "tasks": len(results),
        "model_calls": sum(row["calls"] for row in results),
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "missing_usage_calls": totals["missing_usage_calls"],
        "reserved_input_tokens": totals["reserved_input_tokens"],
        "reserved_output_tokens": totals["reserved_output_tokens"],
        "reasoning_tokens": sum(row["reasoning_tokens"] for row in results),
        "estimated_usd_at_uncached_text_rates": round(_estimated_usd(totals), 8),
        "hard_code_ceiling_usd": MAX_ESTIMATED_USD,
        "structurally_faithful_decisions": sum(row["faithful_candidates"] for row in results),
        "generations": sum(row["generations"] for row in results),
        "chat_sft_projection_supported_count": sum(
            row["sft_projection_supported_count"] for row in results
        ),
        "weak_gold_string_matches": sum(row["gold_string_present_weak"] for row in results),
        "scores_captured": sum(row["scores_after_reconciliation"] for row in results),
        "rights_status": "not_attested; diagnostic only",
    }
    _private_json(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
