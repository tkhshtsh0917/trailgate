"""Live examples must remain importable without starting a model or Langfuse client."""

from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_score_reader_selects_sdk_v4_and_v5_accessors(self) -> None:
        snapshot = _import_live("langfuse_snapshot")

        def v4_reader(**_kwargs):
            return "v4"

        def v5_reader(**_kwargs):
            return "v5"

        v4 = SimpleNamespace(
            api=SimpleNamespace(
                scores_v3=SimpleNamespace(get_many_v3=v4_reader),
                scores=SimpleNamespace(get_many=v5_reader),
            )
        )
        v5 = SimpleNamespace(api=SimpleNamespace(scores=SimpleNamespace(get_many=v5_reader)))

        self.assertIs(snapshot._score_reader(v4), v4_reader)
        self.assertIs(snapshot._score_reader(v5), v5_reader)


if __name__ == "__main__":
    unittest.main()
