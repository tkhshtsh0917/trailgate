# Data protection and failure boundaries

[日本語](ja/SECURITY.md)

Trailgate handles prompts, tool results, and model outputs as potentially sensitive local data. Its core performs no model call or dataset upload. An explicit `pull` uses Langfuse GET endpoints. Saved source bytes and content-bearing exports remain local; the caller controls retention, sharing, and deletion.

| Threat or failure | v1 behavior | Residual responsibility |
|---|---|---|
| Credential disclosure | Keys are read from process environment for `pull`; normal errors and reports omit them. Redirects and ambient HTTP proxies are disabled; HTTP is permitted only on loopback. | Protect shell history, process environment, host, Langfuse account, and network. Use a trusted HTTPS endpoint. |
| Sensitive content in Git or package | Private output names are Git-ignored; outputs use private POSIX modes. `scripts/check_public_files.py` rejects suspicious candidate paths before reading and checks public text and build member names. | This heuristic cannot detect every secret. Review staged files and release artifacts manually. Never commit real traces, corpora, or declarations tied to them. |
| False training eligibility | Dataset export checks structural capture, supported Chat shape, manual quality selection, use attestation, privacy assertion, and task split. Missing evidence excludes the affected decision. | Assertions do not prove rights, correctness, privacy, or capture timing. Review source terms, inputs, outputs, and downstream training setup. |
| Modified or partial source | API pages, blob objects, archive rows, and output hashes are checked at their respective boundaries. Pull resumes only matching scopes/checkpoints; blob resume rejects changed objects. | A source system can omit late-arriving data. A self-declared complete snapshot is bounded to its recorded query and collection time. |
| Resource exhaustion | API pages, snapshots, capture events, JSONL rows, in-memory views, and CLI output have documented limits. Large archives retain streaming report/inspect access. | Large graph or dataset exports require smaller source windows. Disk usage of retained raw data remains the operator's responsibility. |
| Malicious content inside a trace | Trace text is treated as data, not instructions. Core transforms do not execute tool calls, deserialize Python objects, or invoke an LLM. | An application that later feeds exported text to a model needs its own prompt-injection controls. |

Normal CLI reports show counts instead of unknown source field names, and duplicate JSON key errors omit the key text. Source IDs, local paths, and output paths can still appear in diagnostics; keep those identifiers free of sensitive text. JSON Schema validation is a structural check and does not replace Trailgate's semantic validation or a human review. `episodes.jsonl`, graph exports, and candidate SFT rows are private diagnostics even when they contain plausible model turns. When evidence is absent, retain the original archive and investigate; do not mark a row eligible by editing the manifest.

## Dependency and compressed-response controls

The core package has no runtime dependencies. The committed `uv.lock` includes the optional `bulk-parquet` and `live` extras and the development group. As of the current lockfile, neither `brotli` nor `brotlicffi` is present. Therefore CVE-2026-111497 does not require a Brotli package update in Trailgate. The `pull` transport requests identity encoding and rejects any compressed HTTP response before reading its body; it does not invoke a Brotli decoder. The offline dependency check rejects a future lockfile containing `brotli<1.2.1` or `brotlicffi<1.2.1.0`. These floors follow the [reviewed Python advisory](https://github.com/advisories/GHSA-9v8h-qqgm-4m35) and [upstream Brotli security release](https://github.com/google/brotli/releases/tag/v1.2.1).

CI also runs `uv audit --locked` against known advisories for all locked extras and groups on changes and weekly; Dependabot proposes uv and GitHub Actions updates. The audit needs OSV network access and fails if that service is unavailable; an offline lockfile check cannot replace it. Neither check covers Brotli embedded in a host runtime, local Langfuse deployment, or unrelated applications; operators must update those separately where affected.
