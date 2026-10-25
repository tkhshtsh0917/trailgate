# Local enriched blob intake

[日本語](ja/BLOB.md)

Mirror a complete Langfuse blob-export run under one local object root, keeping each object's **full key** as its relative path. Point `--manifest` to that run's manifest:

```sh
uv run --locked trailgate ingest-blob \
  --object-root private-objects \
  --manifest private-objects/PROJECT_ID/manifests/RUN.json \
  --out private-bulk-archive
uv run --locked trailgate report private-bulk-archive
```

Trailgate follows `files[].key` from the manifest; it never guesses table filenames. It currently accepts manifest version 1 with `exportSource: EVENTS` and both `observations_v2` and `scores`, using JSONL, gzip JSONL, or Parquet. CSV, JSON arrays, legacy observations/traces, and a dual-source manifest fail visibly. Parquet is optional: `uv sync --locked --extra bulk-parquet` installs PyArrow. The [Langfuse blob export documentation](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage) describes completion, field groups, and format differences.

Every referenced object must exist within the root and match its declared `sizeBytes`; text files must match `rowCount`. Raw bytes and SHA-256 hashes are retained. Normalization streams records through a private SQLite spool and sorts output by timestamp and ID. A JSONL row is limited to 16,777,216 decoded characters. Identical duplicate IDs are collapsed; conflicting contents fail. If processing stops, rerun with `--resume` and the same manifest and output path. An unchanged input produces the same normalized hashes; a changed manifest or object fails, including after the spool is finalized but before the archive is renamed. A bulk archive with no model-boundary capture yields a structural summary and no faithful training candidates.

The synthetic 100,000-observation benchmark can be rerun with `uv run --locked python scripts/benchmark_bulk.py --rows 100000`. Add `--observations-per-trace 1` to measure 100,000 distinct traces. It measures peak process RSS and elapsed time for import plus summary; it does not model large individual payloads or remote object-store transfer. Bulk summaries and `inspect` stream verified rows. The exact bulk trace count uses a temporary private SQLite index, requiring disk space proportional to the number of distinct traces; the index is removed on normal exit. In-memory graph, audit, and dataset views stop when the archive has more than 20,000 observations or 32 MiB of normalized rows plus capture; use smaller source windows for these views. Raw Parquet remains the source of truth; normalizing typed timestamps and decimal values to JSON can change their presentation. Inspect raw rows when exact numeric representation matters.
