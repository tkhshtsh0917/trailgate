# Dataset eligibility and exports

`trailgate export sft` is a **candidate-only diagnostic projection** retained for v0.1 compatibility. It checks capture structure, Chat envelope, and a self-attested use manifest. It does not check answer quality, privacy, or data splits. Its rows say `eligibility: candidate_only`.

Use `trailgate evaluate dataset` to preview gate counts and reasons without writing content. Use `trailgate export dataset` for the gated training view:

```sh
uv run --locked trailgate export dataset private-archive \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json \
  --use internal_training --out private-dataset
```

The sample files apply **only to the unchanged fictional fixture**. A real export needs new reviewed declarations bound to its exact `snapshot_id`. The rights manifest grants the requested use separately for task input, model output, and tool result. The policy names manually approved generation IDs; scores alone never approve a decision. Privacy review must explicitly assert no sensitive data and `redaction: none`. Trailgate does not detect secrets or implement automatic redaction. If sensitive data may be present, the training export is blocked until it is handled outside Trailgate and a new reviewed snapshot is produced. These declarations are checks of what the user attests, not legal or privacy certification.

The policy also maps trace IDs to task IDs and `train`/`validation`/`test` splits. One task cannot span splits. For a known session, every observed peer trace must have the same task and split; missing or conflicting peer evidence blocks Chat SFT for that session. Reused trace IDs across projects are blocked because the policy cannot distinguish them. A generation missing a split, outside `train`, unapproved by manual review, structurally incomplete, or in an unsupported provider format remains in the private generic episode view with separate gate reasons; it does not enter `chat_sft.jsonl`. Provider-native responses remain opaque in the episode view and original archive. The `target` marks only the final assistant message for loss; tokenization and model-specific loss masks are outside Trailgate.

The output directory contains `episodes.jsonl`, `chat_sft.jsonl`, and `manifest.json`. `episodes.jsonl` is a diagnostic view: an observed session can include traces from multiple tasks and splits, so its rows are not marked training eligible. The session gate detects conflicts visible in the archive; it cannot prove that manually captured prompt text contains no outside data. Human review must check that boundary when selecting generations. Validated Chat rows retain captured request parameters when available. The manifest records each view's status, selection counts, rejection reasons, source and output hashes, transform schema versions, rights and policy hashes, and trace-to-task split evidence. Repeating the export with the same archive and declaration bytes produces byte-identical files. All outputs are private by default; review them before sharing or training.
