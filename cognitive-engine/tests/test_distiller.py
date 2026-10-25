import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "distiller.py"
spec = importlib.util.spec_from_file_location("distiller", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
Distiller = mod.Distiller


class DistillerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.engine = base / "cognitive-engine"
        self.vault = base / "obsidian-vault"
        (self.engine / "cognitive-inbox" / "pending").mkdir(parents=True)
        (self.vault / "wiki" / "concepts" / "finance").mkdir(parents=True)
        (self.vault / "wiki" / "summaries" / "finance").mkdir(parents=True)
        (self.vault / "raw").mkdir(parents=True)
        (self.vault / "wiki" / "concepts" / "finance" / "duration.md").write_text(
            "# Duration\nDuration measures bond price sensitivity to interest-rate changes.\n",
            encoding="utf-8",
        )
        (self.vault / "wiki" / "summaries" / "finance" / "lecture.md").write_text(
            "Duration lecture summary should not be current-knowledge retrieval.", encoding="utf-8"
        )
        (self.vault / "raw" / "source.txt").write_text("duration raw source", encoding="utf-8")
        self.trace_path = self.engine / "cognitive-inbox" / "pending" / "s1.yaml"
        trace = {
            "schema_version": 1,
            "session_id": "s1",
            "learning_unit_id": "fin3703a",
            "topic_chain": ["duration", "interest rate risk"],
            "user_questions": [{"content": "Why does duration measure interest rate risk?", "provenance": "USER"}],
            "user_reasoning": [{"content": "Duration is a sensitivity measure, not just maturity.", "provenance": "USER"}],
            "user_corrections": [],
            "user_connections": [],
            "source_claims": [],
            "open_questions": [],
            "status": "pending",
        }
        self.trace_path.write_text(yaml.safe_dump(trace, sort_keys=False), encoding="utf-8")
        self.d = Distiller(self.engine, self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_retrieves_current_knowledge_not_summaries_or_raw(self):
        packet = self.d.prepare(self.trace_path)
        data = yaml.safe_load(packet.read_text())
        paths = [x["path"] for x in data["current_knowledge"]]
        self.assertIn("wiki/concepts/finance/duration.md", paths)
        self.assertTrue(all("summaries" not in p and not p.startswith("raw/") for p in paths))

    def test_emit_validates_and_moves_trace_to_processed(self):
        packet = self.d.prepare(self.trace_path)
        proposals = [{
            "type": "REFINE",
            "target": "duration",
            "before": "Duration is mainly a time measure.",
            "after": "Duration is also a first-order sensitivity measure for bond prices.",
            "reason": "User explicitly distinguished duration from maturity.",
            "provenance": "USER",
            "confidence": 0.9,
            "trace_evidence": ["user_reasoning[0]"],
            "current_knowledge_refs": ["wiki/concepts/finance/duration.md"],
        }]
        out = self.d.emit(packet, proposals)
        batch = yaml.safe_load(out.read_text())
        self.assertEqual(batch["deltas"][0]["review_status"], "pending")
        self.assertFalse(self.trace_path.exists())
        processed = self.engine / "cognitive-inbox" / "processed" / "s1.yaml"
        self.assertTrue(processed.exists())
        self.assertEqual(yaml.safe_load(processed.read_text())["status"], "processed")

    def test_distiller_cannot_auto_approve(self):
        packet = self.d.prepare(self.trace_path)
        proposals = [{
            "type": "NEW", "target": "duration", "after": "x", "reason": "r",
            "provenance": "USER", "confidence": 0.8, "trace_evidence": ["user_reasoning[0]"],
            "review_status": "approved",
        }]
        with self.assertRaises(ValueError):
            self.d.emit(packet, proposals)

    def test_insight_candidate_requires_user_grounding(self):
        packet = self.d.prepare(self.trace_path)
        proposals = [{
            "type": "INSIGHT_CANDIDATE", "target": "duration", "after": "x", "reason": "r",
            "provenance": "AGENT", "confidence": 0.7, "trace_evidence": ["user_reasoning[0]"],
        }]
        with self.assertRaises(ValueError):
            self.d.emit(packet, proposals)

    def test_empty_delta_batch_is_valid(self):
        packet = self.d.prepare(self.trace_path)
        out = self.d.emit(packet, [], summary="No durable change")
        batch = yaml.safe_load(out.read_text())
        self.assertEqual(batch["review_status"], "no_change")
        self.assertEqual(batch["deltas"], [])

    def test_human_review_moves_resolved_batch(self):
        packet = self.d.prepare(self.trace_path)
        out = self.d.emit(packet, [{
            "type": "QUESTION", "target": "duration", "after": "How does convexity change the approximation?",
            "reason": "Open question remains after the session.", "provenance": "USER", "confidence": 1.0,
            "trace_evidence": ["user_questions[0]"],
        }])
        batch = yaml.safe_load(out.read_text())
        did = batch["deltas"][0]["delta_id"]
        reviewed = self.d.review(out, did, "approve", "Keep as open question")
        self.assertIn("approved", str(reviewed))
        final = yaml.safe_load(reviewed.read_text())
        self.assertEqual(final["review_status"], "approved")
        self.assertEqual(final["deltas"][0]["review_status"], "approved")


if __name__ == "__main__":
    unittest.main()
