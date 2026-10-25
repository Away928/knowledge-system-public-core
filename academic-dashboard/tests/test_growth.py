import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.request import Request, urlopen

from app import Store, Handler
from growth import GrowthError
import growth


class GrowthFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.aca_checks = {"coverage": True, "independence": True, "provenance": True}
        self.xp_checks = {"provenance": True, "independence": True, "originality": True}
        self.place("mathematics", 4)

    def tearDown(self):
        self.tmp.cleanup()

    def place(self, subject, level):
        exam = self.store.propose({"subject": subject, "target_level": level,
                                   "evidence": [{"source": "external", "reference": "exam:placement",
                                                 "summary": "独立入门评估"}]})
        return self.store.decide(exam["id"], {"outcome": "approved", "reviewer": "Human",
                                            "note": "经人工核实初始能力", "checks": self.aca_checks})

    def policy(self, level=4, threshold=30):
        return self.store.apply_growth(growth.policy, {
            "level": level, "required_xp": threshold, "rationale": "测试中显式配置非正式阈值"})

    def quest(self, kind="application", title="一个项目"):
        return self.store.apply_growth(growth.quest, {
            "subject": "mathematics", "kind": kind, "title": title,
            "description": "独立分析已知问题", "criterion": "交付源文件与数学推导"}, {"mathematics"})

    def approve_quest(self, quest_id, reference="proof:1", outcome="approved"):
        return self.store.apply_growth(growth.review, "quest", quest_id, {
            "outcome": outcome, "reviewer": "Human", "note": "根据原始交付复核",
            "checks": self.xp_checks})

    def complete(self, quest, reference):
        self.store.apply_growth(growth.accept, quest["id"])
        self.store.apply_growth(growth.submit, quest["id"],
            {"evidence": {"source": "external", "reference": reference,
                          "summary": "已验收的原始数学推导"}})
        return self.approve_quest(quest["id"])

    def view(self):
        return next(s for s in self.store.overview()["subjects"] if s["id"] == "mathematics")

    def test_initial_placement_allows_higher_level_without_xp(self):
        self.assertEqual(4, self.view()["level"])
        self.assertEqual("threshold_unset", self.view()["progress"]["reason"])

    def test_biology_has_independent_level_xp_and_quest(self):
        before = self.store.overview()
        biology = next(s for s in before["subjects"] if s["id"] == "biology")
        self.assertIsNone(biology["level"])
        self.assertEqual(0, biology["progress"]["xp"])
        self.place("biology", 3)
        self.store.apply_growth(growth.policy, {
            "level": 3, "required_xp": 10, "rationale": "测试生物学独立进度"})
        item = self.store.apply_growth(growth.achievement, {
            "subject": "biology", "kind": "knowledge", "title": "细胞结构讲解",
            "evidence": {"source": "external", "reference": "proof:biology",
                         "summary": "独立分析细胞结构并提交可核验证据"}}, {"biology"})
        self.store.apply_growth(growth.review, "achievement", item["id"], {
            "outcome": "approved", "reviewer": "Human", "note": "来源与能力已核实",
            "checks": self.xp_checks})
        view = self.store.overview()
        new_bio = next(s for s in view["subjects"] if s["id"] == "biology")
        self.assertEqual(3, new_bio["level"])
        self.assertEqual(10, new_bio["progress"]["xp"])
        self.assertTrue(new_bio["progress"]["unlocked"])
        self.assertEqual(0, self.view()["progress"]["xp"])

    def test_unconfigured_xp_does_not_unlock(self):
        self.complete(self.quest(), "proof:no-policy")
        self.assertEqual(30, self.view()["progress"]["xp"])
        self.assertFalse(self.view()["progress"]["unlocked"])
        with self.assertRaises(ValueError):
            self.store.propose({"subject": "mathematics", "target_level": 5,
                                "evidence": [{"source": "external", "reference": "exam:5", "summary": "考核"}]})

    def test_full_xp_unlocks_assessment_only(self):
        self.policy()
        self.complete(self.quest(), "proof:full")
        self.assertTrue(self.view()["progress"]["unlocked"])
        self.assertEqual(4, self.view()["level"])
        with self.assertRaises(ValueError):
            self.store.propose({"subject": "mathematics", "target_level": 6,
                                "evidence": [{"source": "external", "reference": "exam:skip", "summary": "考核"}]})

    def test_exam_failure_preserves_xp_then_success_resets_progress(self):
        self.policy()
        self.complete(self.quest(), "proof:test1")
        def apply_exam():
            return self.store.propose({"subject": "mathematics", "target_level": 5,
                                      "evidence": [{"source": "external", "reference": "exam:5", "summary": "完整的考核表现"}]})
        failed = apply_exam()
        self.store.decide(failed["id"], {"outcome": "rejected", "reviewer": "Human", "note": "尚未达标"})
        self.assertEqual(30, self.view()["progress"]["xp"])
        self.assertEqual(4, self.view()["level"])
        passed = apply_exam()
        self.store.decide(passed["id"], {"outcome": "approved", "reviewer": "Human",
                                        "note": "通过后续综合考核", "checks": self.aca_checks})
        self.assertEqual(5, self.view()["level"])
        self.assertEqual(0, self.view()["progress"]["xp"])
        self.assertEqual("threshold_unset", self.view()["progress"]["reason"])

    def test_xp_capped_and_task_completion_still_recorded(self):
        self.policy(threshold=20)
        first = self.complete(self.quest(), "proof:cap")
        self.assertEqual("completed", first["status"])
        self.assertEqual(20, self.view()["progress"]["xp"])
        second = self.complete(self.quest("research", "另一个研究"), "proof:cap2")
        self.assertEqual("completed", second["status"])
        awards = self.store.overview()["awards"]
        self.assertEqual([0, 20], [a["applied_xp"] for a in awards])
        self.assertEqual(2, len(awards))

    def test_deduplicates_same_subject_evidence(self):
        self.policy(threshold=200)
        self.complete(self.quest(), "Proof:Unique")
        nxt = self.quest(title="第二个任务")
        self.store.apply_growth(growth.accept, nxt["id"])
        self.store.apply_growth(growth.submit, nxt["id"],
          {"evidence": {"source": "external", "reference": "proof:unique", "summary": "重复材料"}})
        with self.assertRaises(GrowthError):
            self.approve_quest(nxt["id"])
        self.assertEqual(30, self.view()["progress"]["xp"])
        self.assertEqual(1, len(self.store.overview()["awards"]))

    def test_submit_reject_resubmit_then_award_once(self):
        self.policy()
        q = self.quest()
        self.store.apply_growth(growth.accept, q["id"])
        self.store.apply_growth(growth.submit, q["id"],
          {"evidence": {"source": "external", "reference": "proof:retry", "summary": "首轮"}})
        self.approve_quest(q["id"], outcome="rejected")
        self.assertEqual(0, self.view()["progress"]["xp"])
        self.store.apply_growth(growth.submit, q["id"],
          {"evidence": {"source": "external", "reference": "proof:retry2", "summary": "修订"}})
        self.approve_quest(q["id"])
        self.assertEqual(30, self.view()["progress"]["xp"])
        with self.assertRaises(GrowthError):
            self.approve_quest(q["id"])

    def test_autonomous_achievement_requires_review_and_placement(self):
        self.policy()
        self.assertEqual(0, self.view()["progress"]["xp"])
        record = self.store.apply_growth(growth.achievement, {
            "subject": "mathematics", "kind": "knowledge", "title": "自主复现",
            "evidence": {"source": "cognitive-engine", "reference": "trace:100",
                         "summary": "可核验的真实个人推导"}}, {"mathematics"})
        self.assertEqual(0, self.view()["progress"]["xp"])
        with self.assertRaises(GrowthError):
            self.store.apply_growth(growth.review, "achievement", record["id"], {
                "outcome": "approved", "reviewer": "Human", "note": "尚未核查", "checks": {}})
        self.store.apply_growth(growth.review, "achievement", record["id"], {
            "outcome": "approved", "reviewer": "Human", "note": "真实核验",
            "checks": self.xp_checks})
        self.assertEqual(10, self.view()["progress"]["xp"])

    def test_policy_recalibration_does_not_change_level(self):
        self.policy(threshold=70)
        self.complete(self.quest(), "proof:policy")
        self.assertFalse(self.view()["progress"]["unlocked"])
        self.policy(threshold=20)
        self.assertTrue(self.view()["progress"]["unlocked"])
        self.assertEqual(4, self.view()["level"])

    def test_legacy_state_read_only_compatibility(self):
        with self.store.lock:
            self.store._save({"profiles": {"mathematics": {"level": 4, "assessment_id": "legacy-1",
                                             "verified_at": "2026-10-01"}}, "assessments": []})
        self.assertEqual("threshold_unset", self.view()["progress"]["reason"])
        self.policy()
        self.complete(self.quest(), "proof:legacy")
        self.assertEqual(30, self.view()["progress"]["xp"])

    def test_invalid_policy_and_quest_are_rejected(self):
        for bad in [
            {"level": True, "required_xp": 10, "rationale": "bad"},
            {"level": 7, "required_xp": 10, "rationale": "bad"},
            {"level": 4, "required_xp": -1, "rationale": "bad"}
        ]:
            with self.assertRaises(GrowthError):
                self.store.apply_growth(growth.policy, bad)
        with self.assertRaises(GrowthError):
            self.store.apply_growth(growth.quest, {
                "subject": "mathematics", "kind": "invented", "title": "bad",
                "description": "bad", "criterion": "bad"}, {"mathematics"})


if __name__ == "__main__":
    unittest.main()
