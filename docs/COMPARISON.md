# What Trailgate adds to source exports

[日本語](ja/COMPARISON.md)

The comparison uses the same fictional two-decision search fixture; it makes no claim about the quality of a real model's answer. A local cross-path test constructs saved API pages, a mocked read-only pull, and a mirrored enriched blob run from the same records. It checks normalized semantic fields, source hashes, and missing-capture behavior. The live self-hosted API was separately exercised with fictional telemetry. These checks do not establish equivalence for arbitrary Langfuse deployments or all blob formats.

| Workflow | What it yields on this fixture | Training limitation |
|---|---|---|
| Plain Langfuse API or enriched blob export | Source observations, scores, metadata, and export provenance. | An observation alone cannot establish the exact message prefix or tool result delivered to a model. A score is evidence tied to its subject, not automatic quality approval. |
| Trailgate import plus `export graph` | Source-linked decisions, tool actions and delivered values where captured; explicit unknown boundaries and missing links. | The graph contains content and remains diagnostic. It does not certify correctness or permission. |
| Trailgate `evaluate dataset` plus `export dataset` | Per-decision gate reasons, a gated Chat SFT view, diagnostic episodes, and a hash-linked manifest. | Only supported Chat shapes enter SFT. Rights, privacy, and quality remain user attestations. No training or upload occurs. |
| [smithtune](https://github.com/langchain-ai/smithtune) (local source inspected at `e5327ec6fc780732085abdf3bbbfa4489911e008`) | LangSmith-based trajectory preparation through provider training, evaluation, and optional deployment, with its own rights acknowledgement. | Different source and destination contracts; no apples-to-apples row-level result was run for this Langfuse fixture. |

The comparison is about scope and evidence. It is not a performance benchmark or a claim that one project replaces another. Trailgate's v1 boundary ends at a local, auditable export.
