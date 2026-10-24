# v1 contracts and migration

Trailgate 1.0.0 freezes the **documented** CLI and Python entry points, not internal functions prefixed with `_`. A major version may remove or change a documented contract. A minor version may add commands, fields, and schemas without changing the meaning of existing fields. Patch versions fix defects. Consumers should ignore unknown object fields and reject an unknown `schema_version`; they should not assume episode boundaries or model context absent from the capture.

## Supported interfaces

| Interface | v1 commitment |
|---|---|
| CLI | `doctor`, `ingest`, `pull`, `ingest-blob`, `capture-recover`, `report`, `inspect`, `evaluate dataset`, and `export steps/graph/sft/dataset` with documented options and exit status `0` on success, `2` on validation or I/O failure. Normal diagnostics omit captured content. `export sft` remains explicitly candidate-only. |
| Python | `trailgate.capture.CaptureWriter`; `trailgate.snapshot.{read_snapshot,ingest_snapshot,SnapshotError}`; `trailgate.archive.{read_archive,iter_archive_rows}`; `trailgate.pull.{PullSpec,preview_pull,pull_snapshot}`; `trailgate.bulk_archive.ingest_blob_export`; `trailgate.episode_graph.build_episode_graph`; `trailgate.dataset_assessment.evaluate_dataset`; `trailgate.dataset_export.export_dataset`; `trailgate.schemas.{SCHEMA_FILES,load_schema}`. Callers should use keyword arguments where shown in the guides. |
| Files | Saved API snapshot v1, capture v1, archive v1/v2, episode graph v1, use manifest v1, dataset policy v1, dataset manifest v1, Chat SFT v1, and diagnostic episode rows v1. See [schemas](#published-json-schemas). |

`read_archive` and graph/audit/dataset materialization are bounded in-memory views. `iter_archive_rows`, bulk `report`, and `inspect` support larger verified archives. The bounds and exclusions are part of the v1 contract; the implementation may raise them in a minor release. Output paths must be new. `export graph`, candidate SFT, and dataset files can contain private content.

## Published JSON Schemas

The nine Draft 2020-12 JSON Schemas are shipped inside the wheel and sdist. Consumers can use `from trailgate.schemas import SCHEMA_FILES, load_schema`, then `load_schema("dataset-manifest-v1.json")`. They are also readable under `src/trailgate/schemas/` in a checkout. No runtime JSON Schema dependency is needed; schema validation in the project tests uses a development dependency.

Schema validation checks required envelopes and selected field types. Trailgate's Python validators additionally check cross-file hashes, cursor termination, ID uniqueness, time windows, linkage, split consistency, and use declarations. A schema-valid file does **not** prove completeness, quality, privacy, legal rights, or eligibility. The legacy fictional fixture has unversioned capture events; the importer accepts those, while the published capture v1 schema describes events written by `CaptureWriter`.

## Migration from v0.2.0

No destructive archive rewrite is required. Existing `trailgate.archive.v1` API archives and `trailgate.archive.v2` blob archives remain readable if their raw files and hashes verify. Keep the original source snapshot or mirrored blob run: it is the recovery path if a later adapter changes. To regenerate a dataset, rerun `evaluate dataset` and `export dataset` into a **new** output directory with rights and policy declarations bound to the archive's `snapshot_id`. Check the new manifest and counts before replacing downstream references. Never edit an archive index, raw bytes, or its hash fields in place.

The v0.2 `export sft` output is still candidate-only and must not be relabeled as the v1 gated Chat SFT view. A consumer expecting `trailgate.chat_sft.v1` must read `export dataset`'s `chat_sft.jsonl`. `episodes.jsonl` is always diagnostic. Capture events from the application sidecar and Langfuse events may be incomplete; the importer records uncertainty instead of filling gaps. New schema versions will be introduced for incompatible file changes; future readers will either accept them explicitly or fail with a clear unsupported-version error.

## Tested compatibility

| Surface | Tested locally | Contract and limits |
|---|---|---|
| Python | CPython 3.12.13 with the full local suite; 3.13.15 and 3.14.7 with clean wheel installs and fictional CLI smoke checks | Declared range is `>=3.12.13,<3.15`; CI runs the full suite on all three. Core is standard-library only. Optional Parquet requires `pyarrow>=26,<27` and runs in CI on 3.12.13. |
| Langfuse self-hosted | 4.56.0 with Observations v2 / Scores v3, fictional trace and score | REST read path was exercised against this server. Other server versions implementing the same endpoints have not been live-tested here. [Langfuse versioning](https://langfuse.com/self-hosting/upgrade/versioning) documents endpoint availability. |
| Langfuse Python SDK | 4.18.0 in optional local diagnostics | The core collector uses REST and has no SDK dependency. SDK 5 was not tested; the local server version is below Langfuse's documented SDK 5 minimum of 4.59. |
| Enriched blob export | Manifest v1, `EVENTS`, `observations_v2` and `scores`; JSONL, gzip JSONL, Parquet fictional fixtures | Offline conformance, resume, and resource tests passed. A production Langfuse blob-export run was not verified. See [bulk intake](BLOB.md). |

The compatibility table states observations, not a promise about all future Langfuse versions. Each new server or SDK combination should run the same synthetic integration fixture and be added only after verification.
