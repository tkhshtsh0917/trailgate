# Trajectory formats and interoperability

Trailgate v1.0.0 uses a versioned, provenance-rich **private archive** as its source of truth. The episode graph and dataset files are derived views. There is no single format that simultaneously standardizes agent telemetry, faithful trajectory interchange, SFT examples, and RL experience. Replacing the archive with a training format would lose the original Langfuse bytes and uncertainty about episode boundaries or model-visible context.

| Format | Role | Fit for Trailgate v1 |
|---|---|---|
| [Agent Trajectory Interchange Format (ATIF)](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md) | A JSON trajectory interchange specification used by Harbor; its RFC described ATIF-v1.8 at this review (2026-10-25). | Closest candidate for a future optional full-trajectory export. It describes a complete, ordered interaction history, which Langfuse observations alone do not establish. Trailgate does not currently emit ATIF. |
| [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md) and [OpenInference](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md) | Vocabulary for instrumenting and interpreting agent, model, retrieval, and tool spans. | Useful for future intake adapters; neither is itself a reviewed training dataset or proof of exact model-visible content. Trailgate v1 reads Langfuse records, not OTLP or OpenInference directly. |
| [TRL conversational datasets](https://huggingface.co/docs/trl/dataset_formats) | Trainer-facing `messages` and `tools` examples, with task-specific prompt/completion or preference forms. | The gated `chat_sft.jsonl` has `messages` and `tools`, plus Trailgate provenance and gate fields. It is **not certified as drop-in TRL input**: Trailgate preserves function arguments as a JSON string while the TRL example uses an object; the chosen model's chat template and final-assistant loss mask also need separate validation. |
| [RLDS](https://github.com/google-research/rlds/blob/main/README.md) | Sequential environment episodes and steps, including action and reward fields when available. | A downstream RL adapter would need actual episode boundaries and reward semantics. Trailgate v1 does not create rewards or RL rollouts. |

## Current contract

- Keep the immutable source archive and its hashes authoritative. Never reconstruct missing user turns, tool deliveries, hidden reasoning, rewards, or terminal states for the sake of matching an external format.
- Use `export dataset` for the gated Trailgate Chat SFT view. `export sft` is an older candidate-only diagnostic. `episodes.jsonl` remains diagnostic even when an episode contains a selected decision.
- Preserve source IDs and declared exclusions when building any downstream adapter. A converter must reject an unsupported provider shape or missing evidence instead of silently repairing it.

## Interoperability gate after v1

An opt-in ATIF adapter is worthwhile once a source can prove ordered user, agent, and tool events plus a complete episode boundary. It should pin an ATIF schema version, validate against Harbor's reference model, carry Trailgate source references in extension fields, and visibly reject incomplete episodes. A TRL adapter should separately validate the exact target model's tokenizer and chat template, transform tool arguments only when semantics are preserved, and define which assistant tokens receive loss. Neither adapter is part of the v1.0.0 contract.
