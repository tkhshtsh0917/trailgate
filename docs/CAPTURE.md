# Model-boundary capture

`CaptureWriter` writes a private JSONL sidecar. Call `begin_decision` immediately before model inference with the exact messages and available tool schemas. An explicit `tools=[]` means no tools were available; an absent schema remains unknown. Attach the returned capture ID to the Langfuse generation metadata as `trailgate_capture_id`.

For gated Chat SFT, instrument Langfuse so every observation in the collected project snapshot records one consistent, nonempty session ID per trace. If any trace lacks this evidence, Trailgate blocks Chat SFT for the project snapshot because it cannot determine whether that trace belongs to a selected session. Collect a smaller, fully instrumented snapshot if needed.

After inference, call `finish_decision` with the actual assistant message and, when available, the provider-native response. If inference fails, call `abort_decision(capture_id, reason_code="timeout")`. For each executed tool call, call `delivered_tool_result` with the value the host actually gave the model and the Langfuse TOOL observation ID. The delivered value and the observation's backend output have separate provenance; Trailgate does not assume they are identical.

Capture writing requires POSIX file locking; other CLI commands can still import on non-POSIX hosts. Appends use a file lock, a size limit of 8 MiB per event, and `fsync`. A completed event is one full JSONL line. Multiple processes may append to one sidecar, but the caller must still preserve each decision's causal order. An interrupted append may leave a partial final line. `trailgate capture-recover PRIVATE-CAPTURE --out PRIVATE-RECOVERED` copies complete lines to a new private file and reports the discarded tail's hash; it never rewrites the source. A corrupt complete line fails recovery.

The sidecar is never sent by the core library. Normal reports omit prompt, provider response, and delivered-result content. A capture is an application-side assertion: Trailgate cannot prove that instrumentation ran at the right instant.
