import importlib.util
import tempfile
import unittest
import sys
from pathlib import Path

import yaml

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "session_capture.py"
spec = importlib.util.spec_from_file_location("session_capture", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
SessionCapture = mod.SessionCapture


class SessionCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "runtime").mkdir(parents=True)
        (self.root / "cognitive-inbox" / "pending").mkdir(parents=True)
        state = {
            "version": 2,
            "learning_threads": {
                "fin3703a": {"unit_id": "fin3703a", "title": "FIN3703A", "learning_state": "Learning", "thread_state": "active", "sessions": {}, "recent_session_id": None},
                "paper": {"unit_id": "paper", "title": "Paper", "learning_state": "Learning", "thread_state": "paused", "sessions": {}, "recent_session_id": None},
            },
            "last_touched_thread": "fin3703a",
        }
        (self.root / "runtime" / "learning-threads.yaml").write_text(yaml.safe_dump(state, sort_keys=False))
        self.sc = SessionCapture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_cognitive_event_starts_session(self):
        r = self.sc.capture("fin3703a", ["duration"], "USER_QUESTION", "What is duration?", platform="ChatGPT")
        self.assertTrue(r["created_session"])
        self.assertEqual(len(self.sc.open_sessions("fin3703a")), 1)

    def test_same_topic_chain_resolves_same_session_without_time_rule(self):
        first = self.sc.capture("fin3703a", ["duration"], "USER_QUESTION", "What is duration?")
        second = self.sc.capture("fin3703a", ["duration", "duration gap"], "USER_REASONING", "Duration gap links asset and liability sensitivity.")
        self.assertEqual(first["session_id"], second["session_id"])

    def test_different_topic_creates_parallel_session_same_unit(self):
        a = self.sc.capture("fin3703a", ["duration"], "USER_QUESTION", "What is duration?")
        b = self.sc.capture("fin3703a", ["debt market", "bond issuance"], "USER_QUESTION", "How are bonds issued?")
        self.assertNotEqual(a["session_id"], b["session_id"])
        self.assertEqual(len(self.sc.open_sessions("fin3703a")), 2)

    def test_other_learning_unit_does_not_interrupt_session(self):
        a = self.sc.capture("fin3703a", ["duration"], "USER_QUESTION", "What is duration?")
        self.sc.capture("paper", ["market bubbles"], "USER_QUESTION", "What is the mechanism?")
        resumed = self.sc.capture("fin3703a", ["duration", "duration gap"], "USER_REASONING", "The chain continues.")
        self.assertEqual(a["session_id"], resumed["session_id"])

    def test_ai_explanation_is_not_retained_by_default(self):
        a = self.sc.capture("fin3703a", ["duration"], "USER_QUESTION", "What is duration?")
        self.sc.capture("fin3703a", ["duration"], "AGENT_EXPLANATION", "An explanation.", session_id=a["session_id"])
        path = self.sc.finalize(a["session_id"])
        trace = yaml.safe_load(path.read_text())
        self.assertEqual(trace["event_count"], 2)
        self.assertEqual(trace["retained_event_count"], 1)
        self.assertEqual(trace["ai_contributions"], [])

    def test_finalize_writes_pending_trace_with_provenance(self):
        a = self.sc.capture("fin3703a", ["duration"], "USER_REASONING", "Duration measures rate sensitivity.")
        self.sc.capture("fin3703a", ["duration"], "SOURCE_CLAIM", "A source claim.", session_id=a["session_id"], source="FM03.pdf", citation="p.10")
        path = self.sc.finalize(a["session_id"])
        trace = yaml.safe_load(path.read_text())
        self.assertEqual(trace["status"], "pending")
        self.assertEqual(trace["user_reasoning"][0]["provenance"], "USER")
        self.assertEqual(trace["source_claims"][0]["provenance"], "SOURCE")
        self.assertIn("FM03.pdf", trace["sources"])

    def test_ledger_event_upsert_is_idempotent_and_streaming_safe(self):
        meta = self.sc.start("fin3703a", ["repo"])
        sid = meta["session_id"]
        first = self.sc.upsert_ledger_event(
            sid,
            "AGENT_EXPLANATION",
            "A repo is",
            ledger_event_key="ledger-a1",
        )
        same = self.sc.upsert_ledger_event(
            sid,
            "AGENT_EXPLANATION",
            "A repo is",
            ledger_event_key="ledger-a1",
        )
        final = self.sc.upsert_ledger_event(
            sid,
            "AGENT_EXPLANATION",
            "A repo is a collateralized funding transaction.",
            ledger_event_key="ledger-a1",
        )
        events = self.sc._read_events(sid)
        self.assertTrue(first["inserted"])
        self.assertFalse(same["inserted"])
        self.assertFalse(same["updated"])
        self.assertTrue(final["updated"])
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["content"],
            "A repo is a collateralized funding transaction.",
        )
        self.assertEqual(
            events[0]["metadata"]["capture_update_count"],
            1,
        )
        self.assertFalse(events[0]["retain_in_trace"])
        self.assertEqual(self.sc._load_meta(sid)["event_count"], 1)

    def test_message_id_deduplicates_cross_surface_observations(self):
        meta = self.sc.start("fin3703a", ["SOFR futures"])
        sid = meta["session_id"]
        first = self.sc.upsert_ledger_event(
            sid,
            "USER_QUESTION",
            "SOFR futures hedge 应该选哪个 delivery month？",
            ledger_event_key="public-ledger-key",
            metadata={
                "message_id": "turn-123:user",
                "conversation_id": "public-conversation",
            },
        )
        duplicate = self.sc.upsert_ledger_event(
            sid,
            "USER_QUESTION",
            "SOFR futures hedge 应该选哪个 delivery month？",
            ledger_event_key="local-ledger-key",
            metadata={
                "message_id": "turn-123:user",
                "conversation_id": "local-chatgpt:internal-alias",
            },
        )
        events = self.sc._read_events(sid)
        self.assertTrue(first["inserted"])
        self.assertFalse(duplicate["inserted"])
        self.assertFalse(duplicate["updated"])
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["metadata"]["conversation_id"],
            "public-conversation",
        )


if __name__ == "__main__":
    unittest.main()
