"""Live examples must remain importable without starting a model or Langfuse client."""

from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


def _import_live(name: str):
    live_dir = Path(__file__).resolve().parents[1] / "examples" / "live"
    sys.path.insert(0, str(live_dir))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(live_dir))


class LiveModuleLayoutTests(unittest.TestCase):
    def test_entry_points_use_shared_helpers(self) -> None:
        snapshot = _import_live("langfuse_snapshot")
        corpus = _import_live("musique_corpus")
        smoke = _import_live("live_langfuse_smoke")
        chat = _import_live("live_musique_pilot")
        responses = _import_live("live_musique_responses")

        self.assertIs(smoke._save_snapshot, snapshot._save_snapshot)
        self.assertIs(chat._save_snapshot, snapshot._save_snapshot)
        self.assertIs(responses._reconcile_score, snapshot._reconcile_score)
        self.assertIs(chat.FrozenSearch, corpus.FrozenSearch)
        self.assertIs(responses.FrozenSearch, corpus.FrozenSearch)

    def test_snapshot_collection_retries_until_expected_id_is_visible(self) -> None:
        snapshot = _import_live("langfuse_snapshot")
        pages = [
            {"data": [], "meta": {"cursor": None}},
            {"data": [{"id": "expected"}], "meta": {"cursor": None}},
        ]

        class Response:
            def __init__(self, page):
                self.page = page

            def json(self):
                return json.dumps(self.page)

        def fetch(**kwargs):
            return Response(pages.pop(0))

        with patch.object(snapshot.time, "sleep") as sleep:
            result = snapshot._collect_complete(
                fetch,
                trace_id="trace-example",
                fields="core",
                start=None,
                end=None,
                score=False,
                expected_ids={"expected"},
                attempts=2,
                retry_delay=0.1,
            )

        self.assertEqual(result[0]["data"][0]["id"], "expected")
        sleep.assert_called_once_with(0.1)


if __name__ == "__main__":
    unittest.main()
