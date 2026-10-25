from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

import yaml

from scripts.learning_router import LearningUnitRouter


F3701 = "fin3701a-corporate-finance-2610"
F3703 = "fin3703a-financial-markets-2610"


class LearningUnitRouterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        runtime = self.root / "runtime"
        runtime.mkdir()
        state = {
            "learning_threads": {
                F3701: {
                    "unit_id": F3701,
                    "title": "FIN3701A Corporate Finance [2610]",
                    "learning_state": "Learning",
                    "thread_state": "active",
                    "sessions": {},
                },
                F3703: {
                    "unit_id": F3703,
                    "title": "FIN3703A Financial Markets [2610]",
                    "learning_state": "Learning",
                    "thread_state": "active",
                    "sessions": {
                        "s1": {
                            "topic_chain": ["duration gap", "SOFR futures", "repo haircut"]
                        }
                    },
                },
            }
        }
        (runtime / "learning-threads.yaml").write_text(
            yaml.safe_dump(state, allow_unicode=True), encoding="utf-8"
        )
        s3701 = runtime / "fin3701a-staging"
        s3701.mkdir()
        (s3701 / "course.xlsx.md").write_text(
            "# Capital Budgeting\nFree Cash Flow NPV WACC beta real option\n"
            "Free Cash Flow NPV WACC beta real option\n",
            encoding="utf-8",
        )
        s3703 = runtime / "fin3703a-staging"
        s3703.mkdir()
        (s3703 / "ignored.md").write_text(
            "WACC WACC WACC", encoding="utf-8"
        )
        self.router = LearningUnitRouter(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sidecar_bootstrap_for_unit_without_sessions(self):
        profiles = self.router.profiles()
        self.assertIn("wacc", profiles[F3701])
        self.assertGreater(profiles[F3701]["wacc"], 0)

    def test_sidecar_bootstrap_does_not_override_session_backed_unit(self):
        profiles = self.router.profiles()
        self.assertNotIn("wacc", profiles[F3703])

    def test_deictic_short_question_stays_unresolved_without_context(self):
        out = self.router.route("这个 beta 是哪一个 beta？")
        self.assertIsNone(out["unit_id"])
        self.assertEqual(out["reason"], "context_required_deictic")

    def test_explicit_course_code_overrides_deictic_guard(self):
        out = self.router.route("FIN3701A 这里的 beta 是什么意思？")
        self.assertEqual(out["unit_id"], F3701)
        self.assertEqual(out["reason"], "explicit_course_code")

    def test_context_can_route_deictic_short_question(self):
        out = self.router.route(
            "这个 beta 是哪一个 beta？",
            previous_unit_id=F3701,
        )
        self.assertEqual(out["unit_id"], F3701)

    def test_cross_unit_comparison_stays_unresolved(self):
        out = self.router.route("WACC和SOFR哪个更适合衡量融资成本？")
        self.assertIsNone(out["unit_id"])
        self.assertEqual(out["reason"], "cross_unit_comparison")

    def test_same_unit_comparison_can_still_route(self):
        out = self.router.route("WACC和beta哪个更重要？")
        self.assertEqual(out["unit_id"], F3701)

    def test_generic_per_annum_does_not_route(self):
        out = self.router.route("per annum是什么意思？")
        self.assertIsNone(out["unit_id"])

    def test_explicit_unit_title_is_strong_signal(self):
        out = self.router.route("corporate finance 里 APR 和 EAR 怎么转换？")
        self.assertEqual(out["unit_id"], F3701)
        self.assertEqual(out["reason"], "explicit_unit_title")

    def test_pdf_phrase_can_correct_single_token_misroute(self):
        self.router.profiles = lambda: {
            F3701: Counter({"yield": 2}),
            F3703: Counter(),
        }
        self.router._pdf_phrase_support = lambda content, units: {
            F3701: 0,
            F3703: 1,
        }
        out = self.router.route("yield curve inverted 是什么意思？")
        self.assertEqual(out["unit_id"], F3703)
        self.assertEqual(out["reason"], "pdf_phrase_correction")

    def test_pdf_phrase_can_complete_cross_unit_comparison_guard(self):
        self.router.profiles = lambda: {
            F3701: Counter({"wacc": 2, "yield": 2}),
            F3703: Counter(),
        }
        self.router._pdf_phrase_support = lambda content, units: {
            F3701: 0,
            F3703: 1,
        }
        out = self.router.route("WACC还是yield curve哪个更适合衡量融资成本？")
        self.assertIsNone(out["unit_id"])
        self.assertEqual(out["reason"], "cross_unit_comparison")


if __name__ == "__main__":
    unittest.main()
