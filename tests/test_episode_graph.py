from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from trailgate.episode_graph import build_episode_graph
from trailgate.snapshot import ingest_snapshot

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "snapshot"


class EpisodeGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        shutil.copytree(FIXTURE, self.source)
        self.archive = self.root / "archive"

    def _change_page(self, name, change) -> None:
        path = self.source / name
        page = json.loads(path.read_text())
        change(page)
        path.write_text(json.dumps(page) + "\n")

    def _change_capture(self, change) -> None:
        path = self.source / "capture.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        change(events)
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    def _graph(self):
        ingest_snapshot(self.source, self.archive)
        return build_episode_graph(self.archive)

    def test_graph_preserves_exact_prefix_and_separate_score(self) -> None:
        graph = self._graph()
        self.assertEqual(graph["schema_version"], "trailgate.episode_graph.v1")
        self.assertEqual(len(graph["episodes"]), 1)
        self.assertEqual(graph["episodes"][0]["boundary"], "unknown")
        self.assertEqual(len(graph["decisions"]), 2)
        self.assertEqual(len(graph["actions"]), 1)
        self.assertEqual(len(graph["deliveries"]), 1)
        self.assertEqual(graph["actions"][0]["state"], "verified_delivery")
        self.assertEqual(graph["score_links"][0]["subject_kind"], "trace")
        self.assertTrue(graph["score_links"][0]["resolved"])
        self.assertEqual(graph["orphan_tool_observation_ids"], [])

        search, answer = graph["decisions"]
        self.assertNotIn("Synthetic evidence.", json.dumps(search["visible_prefix"]))
        self.assertIn("Synthetic evidence.", json.dumps(answer["visible_prefix"]))
        self.assertEqual(answer["history_relation"], "unknown")
        self.assertEqual(graph, build_episode_graph(self.archive))

    def test_cross_trace_session_is_grouped_without_claiming_episode_boundary(self) -> None:
        def add_trace(page):
            page["data"].append(
                {
                    "id": "root-two",
                    "traceId": "trace-two",
                    "projectId": "project-example",
                    "parentObservationId": None,
                    "startTime": "2026-10-01T00:00:08Z",
                    "endTime": "2026-10-01T00:00:09Z",
                    "type": "SPAN",
                    "sessionId": "session-example",
                }
            )

        self._change_page("observations.jsonl", add_trace)
        graph = self._graph()
        self.assertEqual(len(graph["episodes"]), 1)
        self.assertEqual(graph["episodes"][0]["trace_ids"], ["trace-example", "trace-two"])
        self.assertEqual(graph["episodes"][0]["boundary"], "unknown")
        self.assertEqual(len(graph["traces"]), 2)

    def test_parallel_calls_and_orphaned_tool_are_explicit(self) -> None:
        def add_tools(page):
            for name in ("tool-second", "tool-orphan"):
                page["data"].append(
                    {
                        "id": name,
                        "traceId": "trace-example",
                        "projectId": "project-example",
                        "parentObservationId": "gen-search",
                        "startTime": "2026-10-01T00:00:02Z",
                        "endTime": "2026-10-01T00:00:03Z",
                        "type": "TOOL",
                        "sessionId": "session-example",
                    }
                )

        def add_call(events):
            finish = next(event for event in events if event.get("kind") == "finish")
            finish["assistant_message"]["tool_calls"].append(
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"query":"second"}'},
                }
            )
            first_delivery = next(event for event in events if event.get("kind") == "delivery")
            events.insert(
                events.index(first_delivery) + 1,
                {
                    "kind": "delivery",
                    "capture_id": "cap-search",
                    "call_id": "call-2",
                    "observation_id": "tool-second",
                    "value": "Second fictional result",
                },
            )

        self._change_page("observations.jsonl", add_tools)
        self._change_capture(add_call)
        graph = self._graph()
        self.assertEqual(len(graph["actions"]), 2)
        self.assertEqual(len(graph["deliveries"]), 2)
        self.assertEqual(graph["orphan_tool_observation_ids"], ["tool-orphan"])
        self.assertEqual({item["state"] for item in graph["actions"]}, {"verified_delivery"})

    def test_compacted_history_and_incomplete_capture_are_not_filled_in(self) -> None:
        def rewrite(events):
            begin = next(
                event
                for event in events
                if event["capture_id"] == "cap-answer" and event["kind"] == "begin"
            )
            begin["messages"] = [{"role": "user", "content": "Only a short reminder."}]
            events[:] = [
                event
                for event in events
                if not (event["capture_id"] == "cap-answer" and event["kind"] == "finish")
            ]

        self._change_capture(rewrite)
        graph = self._graph()
        answer = graph["decisions"][1]
        self.assertFalse(answer["structural_candidate"])
        self.assertEqual(
            answer["visible_prefix"], [{"role": "user", "content": "Only a short reminder."}]
        )
        self.assertEqual(answer["history_relation"], "unknown")
        self.assertEqual(answer["action_ids"], [])


if __name__ == "__main__":
    unittest.main()
