# Episode graph

`trailgate export graph ARCHIVE --out private-graph.json` writes a private `trailgate.episode_graph.v1` graph. The graph keeps source IDs and references for episodes, traces, generation decisions, tool actions, delivered values, observation parent links, and score subjects. It includes captured prompts and tool results; handle it as private data.

An episode is a **group with an unknown boundary**. Observations with one consistent nonempty session ID are grouped by project and session, including across trace IDs. Otherwise they are grouped by project and trace. This grouping does not assert that a task began or ended there. `history_relation` and `context_compaction` remain `unknown`: Trailgate preserves each captured prefix exactly and never fills gaps from neighboring observations. Repeated text is not deduplicated.

`raw_capture_order` records the sidecar's line order. Decision and action links represent only recorded relationships: a generation's capture ID, its assistant's tool-call ID, a delivered-result record, and the referenced Langfuse TOOL observation. Langfuse parent links have their own relation label and are not promoted to model-visible history. Multiple calls from one decision remain separate actions. A repeated call has `retry_relation: unknown` unless the application later supplies stronger evidence. Missing results, aborted decisions, unresolved score subjects, and orphan TOOL observations remain visible in the graph.

Scores link to their declared subjects as later evidence. They do not enter a decision's historical `visible_prefix` and do not prove answer correctness. The raw provider response, when captured, is retained as an opaque payload on its decision; a Responses payload is not converted into Chat messages.
