#!/usr/bin/env python3
"""Academic Dashboard v2: optional quests, reviewed XP, gated level exams.

No daily learning event automatically grants XP or grades. Only reviewed evidence
may earn XP; full XP unlocks an optional formal exam. The module writes no sibling data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from urllib.parse import urlparse
from uuid import uuid4

import growth

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC = ROOT / "static" / "index.html"

SUBJECTS = [
    {"id": "mathematics", "name": "数学", "english": "Mathematics"},
    {"id": "statistics", "name": "统计学", "english": "Statistics"},
    {"id": "computer_science", "name": "计算机科学", "english": "Computer Science"},
    {"id": "artificial_intelligence", "name": "人工智能", "english": "Artificial Intelligence"},
    {"id": "economics", "name": "经济学", "english": "Economics"},
    {"id": "finance", "name": "金融学", "english": "Finance"},
    {"id": "physics", "name": "物理学", "english": "Physics"},
    {"id": "biology", "name": "生物学", "english": "Biology"},
]
SUBJECT_IDS = {s["id"] for s in SUBJECTS}
LEVELS = [
    {"level": 1, "name": "基础", "stage": "小学", "standard": "掌握基础概念、规则和方法，能够完成简单任务。"},
    {"level": 2, "name": "初级", "stage": "初中", "standard": "理解基础原理，建立初步知识体系，能够独立解决常规问题。"},
    {"level": 3, "name": "中级", "stage": "高中", "standard": "系统掌握学科基础知识，能够综合运用知识解决较复杂的问题。"},
    {"level": 4, "name": "高级", "stage": "本科", "standard": "具备系统专业知识与理论基础，能独立分析、推导和解决典型专业问题。"},
    {"level": 5, "name": "专家", "stage": "硕士", "standard": "深入掌握高级理论与专业方法，能处理复杂开放问题并开展初步研究。"},
    {"level": 6, "name": "研究者", "stage": "博士", "standard": "能识别研究问题、独立开展严谨研究并形成原创成果。"},
    {"level": 7, "name": "先驱", "stage": "学术前沿", "standard": "在特定方向推动有重要价值的理论、方法或发现创新。"},
]
SOURCES = {"learning-hub", "cognitive-engine", "obsidian-vault", "external"}


class AssessmentError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.dir = Path(data_dir)
        self.file = self.dir / "state.json"
        self.lock = RLock()

    def _load(self) -> dict:
        if not self.file.exists():
            return {"profiles": {}, "assessments": []}
        with self.file.open(encoding="utf-8") as f:
            return json.load(f)

    def _save(self, payload: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.file)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def overview(self) -> dict:
        with self.lock:
            state = self._load()
            subjects = []
            for s in SUBJECTS:
                current = state["profiles"].get(s["id"])
                subjects.append({**s, "level": current["level"] if current else None,
                                 "verified_at": current["verified_at"] if current else None,
                                 "assessment_id": current["assessment_id"] if current else None})
            # Historical assessments are immutable after their recorded decision.
            return growth.extend_snapshot(state, {"subjects": subjects, "levels": LEVELS,
                    "assessments": list(reversed(state["assessments"]))})

    def propose(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise AssessmentError("请求必须是 JSON 对象")
        subject = body.get("subject")
        target = body.get("target_level")
        if subject not in SUBJECT_IDS:
            raise AssessmentError("未知学科")
        if type(target) is not int or not 1 <= target <= 7:
            raise AssessmentError("目标 Level 必须是 1–7 的整数")
        raw = body.get("evidence")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
            raise AssessmentError("至少需要一条证据")
        evidence = []
        for item in raw:
            if not isinstance(item, dict):
                raise AssessmentError("证据格式错误")
            source = item.get("source")
            reference = item.get("reference")
            summary = item.get("summary")
            if source not in SOURCES or not isinstance(reference, str) or not reference.strip() or len(reference) > 1200:
                raise AssessmentError("证据来源或引用无效")
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
                raise AssessmentError("证据描述不能为空")
            evidence.append({"source": source, "reference": reference.strip(), "summary": summary.strip()})
        with self.lock:
            state = self._load()
            profile = state["profiles"].get(subject)
            if profile is None:
                mode = "placement"
            else:
                if profile["level"] == 7 or target != profile["level"] + 1:
                    raise AssessmentError("已有等级只能申请下一级晋升；Lv.7 无更高等级")
                stage = growth.progress(state, subject)
                if not stage["unlocked"]:
                    raise AssessmentError("当前级 XP 未满或晋级阈值未配置，暂不能发起晋级考核")
                mode = "promotion"
            item = {"id": uuid4().hex[:12], "subject": subject,
                    "target_level": target, "mode": mode, "evidence": evidence,
                    "status": "pending", "created_at": now(), "decision": None}
            state["assessments"].append(item)
            self._save(state)
        return item

    def decide(self, assessment_id: str, body: dict) -> dict:
        if not isinstance(body, dict):
            raise AssessmentError("请求必须是 JSON 对象")
        choice = body.get("outcome")
        reviewer = body.get("reviewer")
        note = body.get("note")
        if choice not in ("approved", "rejected"):
            raise AssessmentError("审核结果只能是 approved / rejected")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 120:
            raise AssessmentError("审核人不能为空")
        if not isinstance(note, str) or not note.strip() or len(note) > 4000:
            raise AssessmentError("必须记录明确的审核理由")
        checks = body.get("checks", {})
        if choice == "approved":
            if not isinstance(checks, dict) or not all(checks.get(k) is True for k in ("coverage", "independence", "provenance")):
                raise AssessmentError("通过评级前须逐项确认知识覆盖、独立能力和证据可核实性")
        with self.lock:
            state = self._load()
            item = next((a for a in state["assessments"] if a["id"] == assessment_id), None)
            if item is None:
                raise AssessmentError("未找到考核记录")
            if item["status"] != "pending":
                raise AssessmentError("考核已经结束，不能重复评级")
            current = state["profiles"].get(item["subject"])
            if choice == "approved":
                if current is None:
                    if item.get("mode", "placement") != "placement":
                        raise AssessmentError("评级状态已改变，请重新发起考核")
                else:
                    if item.get("mode") != "promotion" or item["target_level"] != current["level"] + 1:
                        raise AssessmentError("当前等级与申请不匹配，请重新发起晋级考核")
                    if not growth.progress(state, item["subject"])["unlocked"]:
                        raise AssessmentError("当前级 XP 不足或阈值已改变，请重新检查资格")
            item["status"] = choice
            item["decision"] = {"reviewer": reviewer.strip(), "note": note.strip(),
                                "checks": checks if choice == "approved" else {},
                                "decided_at": now(), "method": "human-review"}
            if choice == "approved":
                state["profiles"][item["subject"]] = {
                    "level": item["target_level"], "assessment_id": item["id"],
                    "stage_id": uuid4().hex, "verified_at": item["decision"]["decided_at"]}
            self._save(state)
            return item

    def apply_growth(self, op, *args):
        with self.lock:
            state = self._load()
            result = op(state, *args)
            self._save(state)
            return result


class Handler(BaseHTTPRequestHandler):
    store: Store = Store()

    def reply(self, code: int, value: dict):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            return self.reply(200, self.store.overview())
        if path == "/growth.js":
            raw = (ROOT / "static" / "growth.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return self.wfile.write(raw)
        if path in ("/", "/index.html"):
            raw = STATIC.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return self.wfile.write(raw)
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        allowed = {f"http://{host}", f"https://{host}"}
        if origin and origin not in allowed:
            return self.reply(403, {"error": "跨站写入已阻止"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.reply(415, {"error": "必须使用 application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 100_000:
                raise AssessmentError("请求体大小不合法")
            payload = json.loads(self.rfile.read(size))
            path = urlparse(self.path).path
            if path == "/api/assessments":
                return self.reply(201, {"assessment": self.store.propose(payload)})
            if path == "/api/xp-policy":
                return self.reply(200, {"policy": self.store.apply_growth(growth.policy, payload)})
            if path == "/api/quests":
                return self.reply(201, {"quest": self.store.apply_growth(growth.quest, payload, SUBJECT_IDS)})
            if path == "/api/achievements":
                return self.reply(201, {"achievement": self.store.apply_growth(growth.achievement, payload, SUBJECT_IDS)})
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "assessments"] and parts[3] == "decision":
                return self.reply(200, {"assessment": self.store.decide(parts[2], payload)})
            if len(parts) == 4 and parts[:2] == ["api", "quests"]:
                quest_id, action = parts[2:]
                if action == "accept":
                    return self.reply(200, {"quest": self.store.apply_growth(growth.accept, quest_id)})
                if action == "abandon":
                    return self.reply(200, {"quest": self.store.apply_growth(growth.abandon, quest_id)})
                if action == "submit":
                    return self.reply(200, {"quest": self.store.apply_growth(growth.submit, quest_id, payload)})
                if action == "review":
                    return self.reply(200, {"quest": self.store.apply_growth(growth.review, "quest", quest_id, payload)})
            if len(parts) == 4 and parts[:2] == ["api", "achievements"] and parts[3] == "review":
                return self.reply(200, {"achievement": self.store.apply_growth(growth.review, "achievement", parts[2], payload)})
            return self.reply(404, {"error": "not found"})
        except (AssessmentError, growth.GrowthError, ValueError, json.JSONDecodeError) as exc:
            return self.reply(400, {"error": str(exc)})

    def log_message(self, fmt, *args):
        # No learning records or personal evidence in request logs.
        pass


def main():
    parser = argparse.ArgumentParser(description="Independent academic evaluation dashboard")
    subs = parser.add_subparsers(dest="command", required=True)
    serve = subs.add_parser("serve", help="serve the local browser panel")
    serve.add_argument("--port", type=int, default=8787)
    subs.add_parser("status", help="print academic levels and pending reviews")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(Store().overview(), ensure_ascii=False, indent=2))
    else:
        if not 1 <= args.port <= 65535:
            parser.error("port out of range")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"Academic Dashboard: http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
