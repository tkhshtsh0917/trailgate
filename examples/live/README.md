# Opt-in live diagnostics

These scripts run only when called explicitly. `validate_pull.py` creates fictional local Langfuse telemetry but makes no model call. The other scripts may call a model API. They keep captured inputs, tool results, provider responses, and Langfuse snapshots in a new private output directory. They are diagnostic examples, not training datasets or benchmark results. See the [main README](../../README.md) for scope and limits.

| File | Responsibility |
|---|---|
| `validate_pull.py` | Create one fictional trace and score, then check API pull against offline import without a model call |
| `live_langfuse_smoke.py` | Two-call run over fictional local notes |
| `live_musique_pilot.py` | Bounded Chat Completions multi-hop pilot |
| `live_musique_responses.py` | Provider-native Responses pilot |
| `musique_corpus.py` | Frozen MuSiQue task selection and local retrieval tools |
| `langfuse_snapshot.py` | Local Langfuse API snapshot and delayed-score reconciliation |

The core package does not import these scripts. Tests import their modules without running a model or reading credentials.

To validate a local Langfuse v4.59.0+ instance with the locked Python SDK v5, provide `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in the process environment and run `uv run --locked --extra live python examples/live/validate_pull.py --out private-pull-validation`. The script forces `http://127.0.0.1:3000`, writes one fictional trace and score, and prints counts and a snapshot hash only. The output is private and ignored by Git. Earlier Langfuse v4 servers require Python SDK v4 rather than the current locked live extra. If using an environment file, pass it to your process runner; do not commit it.
