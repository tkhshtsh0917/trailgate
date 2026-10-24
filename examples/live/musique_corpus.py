"""Frozen MuSiQue train corpus, task selection, and bounded local retrieval tools."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from trailgate.private_io import _private_bytes

SOURCE_MEMBER = "data/musique_ans_v1.0_train.jsonl"
SOURCE_SHA256 = "98f839bf2fd5319f5c688aed77901a6d5c30b3b9f9f691ab9a8ecafb045ee0cd"
INDEX_SHA256 = "92391a8bcd1ea9a26ca8717a7661480583eec30c03843066e867db62c1145045"
CELL_REPRESENTATIVE_INDICES = (0, 8, 16, 24, 26, 34)
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
