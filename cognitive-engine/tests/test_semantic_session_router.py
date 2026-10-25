from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.semantic_session_router import SemanticSessionRouter


UNIT = "fin3703a-financial-markets-2610"


class SemanticSessionRouterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        runtime = self.root / "runtime"
        sessions_dir = runtime / "sessions"
        sessions_dir.mkdir(parents=True)

        state = {
            "version": 2,
            "learning_threads": {
                UNIT: {
                    "unit_id": UNIT,
                    "title": "FIN3703A Financial Markets [2610]",
                    "learning_state": "Learning",
                    "thread_state": "active",
                    "sessions": {},
                    "recent_session_id": None,
                }
            },
        }
        (runtime / "learning-threads.yaml").write_text(
            yaml.safe_dump(state, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        self.router = SemanticSessionRouter(self.root)
        self.duration = self.router.capture.start(
            UNIT,
            ["duration", "duration gap", "Macaulay duration"],
        )["session_id"]
        self.repo = self.router.capture.start(
            UNIT,
            ["repo haircut repo rate", "money market instruments"],
        )["session_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_reuses_clear_existing_session(self):
        out = self.router.route(
            UNIT,
            "duration gap 为正时利率上升银行 equity value 怎么变化？",
        )
        self.assertEqual(out.action, "reuse_existing_session")
        self.assertEqual(out.session_id, self.duration)

    def test_new_topic_starts_new_session(self):
        out = self.router.route(
            UNIT,
            "yield curve inverted 通常意味着什么？",
        )
        self.assertEqual(out.action, "start_new_session")
        self.assertIsNone(out.session_id)

    def test_ambiguous_overlap_stays_session_unresolved(self):
        # "money market duration" intentionally touches both synthetic sessions.
        out = self.router.route(
            UNIT,
            "money market duration 怎么理解？",
        )
        self.assertEqual(out.action, "unresolved_session")
        self.assertIsNone(out.session_id)

    def test_no_existing_session_starts_new(self):
        other = "fin3701a-corporate-finance-2610"
        state = yaml.safe_load((self.root / "runtime" / "learning-threads.yaml").read_text())
        state["learning_threads"][other] = {
            "unit_id": other,
            "title": "FIN3701A Corporate Finance [2610]",
            "learning_state": "Learning",
            "thread_state": "active",
            "sessions": {},
        }
        (self.root / "runtime" / "learning-threads.yaml").write_text(
            yaml.safe_dump(state, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        out = self.router.route(other, "WACC 为什么用税后债务成本？")
        self.assertEqual(out.action, "start_new_session")


if __name__ == "__main__":
    unittest.main()
