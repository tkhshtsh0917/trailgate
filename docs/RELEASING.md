# Release checklist

Trailgate's first public milestone is a source repository preview. A Git commit, a GitHub push, a GitHub release, and a package-index publication are distinct actions. Carry out only the actions authorized for the task.

## Before the initial commit

1. Run `uv sync --locked` in a clean checkout and follow the synthetic quickstart in [README](../README.md). No model API key or Langfuse server is needed.
2. Run `uv run --locked ruff check src tests examples scripts`, `uv run --locked ruff format --check src tests examples scripts`, and `uv run --locked python -m unittest discover -s tests -q`.
3. Run `uv build`, then `uv run --locked python scripts/check_public_files.py`. The check reviews Git candidates and build member **names**. It rejects sensitive names before opening their files and reports only rule names and line numbers for content findings. It is a guardrail, not a guarantee that all secrets or personal data were found.
4. Inspect `git status --short --untracked-files=all` and `git diff --cached --name-only`. Ensure no private capture, credential file, real trajectory, dataset text, database, or machine-specific path is staged. Verify the commit name and email, using a repository-local noreply identity if desired.
5. Confirm the README states exactly what v0.1.0 implements. `ROADMAP.md` describes planned features, not completed ones.

## Before a later push or tag

Run the same checks from a fresh checkout of the candidate commit. Confirm CI has passed, inspect the release artifact members and package metadata, then create a tag or publish only when separately authorized. Do not include optional live-run output in a release artifact.
