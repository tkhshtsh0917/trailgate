from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from trailgate import pull as module
from trailgate.archive import ingest_snapshot, read_archive
from trailgate.pull import PullSpec, preview_pull, pull_snapshot
from trailgate.snapshot_input import SnapshotError, read_snapshot

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "snapshot"
OBSERVATIONS = json.loads((FIXTURE / "observations.jsonl").read_text())["data"]
SCORES = json.loads((FIXTURE / "scores.jsonl").read_text())["data"]


class PullTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.observations = OBSERVATIONS
        self.scores = SCORES
        self.requests: list[tuple[str, str | None]] = []
        self.spec = PullSpec(
            base_url="http://127.0.0.1:3000",
            project_id="project-example",
            from_time="2026-10-01T00:00:00Z",
            to_time="2026-10-02T00:00:00Z",
            limit=2,
        )
        self.credentials = ("fictional-public", "fictional-secret")

    def _page(self, spec, table, cursor, credentials):
        self.assertEqual(credentials, self.credentials)
        self.requests.append((table, cursor))
        rows = self.observations if table == "observations" else self.scores
        if spec.trace_id and table == "observations":
            rows = [row for row in rows if row["traceId"] == spec.trace_id]
        offset = int(cursor) if cursor else 0
        next_offset = offset + spec.limit
        return json.dumps(
            {
                "data": rows[offset:next_offset],
                "meta": {"cursor": str(next_offset) if next_offset < len(rows) else None},
            }
        ).encode()

    def test_dry_run_does_not_request_pages(self) -> None:
        preview = preview_pull(self.spec)
        self.assertEqual(preview["model_calls"], 0)
        self.assertEqual(self.requests, [])
        with self.assertRaisesRegex(SnapshotError, "HTTPS except for loopback"):
            preview_pull(PullSpec(**{**self.spec.__dict__, "base_url": "http://example.test"}))

    def test_rest_transport_uses_get_basic_auth_and_bounded_retry(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size=-1):
                return b'{"data":[],"meta":{"cursor":null}}'

        requests = []
        retry_body = io.BytesIO(b"fictional rate limit response")

        def respond(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "rate limit", {}, retry_body)
            return Response()

        class Opener:
            open = staticmethod(respond)

        with (
            patch.object(module.urllib.request, "build_opener", return_value=Opener()) as opener,
            patch.object(module.time, "sleep") as sleep,
        ):
            body = module._request_page(self.spec, "observations", None, self.credentials)
        self.assertIn(b'"cursor":null', body)
        self.assertEqual(len(requests), 2)
        self.assertTrue(retry_body.closed)
        self.assertEqual(requests[0].get_method(), "GET")
        self.assertIsInstance(opener.call_args.args[1], module.urllib.request.ProxyHandler)
        self.assertEqual(opener.call_args.args[1].proxies, {})
        self.assertIn("/api/public/v2/observations?", requests[0].full_url)
        self.assertEqual(
            requests[0].get_header("Authorization"),
            "Basic ZmljdGlvbmFsLXB1YmxpYzpmaWN0aW9uYWwtc2VjcmV0",
        )
        sleep.assert_called_once()

        widened = PullSpec(**{**self.spec.__dict__, "score_to_time": "2026-10-04T00:00:00Z"})
        with patch.object(module.urllib.request, "build_opener", return_value=Opener()):
            module._request_page(widened, "scores", None, self.credentials)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[-1].full_url).query)
        self.assertEqual(params["toTimestamp"], ["2026-10-04T00:00:00Z"])
        self.assertNotIn("toStartTime", params)

    def test_paginated_pull_matches_offline_source_rows(self) -> None:
        destination = self.root / "pull"
        with patch.object(module, "_request_page", side_effect=self._page):
            result = pull_snapshot(self.spec, destination, self.credentials)
        parsed = read_snapshot(destination)
        self.assertEqual(result["observations"], 4)
        self.assertEqual(result["scores"], 1)
        self.assertEqual(
            {item["row"]["id"] for item in parsed["observations"]},
            {row["id"] for row in OBSERVATIONS},
        )
        self.assertEqual(
            parsed["manifest"]["collection"]["pages"], {"observations": 2, "scores": 1}
        )
        self.assertEqual(result["completeness"], "cursor_exhausted_at_collection_time")
        self.assertEqual(
            self.requests, [("observations", None), ("observations", "2"), ("scores", None)]
        )

    def test_interrupted_pull_resumes_from_saved_page(self) -> None:
        destination = self.root / "pull"
        calls = 0

        def interrupt(spec, table, cursor, credentials):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SnapshotError("fictional network interruption")
            return self._page(spec, table, cursor, credentials)

        with patch.object(module, "_request_page", side_effect=interrupt):
            with self.assertRaisesRegex(SnapshotError, "interruption"):
                pull_snapshot(self.spec, destination, self.credentials)
        self.assertFalse((destination / "manifest.json").exists())
        self.assertEqual(len(list((destination / "pages" / "observations").glob("*.json"))), 1)
        saved_spec = json.loads((destination / "pull.json").read_text())["spec"]
        self.assertNotIn("score_from_time", saved_spec)
        self.assertNotIn("score_to_time", saved_spec)

        with patch.object(module, "_request_page", side_effect=self._page):
            result = pull_snapshot(self.spec, destination, self.credentials, resume=True)
        self.assertEqual(result["observations"], 4)
        self.assertEqual(
            self.requests, [("observations", None), ("observations", "2"), ("scores", None)]
        )
        with self.assertRaisesRegex(SnapshotError, "existing incomplete"):
            pull_snapshot(self.spec, destination, self.credentials, resume=True)

    def test_resume_rejects_materialized_rows_different_from_saved_pages(self) -> None:
        for table in ("observations", "scores"):
            with self.subTest(table=table):
                destination = self.root / f"pull-{table}"
                original = module._atomic_private_bytes

                def interrupt(path, data, original_write=original):
                    if path.name == "manifest.json":
                        raise SnapshotError("fictional interruption after materialization")
                    return original_write(path, data)

                with (
                    patch.object(module, "_request_page", side_effect=self._page),
                    patch.object(module, "_atomic_private_bytes", side_effect=interrupt),
                ):
                    with self.assertRaisesRegex(SnapshotError, "fictional interruption"):
                        pull_snapshot(self.spec, destination, self.credentials)

                materialized = destination / f"{table}.jsonl"
                pages = [json.loads(line) for line in materialized.read_text().splitlines()]
                pages[0]["data"][0]["name"] = "fictional-tampered-name"
                materialized.write_text("\n".join(json.dumps(page) for page in pages) + "\n")
                with patch.object(module, "_request_page", side_effect=self._page):
                    with self.assertRaisesRegex(SnapshotError, "differs from saved pages"):
                        pull_snapshot(self.spec, destination, self.credentials, resume=True)
                self.assertFalse((destination / "manifest.json").exists())

    def test_resume_accepts_matching_materialized_pages(self) -> None:
        destination = self.root / "pull"
        original = module._atomic_private_bytes

        def interrupt(path, data):
            if path.name == "manifest.json":
                raise SnapshotError("fictional interruption after materialization")
            return original(path, data)

        with (
            patch.object(module, "_request_page", side_effect=self._page),
            patch.object(module, "_atomic_private_bytes", side_effect=interrupt),
        ):
            with self.assertRaisesRegex(SnapshotError, "fictional interruption"):
                pull_snapshot(self.spec, destination, self.credentials)
        with patch.object(module, "_request_page", side_effect=self._page) as request:
            result = pull_snapshot(self.spec, destination, self.credentials, resume=True)
        request.assert_not_called()
        self.assertEqual(result["observations"], 4)

    def test_delayed_score_reconciliation_creates_new_snapshot(self) -> None:
        self.scores = []
        first = self.root / "first"
        with patch.object(module, "_request_page", side_effect=self._page):
            first_result = pull_snapshot(self.spec, first, self.credentials)
        archive = self.root / "archive"
        ingest_snapshot(first, archive)
        original_hashes = read_archive(archive)["index"]["source_hashes"]

        self.scores = SCORES
        second = self.root / "second"
        with patch.object(module, "_request_page", side_effect=self._page):
            second_result = pull_snapshot(
                self.spec, second, self.credentials, previous_archive=archive
            )
        self.assertNotEqual(first_result["snapshot_id"], second_result["snapshot_id"])
        self.assertEqual(second_result["revision_of_snapshot_id"], first_result["snapshot_id"])
        self.assertEqual(read_archive(archive)["index"]["source_hashes"], original_hashes)
        self.assertEqual(second_result["scores"], 1)

    def test_later_score_timestamp_can_be_reconciled_with_wider_score_window(self) -> None:
        delayed = {**SCORES[0], "timestamp": "2026-10-03T01:00:00Z"}

        def filtered_page(spec, table, cursor, credentials):
            if table == "scores":
                start, end = module._score_bounds(spec)
                scored_at = datetime.fromisoformat(delayed["timestamp"].replace("Z", "+00:00"))
                lower = datetime.fromisoformat(start.replace("Z", "+00:00"))
                upper = datetime.fromisoformat(end.replace("Z", "+00:00"))
                self.scores = [delayed] if lower <= scored_at < upper else []
            return self._page(spec, table, cursor, credentials)

        first = self.root / "first"
        with patch.object(module, "_request_page", side_effect=filtered_page):
            initial = pull_snapshot(self.spec, first, self.credentials)
        self.assertEqual(initial["scores"], 0)
        archive = self.root / "archive"
        ingest_snapshot(first, archive)

        widened = PullSpec(**{**self.spec.__dict__, "score_to_time": "2026-10-04T00:00:00Z"})
        second = self.root / "second"
        with patch.object(module, "_request_page", side_effect=filtered_page):
            result = pull_snapshot(widened, second, self.credentials, previous_archive=archive)
        self.assertEqual(result["scores"], 1)
        self.assertEqual(result["revision_of_snapshot_id"], initial["snapshot_id"])
        self.assertEqual(
            read_snapshot(second)["manifest"]["score_query"]["toTimestamp"],
            "2026-10-04T00:00:00Z",
        )

        narrowed = PullSpec(**{**self.spec.__dict__, "score_to_time": "2026-10-01T12:00:00Z"})
        with self.assertRaisesRegex(SnapshotError, "previous pull scope differs"):
            pull_snapshot(
                narrowed, self.root / "narrowed", self.credentials, previous_archive=archive
            )

    def test_revision_rejects_different_prior_scope(self) -> None:
        first = self.root / "first"
        with patch.object(module, "_request_page", side_effect=self._page):
            pull_snapshot(self.spec, first, self.credentials)
        archive = self.root / "archive"
        ingest_snapshot(first, archive)

        other_scope = PullSpec(**{**self.spec.__dict__, "to_time": "2026-10-03T00:00:00Z"})
        with self.assertRaisesRegex(SnapshotError, "previous pull scope differs"):
            pull_snapshot(
                other_scope,
                self.root / "second",
                self.credentials,
                previous_archive=archive,
            )
        self.assertFalse((self.root / "second").exists())

    def test_revision_requires_verified_pull_provenance(self) -> None:
        archive = self.root / "offline-archive"
        ingest_snapshot(FIXTURE, archive)
        with self.assertRaisesRegex(SnapshotError, "no verified pull scope"):
            pull_snapshot(
                self.spec,
                self.root / "second",
                self.credentials,
                previous_archive=archive,
            )

    def test_scope_mismatch_keeps_checkpoint_incomplete(self) -> None:
        self.observations = [{**OBSERVATIONS[0], "projectId": "other-project"}]
        destination = self.root / "pull"
        with patch.object(module, "_request_page", side_effect=self._page):
            with self.assertRaisesRegex(SnapshotError, "outside project_id"):
                pull_snapshot(self.spec, destination, self.credentials)
        self.assertFalse((destination / "manifest.json").exists())

    def test_trace_scoped_pull_rejects_unverifiable_score_subject(self) -> None:
        self.spec = PullSpec(**{**self.spec.__dict__, "trace_id": "trace-example"})
        self.scores = [{**SCORES[0], "subject": {"kind": "trace", "id": "other-trace"}}]
        with patch.object(module, "_request_page", side_effect=self._page):
            with self.assertRaisesRegex(SnapshotError, "outside trace_id"):
                pull_snapshot(self.spec, self.root / "pull", self.credentials)

    def test_changed_checkpoint_page_is_rejected_on_resume(self) -> None:
        destination = self.root / "pull"
        calls = 0

        def interrupt(spec, table, cursor, credentials):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SnapshotError("fictional interruption")
            return self._page(spec, table, cursor, credentials)

        with patch.object(module, "_request_page", side_effect=interrupt):
            with self.assertRaises(SnapshotError):
                pull_snapshot(self.spec, destination, self.credentials)
        page = next((destination / "pages" / "observations").glob("*.json"))
        page.write_bytes(page.read_bytes() + b" ")
        with self.assertRaisesRegex(SnapshotError, "page hash mismatch"):
            pull_snapshot(self.spec, destination, self.credentials, resume=True)

    def test_pull_rejects_duplicate_rows_before_checkpoint(self) -> None:
        def duplicate(spec, table, cursor, credentials):
            if table == "observations" and cursor == "2":
                return json.dumps(
                    {"data": [self.observations[0]], "meta": {"cursor": None}}
                ).encode()
            return self._page(spec, table, cursor, credentials)

        destination = self.root / "pull"
        with patch.object(module, "_request_page", side_effect=duplicate):
            with self.assertRaisesRegex(SnapshotError, "duplicate row ID"):
                pull_snapshot(self.spec, destination, self.credentials)
        self.assertEqual(len(list((destination / "pages" / "observations").glob("*.json"))), 1)

    def test_pull_enforces_total_page_size_before_checkpoint(self) -> None:
        destination = self.root / "pull"
        with (
            patch.object(module, "MAX_API_SNAPSHOT_BYTES", 1),
            patch.object(module, "_request_page", side_effect=self._page),
        ):
            with self.assertRaisesRegex(SnapshotError, "64 MiB API snapshot limit"):
                pull_snapshot(self.spec, destination, self.credentials)
        self.assertEqual(list((destination / "pages" / "observations").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
