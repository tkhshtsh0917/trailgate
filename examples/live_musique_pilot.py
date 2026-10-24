"""Bounded MuSiQue-Ans train retrieval pilot with private Trailgate captures.

Run explicitly with --archive, --db, --task-manifest, and --out. The SDKs
consume credentials from the environment; this file never reads their values.
Gold answers and support flags are withheld from the model and used only after
each episode as weak diagnostics. No training export is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from live_langfuse_smoke import _save_snapshot

from trailgate.audit import public_report
from trailgate.capture import CaptureWriter
from trailgate.snapshot import _private_bytes, ingest_snapshot

MODEL_RATES = {"gpt-6-luna": (0.10, 0.50), "gpt-6-sol": (2.00, 10.00)}
SOURCE_MEMBER = "data/musique_ans_v1.0_train.jsonl"
SOURCE_SHA256 = "98f839bf2fd5319f5c688aed77901a6d5c30b3b9f9f691ab9a8ecafb045ee0cd"
INDEX_SHA256 = "92391a8bcd1ea9a26ca8717a7661480583eec30c03843066e867db62c1145045"
CELL_REPRESENTATIVE_INDICES = (0, 8, 16, 24, 26, 34)
MAX_MODEL_CALLS_PER_TASK = 6
MAX_COMPLETION_TOKENS = 512
MAX_PROMPT_BYTES = 24_000
LOCAL_LANGFUSE_URL = "http://127.0.0.1:3000"
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
}


@dataclass(frozen=True)
class ModelConfig:
    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    max_estimated_usd: float


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the frozen MuSiQue train paragraph index. Returns document IDs, titles, and 180-character snippets. Search again with a different query if needed.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query", "top_k"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read up to 2,000 characters from a document ID already shown by search. Use offset 0 initially; later offsets are allowed.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "offset": {"type": "integer"}},
                "required": ["id", "offset"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Find a literal phrase inside one previously shown document and return short matching windows.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "pattern": {"type": "string"}},
                "required": ["id", "pattern"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _private_json(path: Path, value: Any) -> None:
    _private_bytes(
        path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _gold_string_present(answer: str, gold: str, aliases: list[str]) -> bool:
    normalized = f" {_normalize(answer)} "
    return any(
        f" {_normalize(candidate)} " in normalized
        for candidate in [gold, *aliases]
        if _normalize(candidate)
    )


def _load_tasks(archive: Path, manifest_path: Path) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = manifest["task_ids"]
    if len(ids) != 35:
        raise ValueError("expected the frozen 35-task teacher pilot manifest")
    selected = [ids[index] for index in CELL_REPRESENTATIVE_INDICES]
    needed = set(selected)
    found: dict[str, dict[str, Any]] = {}
    with ZipFile(archive) as zipped, zipped.open(SOURCE_MEMBER) as stream:
        for raw in stream:
            row = json.loads(raw)
            if row["id"] in needed:
                found[row["id"]] = row
                if len(found) == len(needed):
                    break
    if set(found) != needed:
        raise ValueError("selected task IDs missing from the official train member")
    return [found[task_id] for task_id in selected]


class FrozenSearch:
    def __init__(
        self,
        db_path: Path,
        *,
        search_limit: int = 2,
        read_limit: int = 2,
        grep_limit: int = 1,
        displayed_total: int = 15,
    ) -> None:
        self.db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.shown: set[int] = set()
        self.counts = {"search": 0, "read": 0, "grep": 0}
        self.limits = {"search": search_limit, "read": read_limit, "grep": grep_limit}
        self.displayed_total = displayed_total
        self.searched_titles: set[str] = set()
        self.read_titles: set[str] = set()

    def close(self) -> None:
        self.db.close()

    def execute(self, name: str, arguments: str) -> dict[str, Any]:
        try:
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            if name not in self.counts:
                raise ValueError("unknown tool")
            if self.counts[name] >= self.limits[name]:
                return {"error": "tool_budget_exceeded", "tool": name}
            self.counts[name] += 1
            return getattr(self, name)(**args)
        except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return {"error": type(exc).__name__, "detail": str(exc)}

    def search(self, query: str, top_k: int) -> dict[str, Any]:
        if not isinstance(query, str) or len(query) > 240 or not 1 <= top_k <= 10:
            raise ValueError("query or top_k out of bounds")
        terms = list(
            dict.fromkeys(
                word for word in re.findall(r"[a-z0-9]+", query.lower()) if word not in STOPWORDS
            )
        )[:12]
        if not terms:
            return {"results": [], "reason": "no_content_terms"}
        expression = " OR ".join('"' + word + '"' for word in terms)
        rows = self.db.execute(
            "SELECT rowid,title,body FROM docs WHERE docs MATCH ? ORDER BY bm25(docs,4,1),rowid LIMIT ?",
            (expression, top_k),
        ).fetchall()
        new_rows = [row for row in rows if row[0] not in self.shown]
        remaining = max(0, self.displayed_total - len(self.shown))
        displayed = new_rows[:remaining]
        self.shown.update(row[0] for row in displayed)
        self.searched_titles.update(row[1] for row in displayed)
        return {
            "results": [
                {"id": f"d{rowid}", "title": title, "snippet": body[:180]}
                for rowid, title, body in displayed
            ],
            "reason": "no_new_documents" if not displayed else None,
            "truncated_by_display_budget": len(displayed) < len(new_rows),
        }

    def _shown_row(self, doc_id: str) -> tuple[str, str]:
        if not isinstance(doc_id, str) or not re.fullmatch(r"d[1-9][0-9]*", doc_id):
            raise ValueError("invalid document ID")
        rowid = int(doc_id[1:])
        if rowid not in self.shown:
            raise ValueError("document ID was not shown by search")
        row = self.db.execute("SELECT title,body FROM docs WHERE rowid=?", (rowid,)).fetchone()
        if row is None:
            raise ValueError("document missing from frozen index")
        return row

    def read(self, id: str, offset: int) -> dict[str, Any]:
        title, body = self._shown_row(id)
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or offset >= max(1, len(body))
        ):
            raise ValueError("invalid read offset")
        self.read_titles.add(title)
        end = min(len(body), offset + 2000)
        return {
            "id": id,
            "title": title,
            "text": body[offset:end],
            "offset": offset,
            "total_chars": len(body),
            "truncated_right": end < len(body),
        }

    def grep(self, id: str, pattern: str) -> dict[str, Any]:
        title, body = self._shown_row(id)
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 100:
            raise ValueError("invalid grep pattern")
        matches = []
        for match in list(re.finditer(re.escape(pattern), body, re.IGNORECASE))[:5]:
            matches.append(
                {
                    "offset": match.start(),
                    "text": body[max(0, match.start() - 60) : match.end() + 60],
                }
            )
        return {"id": id, "title": title, "matches": matches}


def _estimated_cost(prompt_tokens: int, completion_tokens: int, config: ModelConfig) -> float:
    return (
        prompt_tokens * config.input_usd_per_million
        + completion_tokens * config.output_usd_per_million
    ) / 1_000_000


def _run_task(
    client: Any,
    langfuse: Any,
    task: dict[str, Any],
    db_path: Path,
    out: Path,
    totals: dict[str, int],
    config: ModelConfig,
) -> dict[str, Any]:
    out.mkdir(mode=0o700)
    capture = CaptureWriter(out / "capture.jsonl")
    search = FrozenSearch(db_path)
    question = task["question"]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "You are a retrieval subagent. Start with search, use read when a snippet is insufficient, and answer only from tool evidence. Give a short answer with cited document IDs. If evidence is insufficient, say so. You have at most two searches, two reads, one grep, and six model turns.",
        },
        {"role": "user", "content": question},
    ]
    expected_ids: set[str] = set()
    answer = ""
    calls_made = 0
    started = datetime.now(UTC)
    try:
        with langfuse.start_as_current_observation(
            as_type="agent",
            name="trailgate-musique-train-pilot",
            input={"task_id": task["id"], "question": question},
            metadata={"corpus": "musique-ans-train-frozen-fts", "purpose": "diagnostic_only"},
        ) as root:
            expected_ids.add(root.id)
            trace_id = root.trace_id
            for turn in range(MAX_MODEL_CALLS_PER_TASK):
                serialized_bytes = len(json.dumps(messages, ensure_ascii=False).encode())
                if serialized_bytes > MAX_PROMPT_BYTES:
                    raise RuntimeError("model-visible prompt byte limit exceeded")
                worst_next = (
                    2 * MAX_PROMPT_BYTES * config.input_usd_per_million
                    + MAX_COMPLETION_TOKENS * config.output_usd_per_million
                ) / 1_000_000
                if (
                    _estimated_cost(**totals, config=config) + worst_next
                    >= config.max_estimated_usd
                ):
                    raise RuntimeError("suite estimated cost ceiling reached before next call")
                capture_id = capture.begin_decision(messages=messages, tools=TOOLS)
                with langfuse.start_as_current_observation(
                    as_type="generation",
                    name=f"retrieval-decision-{turn + 1}",
                    model=config.model,
                    input=messages,
                    metadata={"trailgate_capture_id": capture_id},
                    model_parameters={
                        "max_completion_tokens": MAX_COMPLETION_TOKENS,
                        "reasoning_effort": "none",
                    },
                ) as generation:
                    expected_ids.add(generation.id)
                    response = client.chat.completions.create(
                        model=config.model,
                        reasoning_effort="none",
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="required"
                        if turn == 0
                        else ("none" if turn == MAX_MODEL_CALLS_PER_TASK - 1 else "auto"),
                        parallel_tool_calls=False,
                        max_completion_tokens=MAX_COMPLETION_TOKENS,
                    )
                    calls_made += 1
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
                tool_calls = assistant.get("tool_calls") or []
                if not tool_calls:
                    answer = assistant.get("content") or ""
                    root.update(output={"answer": answer})
                    break
                if len(tool_calls) != 1:
                    raise RuntimeError(
                        "parallel tool calls were returned despite parallel_tool_calls=False"
                    )
                call = tool_calls[0]
                name = call["function"]["name"]
                result = search.execute(name, call["function"]["arguments"])
                delivered = json.dumps(result, ensure_ascii=False)
                with langfuse.start_as_current_observation(
                    as_type="tool", name=name, input=call["function"]
                ) as observation:
                    expected_ids.add(observation.id)
                    observation.update(output=result)
                capture.delivered_tool_result(
                    capture_id,
                    call_id=call["id"],
                    observation_id=observation.id,
                    value=delivered,
                    truncated=bool(
                        result.get("truncated_right") or result.get("truncated_by_display_budget")
                    ),
                )
                messages = [
                    *messages,
                    assistant,
                    {"role": "tool", "tool_call_id": call["id"], "content": delivered},
                ]
    finally:
        search.close()
        langfuse.flush()

    # Gold and support data are inspected only after the model and tool loop ends.
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
    # Scores can appear after observation ingestion. First archive the rollout;
    # reconcile the delayed score without making another model call.
    snapshot = _save_snapshot(langfuse, trace_id, expected_ids, started, ended, out)
    archive = out / "archive"
    ingest_snapshot(snapshot, archive)
    report = public_report(archive)
    result = {
        "task_id": task["id"],
        "trace_id": trace_id,
        "model": config.model,
        "calls": calls_made,
        "tool_counts": search.counts,
        "generations": report["generations"],
        "faithful_candidates": report["faithful_candidates"],
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


def _reconcile_score(langfuse: Any, task_dir: Path) -> int:
    state = json.loads((task_dir / "run_state.json").read_text(encoding="utf-8"))
    refreshed = task_dir / "rescored"
    suffix = 2
    while refreshed.exists():
        refreshed = task_dir / f"rescored-{suffix}"
        suffix += 1
    refreshed.mkdir(mode=0o700)
    _private_bytes(refreshed / "capture.jsonl", (task_dir / "capture.jsonl").read_bytes())
    snapshot = _save_snapshot(
        langfuse,
        state["trace_id"],
        set(state["expected_observation_ids"]),
        datetime.fromisoformat(state["started"]),
        datetime.fromisoformat(state["ended"]),
        refreshed,
        {state["score_id"]},
    )
    archive = refreshed / "archive"
    ingest_snapshot(snapshot, archive)
    return public_report(archive)["scores"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Official MuSiQue v1.0 ZIP")
    parser.add_argument("--db", type=Path, required=True, help="Frozen train-only FTS5 index")
    parser.add_argument(
        "--task-manifest", type=Path, required=True, help="Frozen 35-task pilot IDs JSON"
    )
    parser.add_argument("--out", type=Path, required=True, help="New private output directory")
    parser.add_argument(
        "--limit", type=int, default=6, help="Run the first 1–6 representative tasks"
    )
    parser.add_argument("--model", choices=MODEL_RATES, default="gpt-6-luna")
    args = parser.parse_args()
    out = args.out.absolute()
    if out.exists() or not out.parent.is_dir():
        parser.error("--out must be a new directory under an existing parent")
    if not 1 <= args.limit <= len(CELL_REPRESENTATIVE_INDICES):
        parser.error("--limit must be between 1 and 6")
    input_rate, output_rate = MODEL_RATES[args.model]
    config = ModelConfig(
        args.model, input_rate, output_rate, 0.5 if args.model == "gpt-6-luna" else 3.0
    )
    if _sha256(args.archive) != SOURCE_SHA256 or _sha256(args.db) != INDEX_SHA256:
        raise RuntimeError("MuSiQue ZIP or frozen index hash differs from the pilot manifest")
    tasks = _load_tasks(args.archive, args.task_manifest)[: args.limit]
    os.environ["LANGFUSE_BASE_URL"] = LOCAL_LANGFUSE_URL
    from langfuse import get_client
    from openai import OpenAI

    langfuse = get_client()
    if not langfuse.auth_check():
        raise RuntimeError("local Langfuse authentication failed; no model call made")
    client = OpenAI(max_retries=0, timeout=25.0)
    out.mkdir(mode=0o700)
    _private_json(
        out / "manifest.json",
        {
            "source": "MuSiQue-Ans official train",
            "source_sha256": SOURCE_SHA256,
            "index_sha256": INDEX_SHA256,
            "task_ids": [task["id"] for task in tasks],
            "selection_indices": CELL_REPRESENTATIVE_INDICES[: args.limit],
            "model": config.model,
            "max_model_calls_per_task": MAX_MODEL_CALLS_PER_TASK,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "max_prompt_bytes": MAX_PROMPT_BYTES,
            "max_estimated_usd": config.max_estimated_usd,
            "input_usd_per_million": config.input_usd_per_million,
            "output_usd_per_million": config.output_usd_per_million,
            "rights_status": "diagnostic_only",
        },
    )
    totals = {"prompt_tokens": 0, "completion_tokens": 0}
    results = []
    for task in tasks:
        result = _run_task(client, langfuse, task, args.db, out / task["id"], totals, config)
        results.append(result)
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "task_id",
                        "trace_id",
                        "calls",
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
        "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "estimated_usd_at_uncached_text_rates": round(_estimated_cost(**totals, config=config), 8),
        "hard_code_ceiling_usd": config.max_estimated_usd,
        "structurally_faithful_decisions": sum(row["faithful_candidates"] for row in results),
        "generations": sum(row["generations"] for row in results),
        "weak_gold_string_matches": sum(row["gold_string_present_weak"] for row in results),
        "scores_captured": sum(row["scores_after_reconciliation"] for row in results),
        "rights_status": "not_attested; diagnostic only",
    }
    _private_json(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
