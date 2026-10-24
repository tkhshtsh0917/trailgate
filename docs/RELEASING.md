# Release checklist

Trailgate v0.6.0 is a source milestone. A Git commit, a GitHub push, a GitHub release, and a package-index publication are distinct actions. Carry out only the actions authorized for the task.

## Before a release candidate

1. Run `uv sync --locked` in a clean checkout and follow the synthetic quickstart in [README](../README.md). No model API key or Langfuse server is needed.
2. Run `uv run --locked ruff check src tests examples scripts`, `uv run --locked ruff format --check src tests examples scripts`, and `uv run --locked python -m unittest discover -s tests -q`. Repeat the unit suite with `--extra bulk-parquet`.
3. Run `uv run --locked python scripts/benchmark_bulk.py --rows 100000 --max-rss-mib 1024`, `uv build`, then `uv run --locked python scripts/check_public_files.py`. The check reviews Git candidates and build member **names**. It rejects sensitive names before opening their files and reports only rule names and line numbers for content findings. It is a guardrail, not a guarantee that all secrets or personal data were found.
4. Inspect `git status --short --untracked-files=all` and `git diff --cached --name-only`. Ensure no private capture, credential file, real trajectory, dataset text, database, or machine-specific path is staged. Verify the commit name and email, using a repository-local noreply identity if desired.
5. Confirm the README states exactly what v0.6.0 implements and labels candidate-only SFT separately from gated dataset export. Confirm `docs/ROADMAP.md` describes the remaining v0.9/v1 work.

## Before a push or tag

Run the same checks from a fresh checkout of the candidate commit. Confirm CI has passed, inspect the release artifact members and package metadata, then create a tag or publish only when separately authorized. Do not include optional live-run output in a release artifact.
