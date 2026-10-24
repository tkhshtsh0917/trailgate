# Trailgate

**Stitch agent traces into auditable trajectories and export eligible training data, starting with Langfuse.**

See the [Japanese README](README.ja.md) and [roadmap to v1.0.0](docs/ROADMAP.md) for the current preview scope and planned release gates.

Trailgate is an early local-first Python CLI and library for preparing agent trajectories. It imports saved [Langfuse Observations API v2 and Scores API v3](https://langfuse.com/docs/api-and-data-platform/features/public-api) response pages, retains their original bytes, and reports which generation decisions have enough capture evidence for a faithful SFT projection.

The core CLI is an **offline prototype**. Separate opt-in examples can run a fictional local search and a bounded multi-hop QA diagnostic against an official MuSiQue train corpus. These examples record private trajectories in a self-hosted Langfuse instance. Structural capture checks do not establish answer correctness or training rights.

## Project layout

| Location | Purpose |
|---|---|
| `src/trailgate/` | Offline snapshot validation, archive storage, capture, audit, export, and CLI |
| `examples/synthetic/` | Fictional input and rights fixture for reproducible local checks |
| `examples/live/` | Explicitly run model and Langfuse diagnostics; shared corpus and snapshot helpers |
| `tests/` | Offline regression tests |
| `docs/` | [Roadmap](docs/ROADMAP.md) and [release checklist](docs/RELEASING.md) |

## Try the synthetic example

The project pins Python 3.12.13 in `.python-version`. From the repository root, create the environment and run the synthetic example with [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked
uv run trailgate doctor --source langfuse --input examples/synthetic/snapshot
uv run trailgate ingest langfuse-api-snapshot examples/synthetic/snapshot --out ./private-example
uv run trailgate report ./private-example
uv run trailgate inspect ./private-example --trace trace-example
uv run trailgate export steps ./private-example --out ./private-steps.jsonl
uv run trailgate export sft ./private-example --rights-file examples/synthetic/rights.json --use internal_training --out ./private-sft.jsonl
```

The `private-*` outputs are ignored by Git. The archive directory is created with mode `0700`; raw and derived files use `0600`. Trailgate refuses to overwrite an existing archive or export.

The `uv run trailgate` command uses the package entry point in `pyproject.toml`.

## Opt-in live examples

The live examples are separate from the CLI and are never run by tests. They use the official OpenAI SDK and Langfuse Python SDK as optional dependencies. They require a self-hosted Langfuse at `http://127.0.0.1:3000`, with a project API key pair. Put `OPENAI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` in a local environment file **yourself**; do not commit or paste that file. The examples do not print key values and force Langfuse telemetry to the local URL. `*.env` and `.env*` are ignored by Git.

```sh
uv sync --locked --extra live
uv run --offline --locked --extra live --env-file local-langfuse.env \
  python examples/live/live_langfuse_smoke.py --out private-live-run
uv run --offline --locked trailgate report private-live-run/archive
```

The example performs at most **two** `gpt-6-luna` Chat Completions requests, with at most 256 completion tokens each and retries disabled. Its only tool is a deterministic search over four fictional notes; no paid retrieval tool is involved. It checks Langfuse authentication before any model call. The code stops if the measured token cost estimate reaches $0.01 between calls; this is an estimate based on full uncached text rates, not a provider billing cap. A failed or timed-out call may have an unknown charge. The output directory is private and contains the full provider responses, exact model request messages, tool delivery, Langfuse API snapshot, and Trailgate archive. It contains **no API key**. Review the contents before sharing. The `rights_status` is diagnostic only; do not export it as training data without an independent rights decision.

For multi-hop QA, `examples/live/live_musique_pilot.py` runs six frozen 2-, 3-, and 4-hop MuSiQue-Ans **train** questions against a frozen local SQLite FTS5 index, using `search`, `read`, and literal `grep`. `examples/live/live_musique_responses.py` runs a separate Responses API diagnostic. Both verify source and index hashes before calling a model, impose per-task action and token limits, and keep task answers and official support labels out of model inputs. Supply the official MuSiQue ZIP, the frozen index, and the task-ID manifest with `--archive`, `--db`, and `--task-manifest`; use `--out` for a new private directory. These examples are diagnostic harnesses, not benchmark scores or a general MCP implementation.

The Responses API example preserves provider-native request and response items, including encrypted reasoning items needed for stateless replay. Trailgate audits their capture structure but currently **does not export them as Chat SFT rows**. The MuSiQue outputs are marked `not_attested; diagnostic only`: no real-data training or redistribution is asserted. Gold-string presence is a weak diagnostic; evidence sufficiency and semantic correctness require separate review.

## What is checked

The importer accepts **saved API response pages**, one JSON object per line in `observations.jsonl` and `scores.jsonl`. Each page has a `data` array and `meta.cursor`. The last page must have a null or absent cursor. A `manifest.json` declares the API versions, selected field groups, timezone-aware observation and score query ranges, and `complete: true`. This declaration is supplied by the snapshot creator; Trailgate checks that every non-final page advertises a next cursor but cannot prove that no pages were skipped or that the original query captured every relevant trace.

The current audit treats each Langfuse `GENERATION` as a decision candidate. It requires:

- a unique `trailgate_capture_id` in the generation's metadata;
- exactly one locally captured pre-inference message list and tool list, including an explicit empty list when no tools were available;
- exactly one captured assistant response;
- tool calls that name a declared tool, with a matching locally recorded delivered result and a Langfuse `TOOL` observation in the same trace;
- capture events in the order input → response → delivered tool result.

The report lists rejection reasons per generation. A Langfuse observation's `input` field is **never assumed to be the exact model-visible prompt**. Scores are retained as separate evidence and are never inserted into the model's historical input.

`faithful_candidates` means the structural capture checks passed. It is **not a quality score or a legal determination**. SFT export also requires an explicit use manifest that attests permission for task inputs, model outputs, and tool results. The example use manifest applies only to the fictional fixture. Tool messages and user messages remain context; the export's `target` identifies only the final assistant message for loss.

## Capture at the model boundary

The optional local helper records exactly what the application passes to its model. Its payload stays in a private JSONL file. The application attaches only the returned correlation ID to a Langfuse generation:

```python
from trailgate.capture import CaptureWriter

capture = CaptureWriter("private-capture.jsonl")
capture_id = capture.begin_decision(messages=actual_messages, tools=actual_tool_schemas)

# Attach {"trailgate_capture_id": capture_id} to this Langfuse generation.
# Invoke your own model, then record its actual assistant message.
capture.finish_decision(capture_id, assistant_message=actual_assistant_message)

# For each executed call, record the value actually handed back to the model.
capture.delivered_tool_result(
    capture_id,
    call_id="call-1",
    observation_id="langfuse-tool-observation-id",
    value=shown_to_model,
)
```

Capture is explicit because prompts, tool results, and user data can be sensitive. The helper does not call or modify any model or Langfuse API. The caller is responsible for invoking it at the actual model boundary.

## Current boundaries

- The importer targets Langfuse API response pages, not UI exports or enriched blob exports. The [Langfuse API docs](https://langfuse.com/docs/api-and-data-platform/features/public-api) explain the v2/v3 field groups and cursor pagination; request `io` and `metadata` for observations and `subject` for scores when collecting a useful snapshot.
- Trace observations and scores are preserved with source row references and SHA-256 hashes. Session stitching, full episode boundaries, incremental API pulling, and score attribution are not implemented yet.
- The capture helper provides an application-side assertion of model-visible state. Trailgate cannot independently prove that the application called it at the right point or that its rights declaration is accurate.
- The capture writer currently expects one process to append to a sidecar. Concurrent writers, redaction, and authenticated capture records are future work.
- The SFT projection preserves captured message objects and declares the target role. Model-specific chat templates, tokenization, loss masks, and on-policy RL rollout data are outside this prototype.
- Provider terms, third-party tool-result rights, redaction, and task-level splits require review before any real-data training or publication.

## Development

```sh
uv run --locked ruff check src tests examples scripts
uv run --locked ruff format --check src tests examples scripts
uv run python -m unittest discover -s tests -v
uv build
uv run --locked python scripts/check_public_files.py
```

The synthetic fixture and local pilot establish only the documented v0.1.0 capture and audit behavior. The next milestone is a canonical episode graph; [docs/ROADMAP.md](docs/ROADMAP.md) lists its acceptance gates. Design history and the broader retrieval-subagent research plan live outside this code repository.

MIT licensed. See [LICENSE](LICENSE).
