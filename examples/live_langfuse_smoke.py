"""Two-call, local-search trajectory smoke test. Run only with explicit intent.

The OpenAI and Langfuse SDKs consume credentials from the process environment.
This module never reads or prints OPENAI_API_KEY. No network retrieval tool is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trailgate.capture import CaptureWriter
from trailgate.snapshot import SNAPSHOT_SCHEMA, _private_bytes, ingest_snapshot, read_snapshot

MODEL = "gpt-6-luna"
MAX_CALLS = 2
MAX_COMPLETION_TOKENS = 256
MAX_ESTIMATED_USD = 0.01
LOCAL_LANGFUSE_URL = "http://127.0.0.1:3000"
QUESTION = "In the 2025 Orion plan, which storage engine is specified and what is its recovery target? Cite the document ID."
DOCS = (
    ("doc-orion-2024", "Orion 2024 plan: Quartz storage engine. Recovery target: 6 hours."),
    ("doc-vega-2025", "Vega 2025 plan: Quartz storage engine. Recovery target: 1 hour."),
    ("doc-orion-2025", "Orion 2025 plan: Granite storage engine. Recovery target: 2 hours."),
    ("doc-orion-checklist", "Orion 2025 rollout checklist: verify backups twice daily."),
)
TOOL = {
    "type": "function",
    "function": {
        "name": "search_local_corpus",
        "description": "Search the local planning notes; returns document IDs and snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords for the local search"}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}


def search_local_corpus(query: str) -> str:
    words = set(re.findall(r"[a-z0-9]+", query.lower())) - {
        "the",
        "and",
        "what",
        "which",
        "is",
        "in",
    }
    ranked = []
    for doc_id, body in DOCS:
        haystack = f"{doc_id} {body}".lower()
        overlap = sum(word in haystack for word in words)
        ranked.append((overlap, doc_id, body))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    results = [{"id": doc_id, "text": body} for score, doc_id, body in ranked if score > 0][:3]
    return json.dumps(results, ensure_ascii=False)


def _utc() -> datetime:
    return datetime.now(UTC)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode()


def _estimate_usd(prompt_tokens: int, completion_tokens: int) -> float:
    # Full uncached text rates. These are a conservative estimate for this tiny run.
    return prompt_tokens * 0.10 / 1_000_000 + completion_tokens * 0.50 / 1_000_000


def _pull_pages(
    fetch: Any, *, trace_id: str, fields: str, start: datetime, end: datetime, score: bool
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor = None
    while True:
        kwargs: dict[str, Any] = {
            "trace_id": trace_id,
            "fields": fields,
            "limit": 100,
            "cursor": cursor,
        }
        if score:
            kwargs.update(from_timestamp=start, to_timestamp=end)
        else:
            kwargs.update(from_start_time=start, to_start_time=end)
        page = json.loads(fetch(**kwargs).json())
        pages.append(page)
        cursor = page["meta"].get("cursor")
        if cursor is None:
            return pages
        if len(pages) >= 20:
            raise RuntimeError("pagination limit reached; snapshot left incomplete")


def _save_snapshot(
    langfuse: Any,
    trace_id: str,
    expected_ids: set[str],
    started: datetime,
    ended: datetime,
    run_dir: Path,
    expected_score_ids: set[str] | None = None,
) -> Path:
    start, end = started - timedelta(seconds=5), ended + timedelta(seconds=5)
    observations = []
    for attempt in range(5):
        observations = _pull_pages(
            langfuse.api.observations.get_many,
            trace_id=trace_id,
            fields="core,basic,io,metadata,model",
            start=start,
            end=end,
            score=False,
        )
        found_ids = {row["id"] for page in observations for row in page["data"]}
        if expected_ids <= found_ids:
            break
        if attempt < 4:
            time.sleep(1)
    expected_score_ids = expected_score_ids or set()
    scores = []
    for attempt in range(10):
        scores = _pull_pages(
            langfuse.api.scores_v3.get_many_v3,
            trace_id=trace_id,
            fields="details,subject",
            start=start,
            end=end,
            score=True,
        )
        found_scores = {row["id"] for page in scores for row in page["data"]}
        if expected_score_ids <= found_scores:
            break
        if attempt < 9:
            time.sleep(0.5)
    if not expected_ids <= found_ids:
        raise RuntimeError(
            "Langfuse has not returned every expected observation; capture remains private"
        )
    if not expected_score_ids <= found_scores:
        raise RuntimeError(
            "Langfuse has not returned every expected score; capture remains private"
        )
    snapshot = run_dir / "snapshot"
    snapshot.mkdir(mode=0o700)
    manifest = {
        "schema_version": SNAPSHOT_SCHEMA,
        "source": "langfuse",
        "observations_api": "v2",
        "scores_api": "v3",
        "observation_fields": ["core", "basic", "io", "metadata", "model"],
        "score_fields": ["details", "subject"],
        "query": {"fromStartTime": start.isoformat(), "toStartTime": end.isoformat()},
        "score_query": {"fromTimestamp": start.isoformat(), "toTimestamp": end.isoformat()},
        "complete": True,
        "trace_id_filter": trace_id,
    }
    _private_bytes(snapshot / "manifest.json", _json_bytes(manifest))
    _private_bytes(
        snapshot / "observations.jsonl", b"".join(_json_bytes(page) for page in observations)
    )
    _private_bytes(snapshot / "scores.jsonl", b"".join(_json_bytes(page) for page in scores))
    _private_bytes(snapshot / "capture.jsonl", (run_dir / "capture.jsonl").read_bytes())
    read_snapshot(snapshot)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="New private directory; must not exist"
    )
    args = parser.parse_args()
    out = args.out.absolute()
    if out.exists() or not out.parent.is_dir():
        parser.error("--out must be a new directory under an existing parent")

    # Force telemetry to the user's local Langfuse; never fall back to a cloud host.
    os.environ["LANGFUSE_BASE_URL"] = LOCAL_LANGFUSE_URL
    from langfuse import get_client
    from openai import OpenAI

    langfuse = get_client()
    if not langfuse.auth_check():
        raise RuntimeError("local Langfuse authentication failed; no model call made")
    client = OpenAI(max_retries=0, timeout=20.0)
    out.mkdir(mode=0o700)
    capture = CaptureWriter(out / "capture.jsonl")
    started = _utc()
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "Use the local search tool before answering. Keep the answer to one sentence and cite a document ID.",
        },
        {"role": "user", "content": QUESTION},
    ]
    totals = {"prompt_tokens": 0, "completion_tokens": 0}
    trace_id = ""
    answer = ""
    expected_ids: set[str] = set()

    try:
        with langfuse.start_as_current_observation(
            as_type="agent",
            name="trailgate-live-local-search",
            input={"question": QUESTION},
            metadata={"corpus": "local-fictional-plans-v1", "purpose": "diagnostic_only"},
        ) as root:
            trace_id = root.trace_id
            expected_ids.add(root.id)
            for turn in range(MAX_CALLS):
                if _estimate_usd(**totals) >= MAX_ESTIMATED_USD:
                    raise RuntimeError("estimated cost ceiling reached")
                capture_id = capture.begin_decision(messages=messages, tools=[TOOL])
                with langfuse.start_as_current_observation(
                    as_type="generation",
                    name="search-decision" if turn == 0 else "answer-decision",
                    model=MODEL,
                    input=messages,
                    metadata={"trailgate_capture_id": capture_id},
                    model_parameters={
                        "max_completion_tokens": MAX_COMPLETION_TOKENS,
                        "reasoning_effort": "none",
                    },
                ) as generation:
                    expected_ids.add(generation.id)
                    response = client.chat.completions.create(
                        model=MODEL,
                        reasoning_effort="none",
                        messages=messages,
                        tools=[TOOL],
                        tool_choice="required" if turn == 0 else "none",
                        parallel_tool_calls=False,
                        max_completion_tokens=MAX_COMPLETION_TOKENS,
                    )
                    assistant = response.choices[0].message.model_dump(
                        mode="json", exclude_none=True
                    )
                    assistant.pop("refusal", None)
                    assistant.pop("annotations", None)
                    capture.finish_decision(
                        capture_id,
                        assistant_message=assistant,
                        provider_response=response.model_dump(mode="json"),
                    )
                    if response.usage is not None:
                        totals["prompt_tokens"] += response.usage.prompt_tokens
                        totals["completion_tokens"] += response.usage.completion_tokens
                        generation.update(
                            usage_details={
                                "input": response.usage.prompt_tokens,
                                "output": response.usage.completion_tokens,
                            }
                        )
                    generation.update(output=assistant)
                calls = assistant.get("tool_calls") or []
                if turn == 0:
                    if len(calls) != 1 or calls[0]["function"]["name"] != "search_local_corpus":
                        raise RuntimeError(
                            "first model response did not issue exactly one expected search call"
                        )
                    call = calls[0]
                    arguments = json.loads(call["function"]["arguments"])
                    result = search_local_corpus(arguments["query"])
                    with langfuse.start_as_current_observation(
                        as_type="tool", name="search_local_corpus", input=arguments
                    ) as tool_observation:
                        expected_ids.add(tool_observation.id)
                        tool_observation.update(output=result)
                    capture.delivered_tool_result(
                        capture_id,
                        call_id=call["id"],
                        observation_id=tool_observation.id,
                        value=result,
                    )
                    messages = [
                        *messages,
                        assistant,
                        {"role": "tool", "tool_call_id": call["id"], "content": result},
                    ]
                else:
                    if calls:
                        raise RuntimeError(
                            "second model response unexpectedly requested another tool"
                        )
                    answer = assistant.get("content") or ""
                    root.update(output={"answer": answer})
    finally:
        langfuse.flush()

    ended = _utc()
    snapshot = _save_snapshot(langfuse, trace_id, expected_ids, started, ended, out)
    archive = out / "archive"
    ingest_snapshot(snapshot, archive)
    summary = {
        "trace_id": trace_id,
        "model": MODEL,
        "calls": MAX_CALLS,
        "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "estimated_usd_at_uncached_text_rates": round(_estimate_usd(**totals), 8),
        "answer_matches_fixture": all(
            part in answer for part in ("Granite", "2", "doc-orion-2025")
        ),
        "archive": str(archive),
        "rights_status": "not_attested; diagnostic only",
    }
    _private_bytes(out / "summary.json", _json_bytes(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
