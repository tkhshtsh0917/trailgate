# Opt-in live diagnostics

These scripts call a model API only when run explicitly. They keep captured inputs, tool results, provider responses, and Langfuse snapshots in a new private output directory. They are diagnostic examples, not training datasets or benchmark results. See the [main README](../../README.md#opt-in-live-examples) for setup and limits.

| File | Responsibility |
|---|---|
| `live_langfuse_smoke.py` | Two-call run over fictional local notes |
| `live_musique_pilot.py` | Bounded Chat Completions multi-hop pilot |
| `live_musique_responses.py` | Provider-native Responses pilot |
| `musique_corpus.py` | Frozen MuSiQue task selection and local retrieval tools |
| `langfuse_snapshot.py` | Local Langfuse API snapshot and delayed-score reconciliation |

The core package does not import these scripts. Tests import their modules without running a model or reading credentials.
