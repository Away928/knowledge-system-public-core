import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

from app import Handler, Store, AssessmentError
import growth


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def propose(self, subject="mathematics", level=4):
        return self.store.propose({
            "subject": subject, "target_level": level,
            "evidence": [{"source": "cognitive-engine", "reference": "trace:test-001",
                          "summary": "独立推导并解决典型任务，保留可复核的原始过程。"}]})

    def test_unassessed_is_not_zero_or_one(self):
        view = self.store.overview()
        self.assertEqual(8, len(view["subjects"]))
        self.assertEqual("生物学", next(s["name"] for s in view["subjects"] if s["id"] == "biology"))
        self.assertTrue(all(s["level"] is None for s in view["subjects"]))
        self.assertNotIn("mastery", json.dumps(view))

    def test_pending_does_not_infer_level(self):
        proposed = self.propose()
        self.assertEqual("pending", proposed["status"])
        self.assertIsNone(self.store.overview()["subjects"][0]["level"])

    def test_reject_does_not_change_level(self):
        proposed = self.propose()
        self.store.decide(proposed["id"], {"outcome": "rejected",
                         "reviewer": "Human", "note": "证据尚不充分"})
        self.assertIsNone(self.store.overview()["subjects"][0]["level"])

    def test_approval_requires_three_independent_checks(self):
        proposed = self.propose()
        with self.assertRaises(AssessmentError):
            self.store.decide(proposed["id"], {
                "outcome": "approved", "reviewer": "Human",
                "note": "有效", "checks": {"coverage": True, "independence": True}})
        self.assertIsNone(self.store.overview()["subjects"][0]["level"])
        approved = self.store.decide(proposed["id"], {
            "outcome": "approved", "reviewer": "Human",
            "note": "已逐项核对材料并确认达到要求",
            "checks": {"coverage": True, "independence": True, "provenance": True}})
        self.assertEqual("approved", approved["status"])
        self.assertEqual(4, self.store.overview()["subjects"][0]["level"])

    def test_reassessment_and_review_history(self):
        first = self.propose("finance", 3)
        check = {"coverage": True, "independence": True, "provenance": True}
        self.store.decide(first["id"], {"outcome": "approved", "reviewer": "Human", "note": "初评", "checks": check})
        with self.assertRaises(AssessmentError):
            self.propose("finance", 4)
        self.store.apply_growth(growth.policy, {"level": 3, "required_xp": 10, "rationale": "测试阈值"})
        award = self.store.apply_growth(growth.achievement, {
            "subject": "finance", "kind": "knowledge", "title": "模型推导",
            "evidence": {"source": "external", "reference": "proof:finance",
                         "summary": "独立完成并核对推导"}}, {"finance"})
        self.store.apply_growth(growth.review, "achievement", award["id"],
            {"outcome": "approved", "reviewer": "Human", "note": "证据通过",
             "checks": {"provenance": True, "independence": True, "originality": True}})
        second = self.propose("finance", 4)
        self.store.decide(second["id"], {"outcome": "approved", "reviewer": "Human", "note": "已通过独立考核", "checks": check})
        view = self.store.overview()
        finance = next(s for s in view["subjects"] if s["id"] == "finance")
        self.assertEqual(4, finance["level"])
        self.assertEqual(0, finance["progress"]["xp"])
        self.assertEqual(2, len(view["assessments"]))
        with self.assertRaises(AssessmentError):
            self.store.decide(first["id"], {"outcome": "rejected", "reviewer": "Human", "note": "重复操作"})

    def test_invalid_inputs(self):
        for bad in [
            {"subject": "wrong", "target_level": 1, "evidence": []},
            {"subject": "mathematics", "target_level": True, "evidence": []},
            {"subject": "mathematics", "target_level": 8, "evidence": []},
            {"subject": "mathematics", "target_level": 3, "evidence": []},
            {"subject": "mathematics", "target_level": 3, "evidence":
             [{"source": "unknown", "reference": "id", "summary": "说明"}]}
        ]:
            with self.assertRaises(AssessmentError):
                self.store.propose(bad)
        self.assertFalse(self.store.file.exists())


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_store = Handler.store
        Handler.store = Store(Path(self.tmp.name))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = "http://127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        Handler.store = self.old_store
        self.tmp.cleanup()

    def test_web_ui_and_end_to_end(self):
        with urlopen(self.root + "/") as r:
            self.assertIn(b"Academic Dashboard", r.read())
        with urlopen(self.root + "/api/state") as r:
            state = json.load(r)
            self.assertIsNone(state["subjects"][0]["level"])

        payload = {"subject": "artificial_intelligence", "target_level": 2,
                   "evidence": [{"source": "external", "reference": "quiz-001",
                                 "summary": "在独立考核中完成受限问题，待人工复核"}]}
        req = Request(self.root + "/api/assessments",
                      data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(req) as r:
            item = json.load(r)["assessment"]
        with urlopen(self.root + "/api/state") as r:
            self.assertIsNone(next(s for s in json.load(r)["subjects"] if s["id"] == "artificial_intelligence")["level"])

        decision = {"outcome": "approved", "reviewer": "Human",
                    "note": "人工复核已完成", "checks": {
                        "coverage": True, "independence": True, "provenance": True}}
        req = Request(self.root + "/api/assessments/" + item["id"] + "/decision",
                      data=json.dumps(decision).encode(), headers={"Content-Type": "application/json"})
        with urlopen(req) as r:
            self.assertEqual("approved", json.load(r)["assessment"]["status"])
        with urlopen(self.root + "/api/state") as r:
            self.assertEqual(2, next(s for s in json.load(r)["subjects"] if s["id"] == "artificial_intelligence")["level"])

    def test_http_quest_xp_and_promotion_unlock(self):
        def post(path, body):
            request = Request(self.root + path, data=json.dumps(body).encode("utf-8"),
                              headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                return json.load(response)
        evidence = [{"source": "external", "reference": "exam:initial",
                     "summary": "完整初始能力考核"}]
        initial = post("/api/assessments", {
            "subject": "mathematics", "target_level": 4, "evidence": evidence})["assessment"]
        academic_checks = {"coverage": True, "independence": True, "provenance": True}
        post("/api/assessments/" + initial["id"] + "/decision", {
            "outcome": "approved", "reviewer": "Human", "note": "初次能力认定",
            "checks": academic_checks})
        post("/api/xp-policy", {"level": 4, "required_xp": 30,
                               "rationale": "隔离的端到端测试阈值"})
        task = post("/api/quests", {
            "subject": "mathematics", "kind": "application",
            "title": "独立推导", "description": "完成数学问题推导",
            "criterion": "提供可检查的完整演算"})["quest"]
        post("/api/quests/" + task["id"] + "/accept", {})
        post("/api/quests/" + task["id"] + "/submit", {
            "evidence": {"source": "external", "reference": "proof:actual",
                         "summary": "已提交演算及复核记录"}})
        post("/api/quests/" + task["id"] + "/review", {
            "outcome": "approved", "reviewer": "Human", "note": "核实完成",
            "checks": {"provenance": True, "independence": True, "originality": True}})
        with urlopen(self.root + "/api/state") as response:
            state = json.load(response)
        subject = next(s for s in state["subjects"] if s["id"] == "mathematics")
        self.assertEqual(4, subject["level"])
        self.assertEqual(30, subject["progress"]["xp"])
        self.assertTrue(subject["progress"]["unlocked"])
        exam = post("/api/assessments", {
            "subject": "mathematics", "target_level": 5,
            "evidence": [{"source": "external", "reference": "exam:next",
                          "summary": "下一等级正式考核"}]})["assessment"]
        post("/api/assessments/" + exam["id"] + "/decision", {
            "outcome": "rejected", "reviewer": "Human",
            "note": "能力未达标，保留本级 XP"})
        with urlopen(self.root + "/api/state") as response:
            state = json.load(response)
        subject = next(s for s in state["subjects"] if s["id"] == "mathematics")
        self.assertEqual(4, subject["level"])
        self.assertEqual(30, subject["progress"]["xp"])

    def test_cross_origin_write_blocked(self):
        req = Request(self.root + "/api/assessments", data=b"{}",
                      headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req)
        self.assertEqual(403, ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
