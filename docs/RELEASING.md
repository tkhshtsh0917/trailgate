# Release checklist

A commit, push, tag, GitHub release, and package-index publication are distinct actions. Review the candidate and complete the checks below before creating a tag. Package publication is optional and separate.

Use uv 0.12.10 for release builds, matching CI and the checked wheel generator metadata.

## Candidate checks

1. In a clean checkout run `uv sync --locked`, then the [fictional end-to-end guide](GUIDE.md). No model key or Langfuse server is required.
2. Run `uv run --locked ruff check src tests examples scripts`, `uv run --locked ruff format --check src tests examples scripts`, and `uv run --locked python -m unittest discover -s tests -q`. Repeat the unit suite with `--extra bulk-parquet`.
3. Run `uv run --locked python scripts/benchmark_bulk.py --rows 100000 --max-rss-mib 1024`, then repeat with `--observations-per-trace 1`. Record machine, peak RSS, and elapsed time; the gates cover import and summary at ordinary and high trace cardinality, not large graph export.
4. Build fresh wheel and sdist with `uv build`. Run `uv run --locked python scripts/check_release_artifacts.py` to compare source bytes, metadata, bundled JSON Schemas, and the exact archive member allowlists. Check a clean wheel installation and run `uv run --locked python scripts/check_public_files.py`. Repeat the build in a second clean directory and compare SHA-256 digests to test reproducibility on the same platform/toolchain.
5. Run `uv run --locked python scripts/check_dependency_safety.py` and `uv audit --locked`. The first is an offline Brotli floor; the second needs OSV network access and covers all locked extras and groups. Treat an unavailable audit service as unverified, not as a clean result.
6. Inspect `git status --short --untracked-files=all` and `git diff --cached --name-only`. Never stage an environment file, credential, private capture/archive/dataset, real corpus, or machine-specific path. Confirm repository-local Git identity and reread documentation for claims that exceed verified behavior.
7. Check [v1 contracts and migration](CONTRACT.md), [threat model](SECURITY.md), and [comparison scope](COMPARISON.md). CI should pass across declared Python versions. A live Langfuse or Parquet combination not exercised in the matrix must be labeled untested.

The release artifact checker validates the exact member set, the CLI entry point, project metadata, the wheel RECORD, unreviewed ZIP/gzip/tar metadata, and the active source-distribution build configuration. The public-file scanner is a heuristic. It rejects sensitive names before reading their contents and reports only rule names and line numbers for text findings; it cannot establish that all private material was found.

## After review

Only after the user reviews and authorizes it, create and push the `v1.0.0` tag. A GitHub release or package-index publication needs its own decision. Keep optional live-run outputs out of release artifacts.
