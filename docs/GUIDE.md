# End-to-end fictional workflow

This walkthrough uses only bundled fictional records. It makes no network request or model call. Start from a fresh checkout with Python 3.12.13 and `uv`:

```sh
uv sync --locked
uv run --locked trailgate doctor --source langfuse --input examples/synthetic/snapshot
uv run --locked trailgate ingest langfuse-api-snapshot examples/synthetic/snapshot --out private-example
uv run --locked trailgate report private-example
uv run --locked trailgate inspect private-example --trace trace-example
uv run --locked trailgate export graph private-example --out private-graph.json
uv run --locked trailgate evaluate dataset private-example \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json --use internal_training
uv run --locked trailgate export dataset private-example \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json --use internal_training \
  --out private-dataset
```

The example yields two eligible Chat SFT rows for the **unchanged** fixture and one diagnostic episode row. Inspect `private-dataset/manifest.json` to see source hashes, output hashes, count, use, rights attestation, policy hash, and the view labels. `private-dataset/chat_sft.jsonl` is the gated Chat view; `episodes.jsonl` is diagnostic and must not be silently treated as training data. Repeating the export to a different empty destination with the same bytes should produce identical output files.

## From your own Langfuse project

1. Instrument the application at its model boundary with `CaptureWriter` and attach each capture ID to the matching generation. Record tool deliveries actually seen by the model. See [capture](CAPTURE.md).
2. Choose one source route: saved API pages, a bounded read-only [pull](PULL.md), or a complete local [enriched blob run](BLOB.md). Keep the raw source and the imported archive private. A bulk run without capture is still useful for diagnosis but cannot supply faithful Chat SFT decisions.
3. Use `report` and `inspect` to check missing capture, ambiguous links, and truncation. Use `export graph` only into a private location when full context is needed. Scores remain later evidence.
4. Create fresh rights and policy declarations for the archive's exact `snapshot_id`; do not reuse the fictional files. Review generation IDs, answer quality, sensitive content, and task-level splits. Preview with `evaluate dataset`, then export into a new private directory. See [dataset gates](DATASET.md).
5. Verify the manifest and selected rows before any downstream training. Trailgate does not upload, tokenize, or train a model.

## Failure examples

| Symptom | Meaning and next step |
|---|---|
| `snapshot has an unsaved next page` | A saved API page still has a cursor. Fetch the remaining pages or resume the original bounded pull. Do not set `complete: true` by hand. |
| `raw archive hash mismatch` | Raw bytes changed after import. Restore the original snapshot or create a new archive; do not patch the hash. |
| `rights snapshot_id does not match archive` | The attestation belongs to another source snapshot. Review this snapshot and make a new declaration. |
| `gate:quality_manual_selection` | The generation was not manually approved. A Langfuse score alone cannot approve it. |
| `structural:unmatched_tool_result` | A tool call lacks a verified delivered result in the captured prefix. Inspect the application instrumentation and retain the row as diagnostic. |
| `archive exceeds the in-memory view limit` | The import is valid, but graph/audit/dataset materialization is too large. Use streaming `report`/`inspect` and collect smaller time windows. |

Outputs and captures can contain private prompts and tool data. The CLI's normal report omits their contents, but `export graph`, `export sft`, and `export dataset` write them to private files. The [threat model](SECURITY.md) describes the remaining responsibilities.
