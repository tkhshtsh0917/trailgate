# Read-only Langfuse pull

Use a bounded time interval and the project ID associated with your Langfuse API keys. `--dry-run` validates and prints scope without reading keys or contacting the server:

```sh
uv run --locked trailgate pull langfuse \
  --base-url http://127.0.0.1:3000 --project-id YOUR_PROJECT_ID \
  --from-time 2026-10-01T00:00:00Z --to-time 2026-10-02T00:00:00Z \
  --out private-pull --dry-run
```

For an actual pull, set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in the process environment and remove `--dry-run`. The command makes GET requests only to [Observations API v2 and Scores API v3](https://langfuse.com/docs/api-and-data-platform/features/public-api). It permits plain HTTP only on loopback, ignores ambient HTTP proxies, refuses redirects, caps each response at 32 MiB, retries 429/5xx at most three times, and never prints a key or response body. `--limit` is 1–100; `--max-pages` bounds **each** table. `--trace-id` narrows collection and requires score subjects so scope can be checked.

Each response page is stored under `private-pull/pages/` with its SHA-256 in the filename. If collection stops, repeat the identical command with `--resume`; the same scope is required, and changed checkpoint bytes fail. The final `manifest.json` is published after both tables reach a null cursor and the assembled snapshot passes validation. The manifest records the collection time and `cursor_exhausted_at_collection_time`. This is a statement about a bounded API walk, not proof that every trace or late-arriving score already existed at collection time.

To reconcile delayed scores, run a **new** pull into a new directory with `--previous-archive` pointing to an imported prior **Trailgate pull**. Project, trace, time range, and field groups must match; an unrelated offline snapshot cannot be claimed as the predecessor. The new manifest records its predecessor's snapshot ID. The prior archive is never changed. After collection, use `trailgate ingest langfuse-api-snapshot private-pull --out private-archive`.

API behavior and availability depend on the Langfuse server version. The v2 observations endpoint requires self-hosted Langfuse v4; consult the [official compatibility documentation](https://langfuse.com/self-hosting/upgrade/versioning). An API cursor and a user-supplied query bound do not establish complete episode boundaries.
