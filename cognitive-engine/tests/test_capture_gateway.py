import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "capture_gateway.py"
spec = importlib.util.spec_from_file_location("capture_gateway", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)
Gateway = mod.Gateway


class CaptureGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "runtime").mkdir(parents=True)
        (self.root / "runtime" / "fin3703a-staging").mkdir()
        (self.root / "runtime" / "fin3701a-staging").mkdir()
        (self.root / "runtime" / "fin3703a-staging" / "repo agreement and fed funds.txt").write_text("x")
        (self.root / "runtime" / "fin3701a-staging" / "mergers acquisitions capital structure.pdf").write_text("x")

        state = {
            "version": 2,
            "learning_threads": {
                "fin3703a": {
                    "unit_id": "fin3703a",
                    "title": "FIN3703A Financial Markets",
                    "learning_state": "Learning",
                    "thread_state": "active",
                    "sessions": {
                        "s1": {
                            "topic_chain": ["repo agreement", "federal funds", "duration gap"]
                        }
                    },
                },
                "fin3701a": {
                    "unit_id": "fin3701a",
                    "title": "FIN3701A Corporate Finance",
                    "learning_state": "Learning",
                    "thread_state": "active",
                    "sessions": {},
                },
                "paused": {
                    "unit_id": "paused",
                    "title": "Paused learning unit",
                    "learning_state": "Learning",
                    "thread_state": "paused",
                    "sessions": {},
                },
            },
        }
        (self.root / "runtime" / "learning-threads.yaml").write_text(
            yaml.safe_dump(state, sort_keys=False), encoding="utf-8"
        )
        self.gateway = Gateway(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, **overrides):
        data = {
            "conversation_id": "abc",
            "message_id": "m1",
            "ordinal": 1,
            "role": "user",
            "content": "FIN3703A repo agreement",
            "url": "https://chatgpt.com/c/abc",
            "observed_at": "2026-10-07T00:00:00Z",
        }
        data.update(overrides)
        return data

    def test_duplicate_message_is_idempotent(self):
        a = self.gateway.ingest_event(self.payload())
        b = self.gateway.ingest_event(self.payload())
        self.assertTrue(a["inserted"])
        self.assertFalse(b["inserted"])
        status = self.gateway.ledger.conversation_status("abc")
        self.assertEqual(status["event_count"], 1)

    def test_ambiguous_first_message_stays_unresolved(self):
        r = self.gateway.ingest_event(
            self.payload(content="为什么这里要除以1.05", message_id="amb1")
        )
        self.assertEqual(r["routing_status"], "unresolved")
        self.assertIsNone(r["learning_unit_id"])

    def test_explicit_course_code_routes_without_manual_binding(self):
        r = self.gateway.ingest_event(self.payload())
        self.assertEqual(r["routing_status"], "session_routed")
        self.assertEqual(r["learning_unit_id"], "fin3703a")
        self.assertTrue(r["segment_id"])
        self.assertTrue(r["session_id"])

    def test_same_conversation_can_switch_learning_units(self):
        a = self.gateway.ingest_event(self.payload())
        b = self.gateway.ingest_event(
            self.payload(
                message_id="m2",
                ordinal=2,
                content="FIN3701A mergers and acquisitions",
            )
        )
        self.assertEqual(a["learning_unit_id"], "fin3703a")
        self.assertEqual(b["learning_unit_id"], "fin3701a")
        self.assertNotEqual(a["segment_id"], b["segment_id"])
        status = self.gateway.ledger.conversation_status("abc")
        self.assertEqual(set(status["learning_unit_ids"]), {"fin3703a", "fin3701a"})
        self.assertEqual(status["segment_count"], 2)

    def test_assistant_inherits_preceding_user_segment(self):
        user = self.gateway.ingest_event(self.payload())
        assistant = self.gateway.ingest_event(
            self.payload(
                message_id="m2",
                ordinal=2,
                role="assistant",
                content="A repo is a collateralized short-term funding transaction.",
            )
        )
        self.assertEqual(assistant["learning_unit_id"], "fin3703a")
        self.assertEqual(assistant["segment_id"], user["segment_id"])
        self.assertEqual(assistant["session_id"], user["session_id"])
        self.assertEqual(assistant["routing_status"], "session_routed")
        self.assertEqual(
            assistant["routing_reason"], "assistant_inherits_preceding_user"
        )

    def test_legacy_binding_row_no_longer_influences_routing(self):
        with self.gateway.ledger.connect() as con:
            con.execute(
                """
                INSERT INTO bindings(conversation_id, learning_unit_id, bound_at, source)
                VALUES (?, ?, ?, ?)
                """,
                ("abc", "fin3703a", "2026-10-07T00:00:00Z", "legacy"),
            )
        result = self.gateway.ingest_event(
            self.payload(
                message_id="m3",
                ordinal=3,
                content="为什么这里要除以1.05",
            )
        )
        self.assertEqual(result["routing_status"], "unresolved")
        self.assertIsNone(result["learning_unit_id"])

    def test_streaming_update_changes_content_without_duplicate_row(self):
        first = self.gateway.ingest_event(
            self.payload(
                message_id="a1",
                ordinal=2,
                role="assistant",
                content="A repo is",
            )
        )
        second = self.gateway.ingest_event(
            self.payload(
                message_id="a1",
                ordinal=2,
                role="assistant",
                content="A repo is a collateralized short-term funding transaction.",
            )
        )
        third = self.gateway.ingest_event(
            self.payload(
                message_id="a1",
                ordinal=2,
                role="assistant",
                content="A repo is a collateralized short-term funding transaction.",
            )
        )
        self.assertTrue(first["inserted"])
        self.assertEqual(first["content_update_count"], 0)
        self.assertFalse(second["inserted"])
        self.assertTrue(second["updated"])
        self.assertEqual(second["content_update_count"], 1)
        self.assertFalse(third["inserted"])
        self.assertFalse(third["updated"])
        self.assertEqual(third["content_update_count"], 1)
        rows = self.gateway.ledger.events_for_conversation("abc")
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["content"],
            "A repo is a collateralized short-term funding transaction.",
        )
        status = self.gateway.ledger.conversation_status("abc")
        self.assertEqual(status["streaming_updated_events"], 1)
        self.assertEqual(status["max_content_update_count"], 1)

    def test_streaming_assistant_is_single_session_event(self):
        user = self.gateway.ingest_event(self.payload())
        sid = user["session_id"]
        first = self.gateway.ingest_event(
            self.payload(
                message_id="stream-a",
                ordinal=2,
                role="assistant",
                content="A repo is",
            )
        )
        second = self.gateway.ingest_event(
            self.payload(
                message_id="stream-a",
                ordinal=2,
                role="assistant",
                content="A repo is a collateralized short-term funding transaction.",
            )
        )
        events = self.gateway.session_router.capture._read_events(sid)
        assistant_events = [
            e for e in events
            if (e.get("metadata") or {}).get("message_id") == "stream-a"
        ]
        self.assertEqual(first["routing_status"], "session_routed")
        self.assertEqual(second["routing_status"], "session_routed")
        self.assertEqual(len(assistant_events), 1)
        self.assertEqual(
            assistant_events[0]["content"],
            "A repo is a collateralized short-term funding transaction.",
        )
        self.assertEqual(
            assistant_events[0]["metadata"]["capture_update_count"],
            1,
        )
        self.assertFalse(assistant_events[0]["retain_in_trace"])

    def test_unresolved_can_be_resolved_by_surrounding_same_unit(self):
        self.gateway.ingest_event(
            self.payload(
                message_id="m1",
                ordinal=1,
                content="FIN3701A mergers and acquisitions",
            )
        )
        middle = self.gateway.ingest_event(
            self.payload(
                message_id="m2",
                ordinal=2,
                content="为什么这里要除以1.05",
            )
        )
        self.assertEqual(middle["routing_status"], "unresolved")
        self.gateway.ingest_event(
            self.payload(
                message_id="m3",
                ordinal=3,
                content="FIN3701A capital structure",
            )
        )
        rows = self.gateway.ledger.events_for_conversation("abc")
        by_id = {row["message_id"]: row for row in rows}
        self.assertEqual(by_id["m2"]["learning_unit_id"], "fin3701a")
        self.assertEqual(by_id["m2"]["routing_status"], "session_routed")
        self.assertEqual(
            by_id["m2"]["routing_reason"],
            "context_sandwich_same_unit",
        )
        self.assertEqual(by_id["m1"]["segment_id"], by_id["m2"]["segment_id"])
        self.assertEqual(by_id["m2"]["segment_id"], by_id["m3"]["segment_id"])

    def test_assistant_is_repaired_after_user_contextual_reroute(self):
        self.gateway.ingest_event(
            self.payload(
                message_id="m1",
                ordinal=1,
                content="FIN3701A capital structure",
            )
        )
        self.gateway.ingest_event(
            self.payload(
                message_id="m2",
                ordinal=2,
                content="为什么这里要除以1.05",
            )
        )
        assistant = self.gateway.ingest_event(
            self.payload(
                message_id="a2",
                ordinal=3,
                role="assistant",
                content="这里需要结合上一条公式来看。",
            )
        )
        self.assertIsNone(assistant["learning_unit_id"])
        self.gateway.ingest_event(
            self.payload(
                message_id="m3",
                ordinal=4,
                content="FIN3701A mergers and acquisitions",
            )
        )
        rows = self.gateway.ledger.events_for_conversation("abc")
        by_id = {row["message_id"]: row for row in rows}
        self.assertEqual(by_id["m2"]["learning_unit_id"], "fin3701a")
        self.assertEqual(by_id["a2"]["learning_unit_id"], "fin3701a")
        self.assertEqual(by_id["a2"]["segment_id"], by_id["m2"]["segment_id"])

    def test_alternating_units_rebuild_as_distinct_segments(self):
        messages = [
            ("m1", 1, "FIN3701A capital structure"),
            ("m2", 2, "FIN3703A repo agreement"),
            ("m3", 3, "FIN3701A mergers and acquisitions"),
            ("m4", 4, "FIN3703A federal funds"),
        ]
        for message_id, ordinal, content in messages:
            self.gateway.ingest_event(
                self.payload(
                    message_id=message_id,
                    ordinal=ordinal,
                    content=content,
                )
            )
        rows = [
            row
            for row in self.gateway.ledger.events_for_conversation("abc")
            if row["role"] == "user"
        ]
        self.assertEqual(len({row["segment_id"] for row in rows}), 4)

    def test_unresolved_event_eventually_moves_to_needs_review(self):
        self.gateway.ingest_event(
            self.payload(
                message_id="u1",
                ordinal=1,
                content="这个数字为什么这样算",
            )
        )
        self.gateway.ingest_event(
            self.payload(
                message_id="u2",
                ordinal=2,
                content="这个地方我还是没理解",
            )
        )
        self.gateway.ingest_event(
            self.payload(
                message_id="u3",
                ordinal=3,
                content="上面这个步骤是什么意思",
            )
        )
        self.gateway.reroute_unresolved("abc")
        rows = self.gateway.ledger.events_for_conversation("abc")
        first = next(row for row in rows if row["message_id"] == "u1")
        self.assertEqual(first["routing_status"], "needs_review")
        queue = self.gateway.ledger.review_queue()
        self.assertTrue(any(item["event_key"] == first["event_key"] for item in queue))

    def test_adapter_observation_is_queryable(self):
        self.gateway.ledger.record_adapter_observation(
            {
                "conversation_id": "abc",
                "surface": "pwa",
                "url": "https://chatgpt.com/c/abc",
                "page_message_count": 12,
                "extension_version": "0.3.0",
                "observed_at": "2026-10-07T06:00:00Z",
            }
        )
        status = self.gateway.capture_status("abc")
        self.assertEqual(status["adapter"]["surface"], "pwa")
        self.assertEqual(status["adapter"]["page_message_count"], 12)
        self.assertEqual(status["adapter"]["extension_version"], "0.3.0")

    def test_same_segment_can_contain_multiple_sessions(self):
        self.gateway.ingest_event(
            self.payload(
                message_id="s1",
                ordinal=1,
                content="FIN3701A mergers and acquisitions",
            )
        )
        self.gateway.ingest_event(
            self.payload(
                message_id="s2",
                ordinal=2,
                content="为什么这里要除以1.05",
            )
        )
        self.gateway.ingest_event(
            self.payload(
                message_id="s3",
                ordinal=3,
                content="FIN3701A capital structure",
            )
        )
        rows = {
            row["message_id"]: row
            for row in self.gateway.ledger.events_for_conversation("abc")
        }
        self.assertEqual(rows["s1"]["segment_id"], rows["s2"]["segment_id"])
        self.assertEqual(rows["s2"]["segment_id"], rows["s3"]["segment_id"])
        self.assertEqual(rows["s1"]["session_id"], rows["s2"]["session_id"])
        self.assertNotEqual(rows["s2"]["session_id"], rows["s3"]["session_id"])
        self.assertEqual(rows["s2"]["session_routing_reason"], "context_sandwich_session_continuity")

    def test_same_session_can_resume_across_conversations(self):
        first = self.gateway.ingest_event(
            self.payload(
                conversation_id="conv-a",
                message_id="a1",
                ordinal=1,
                content="FIN3703A repo agreement mechanics",
            )
        )
        second = self.gateway.ingest_event(
            self.payload(
                conversation_id="conv-b",
                message_id="b1",
                ordinal=1,
                content="FIN3703A repo agreement collateral mechanics",
            )
        )
        self.assertEqual(first["routing_status"], "session_routed")
        self.assertEqual(second["routing_status"], "session_routed")
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertNotEqual(first["segment_id"], second["segment_id"])

    def test_paused_learning_unit_is_not_auto_candidate(self):
        units = self.gateway.learning_units()
        self.assertEqual({u["unit_id"] for u in units}, {"fin3703a", "fin3701a"})


if __name__ == "__main__":
    unittest.main()
