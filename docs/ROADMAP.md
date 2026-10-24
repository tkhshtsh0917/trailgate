# Roadmap to Trailgate v1.0.0

Updated: 2026-10-25. This is a release roadmap for **Trailgate the library and CLI**. It does not depend on training a retrieval model or publishing any real trajectory data. Versions below describe capability milestones. A local commit, GitHub push, version tag, and package release are separate actions; the package version alone does not imply a published release.

## Product contract for v1.0.0

Trailgate prepares trajectories from a user's own Langfuse project. It obtains or imports a bounded source snapshot, preserves source evidence, reconstructs the observable agent run, and explains which decisions can be exported to a specified training view. A useful result is a **reproducible eligibility decision with reasons**, not a JSON conversion alone.

For v1, the supported source is Langfuse. The read contract is [Observations API v2 and Scores API v3](https://langfuse.com/docs/api-and-data-platform/features/public-api), plus complete [enriched blob export manifests](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage) for bulk intake. Langfuse distinguishes [observations, traces, and sessions](https://langfuse.com/docs/observability/data-model); Trailgate must not silently equate any one of those with a complete episode. Server and SDK versions supported in practice will be listed in a tested compatibility matrix. In particular, Langfuse Python SDK v4 and v5 use different score-read accessors, so the collector must keep that difference inside its adapter or use the stable REST endpoints.

The v1 outputs are (1) an immutable local source snapshot, (2) a versioned episode/decision/action/observation view with source references, (3) an audit report separating structural fidelity, quality signals, rights, and missing evidence, and (4) opt-in generic episode and **supported-format** SFT exports. A Langfuse score is an attributed signal, not proof that every action was correct. A provider-native Responses capture is not automatically a Chat SFT example.

The core package does not run models, fine-tune them, judge answer quality, or send datasets to a training provider. It does not claim that a trace alone contains exact model-visible context. Direct Codex/Claude Code log ingestion, multi-source connectors, preference/RL-ready export, and automated legal clearance are outside v1.

## Milestones

| Milestone | Deliverable | Exit gate |
|---|---|---|
| **v0.1.0 — honest public preview** | Provide the offline snapshot importer, capture helper, decision audit, and synthetic fixture. Document the exact CLI commands, supported input shape, known exclusions, and private-by-default behavior. Keep opt-in model examples separate. | A fresh checkout can install, test, run the fixture, and build an sdist/wheel. Candidate artifacts and the Git tree contain no `.env`, private archive, dataset text, or credential. README does not imply episode stitching or online pulling exists. |
| **v0.2.0 — cumulative trajectory pipeline** | Add the episode graph, capture recovery, bounded read-only Langfuse pull, resumable enriched-blob intake, and gated dataset export together. Preserve raw evidence and separate structural fidelity, manual quality selection, rights, privacy, and split provenance. | Synthetic adversarial and pagination tests, a local self-hosted pull of fictional telemetry, deterministic dataset output, and a 100,000-observation import below 1 GiB pass. Unknown model context and episode boundaries remain explicit. |
| **v1.0.0 source candidate** | Freeze the documented Python/CLI contracts, publish JSON Schemas and migration guidance, and verify the bounded conformance and release gates. | A new user can collect or import Langfuse data, inspect eligibility, and export permitted views without running a model. Build artifacts contain only intended public files. |
| **v1.0.0 tag** | Create only after review of the source candidate and CI results. Package-index publication is a separate decision. | The tagged commit matches the reviewed candidate. |

Milestones are gates, not calendar dates. A preview may ship with explicit limitations; v1.0.0 waits for the reliability and contract gates. Features can move between pre-1.0 versions, but the exit gates for provenance, completeness, privacy, and reproducibility must remain.

## Current state and next implementation slices

The v0.2.0 milestone implemented the graph, capture recovery, read-only v2/v3 pull, resumable enriched-blob intake, and gated dataset exports. The v1 source candidate adds a cross-path conformance suite, bounded in-memory views and CLI output, an eligibility preview, public JSON Schemas, and [contracts, migration and compatibility](CONTRACT.md). Fictional fixtures cover adversarial capture states, checkpoint recovery, score revision, and split leakage. A local self-hosted Langfuse trace and score were pulled and compared with offline import without a model call. The 100,000-observation resource gate covers import and summary only. Large graph and dataset exports fail visibly at the documented limit; streaming diagnostics remain available.

The remaining release gate is a clean build and installation, complete CI on declared Python versions, public-file review, and a final human review. No new tag or package-index release is implied by a source commit.

## v1 release rules

- Source data, captures, and exports are local and private by default. The CLI must not print prompts, tool results, credentials, or raw provider payloads in normal reports.
- Every transformation records source IDs, source hashes, schema/adapter versions, filtering rules, and exact exclusions. A declared `complete` flag without verifiable collection evidence is not treated as proof of a complete episode.
- `faithful` means only that required capture relationships were observed. `eligible_for_<view>` additionally checks that view's format and declared use. Answer correctness is a separate label.
- An unknown or ambiguous relation fails closed for a faithful training view while remaining available for diagnosis.
- Provider/model terms and third-party corpus rights are supplied and reviewed by the user of Trailgate. The library can enforce a declaration; it cannot certify legal permission.
