# Trailgate development rules

- Keep `src/trailgate/` offline and provider-neutral where possible. Put opt-in model calls and corpus-specific harnesses in `examples/`; tests must use fictional data.
- Use uv with the committed lockfile and Python version. Run Ruff check/format, unit tests, build, and the public-file check after changes. Add regression tests for bugs that affect capture fidelity, safety, or compatibility.
- Give modules, classes, and functions one clear responsibility. Separate setup, validation, transformation, and output blocks with blank lines where that makes the flow easier to scan. Keep shared example helpers separate from executable diagnostics.
- Preserve original API response bytes and provenance. Never infer exact model-visible context from a Langfuse observation alone, and never treat a structural capture check as an answer-quality or rights decision.
- Do not read, print, stage, or commit credentials, environment files, private trajectories, or real corpus text. The release checker rejects sensitive filenames before reading content.
- Keep public documentation accurate and useful from a fresh checkout. Record completed behavior in the README; keep future features in `docs/ROADMAP.md`. Keep implementation comments short and about behavior or constraints.
- Make commits only when authorized. Treat pushing, tagging, and publishing as separate steps.
