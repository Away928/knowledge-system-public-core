"""On-demand quest, evidence review, and XP ledger; independent of learning workflows."""
from __future__ import annotations
from uuid import uuid4
from datetime import datetime, timezone

QUEST_KINDS = {
    "knowledge": {"name": "知识巩固", "xp": 10},
    "application": {"name": "应用实践", "xp": 30},
    "research": {"name": "研究探索", "xp": 60},
}
SOURCES = {"learning-hub", "cognitive-engine", "obsidian-vault", "external"}


class GrowthError(ValueError):
    pass


def time_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dict(value):
    if not isinstance(value, dict):
        raise GrowthError("请求必须是 JSON 对象")
    return value


def required_text(body, field, max_len=4000):
    val = body.get(field)
    if not isinstance(val, str) or not val.strip() or len(val) > max_len:
        raise GrowthError(f"{field} 不能为空或超出长度限制")
    return val.strip()


def evidence_from(body):
    evidence = ensure_dict(body.get("evidence"))
    source = required_text(evidence, "source", 50)
    reference = required_text(evidence, "reference", 1200)
    summary = required_text(evidence, "summary")
    if source not in SOURCES:
        raise GrowthError("未知证据来源")
    return {"source": source, "reference": reference, "summary": summary}


def review_fields(body):
    body = ensure_dict(body)
    outcome = body.get("outcome")
    if outcome not in ("approved", "rejected"):
        raise GrowthError("审核结果必须是 approved 或 rejected")
    reviewer = required_text(body, "reviewer", 120)
    note = required_text(body, "note")
    if outcome == "approved":
        checks = ensure_dict(body.get("checks"))
        if not all(checks.get(k) is True for k in ("provenance", "independence", "originality")):
            raise GrowthError("须确认来源、独立完成、未重复计奖三项审核条件")
    return outcome, reviewer, note


def xp_total(state, subject, stage_id):
    return sum(a["applied_xp"] for a in state.get("awards", [])
               if a["subject"] == subject and a.get("stage_id") == stage_id)


def stage_id(profile):
    return profile.get("stage_id") or "legacy-" + profile["assessment_id"]


def progress(state, subject):
    profile = state["profiles"].get(subject)
    if profile is None:
        return {"xp": 0, "required_xp": None, "unlocked": False, "reason": "unassessed"}
    lv = profile["level"]
    if lv >= 7:
        return {"xp": 0, "required_xp": None, "unlocked": False, "reason": "max_level"}
    requirement = state.get("xp_requirements", {}).get(str(lv))
    earned = xp_total(state, subject, stage_id(profile))
    if requirement is None:
        return {"xp": earned, "required_xp": None, "unlocked": False, "reason": "threshold_unset"}
    return {"xp": min(earned, requirement), "required_xp": requirement,
            "unlocked": earned >= requirement, "reason": "ready" if earned >= requirement else "earning"}


def policy(state, body):
    ensure_dict(body)
    level = body.get("level")
    required_xp = body.get("required_xp")
    if type(level) is not int or not 1 <= level <= 6:
        raise GrowthError("仅能设置 Lv.1–Lv.6 的晋级要求")
    if type(required_xp) is not int or not 1 <= required_xp <= 100000:
        raise GrowthError("目标 XP 必须是 1–100000 的整数")
    reason = required_text(body, "rationale", 1000)
    state.setdefault("xp_requirements", {})[str(level)] = required_xp
    state.setdefault("policy_history", []).append({
        "id": uuid4().hex[:12], "level": level, "required_xp": required_xp,
        "rationale": reason, "at": time_now()})
    return {"level": level, "required_xp": required_xp, "rationale": reason}


def quest(state, body, subjects):
    ensure_dict(body)
    subject = body.get("subject")
    kind = body.get("kind")
    if subject not in subjects or kind not in QUEST_KINDS:
        raise GrowthError("未知学科或任务类型")
    title = required_text(body, "title", 160)
    description = required_text(body, "description", 1600)
    criterion = required_text(body, "criterion", 1200)
    task = {"id": uuid4().hex[:12], "subject": subject, "kind": kind,
            "title": title, "description": description, "criterion": criterion,
            "xp": QUEST_KINDS[kind]["xp"], "status": "published",
            "created_at": time_now(), "evidence": None, "reviews": []}
    state.setdefault("quests", []).append(task)
    return task


def get_quest(state, quest_id):
    task = next((q for q in state.get("quests", []) if q["id"] == quest_id), None)
    if task is None:
        raise GrowthError("找不到任务")
    return task


def accept(state, quest_id):
    task = get_quest(state, quest_id)
    if task["status"] not in ("published",):
        raise GrowthError("当前任务不能重复领取")
    task["status"] = "accepted"
    return task


def abandon(state, quest_id):
    task = get_quest(state, quest_id)
    if task["status"] != "accepted":
        raise GrowthError("只能放弃已领取、尚未提交的任务")
    task["status"] = "published"
    return task


def submit(state, quest_id, body):
    task = get_quest(state, quest_id)
    if task["status"] not in ("accepted", "rejected"):
        raise GrowthError("任务需先领取才能提交成果")
    task["evidence"] = evidence_from(body)
    task["status"] = "submitted"
    return task


def achievement(state, body, subjects):
    ensure_dict(body)
    subject = body.get("subject")
    kind = body.get("kind")
    if subject not in subjects or kind not in QUEST_KINDS:
        raise GrowthError("未知学科或成果类型")
    record = {"id": uuid4().hex[:12], "subject": subject, "kind": kind,
              "title": required_text(body, "title", 160), "xp": QUEST_KINDS[kind]["xp"],
              "evidence": evidence_from(body), "status": "submitted",
              "created_at": time_now(), "reviews": []}
    state.setdefault("achievements", []).append(record)
    return record


def review(state, record_type, record_id, body):
    entries = state.get("quests" if record_type == "quest" else "achievements", [])
    entry = next((a for a in entries if a["id"] == record_id), None)
    if entry is None:
        raise GrowthError("找不到对应的成果")
    if entry["status"] != "submitted":
        raise GrowthError("只能审核已经提交的成果")
    outcome, reviewer, note = review_fields(body)
    if outcome == "approved":
        if not entry["evidence"]:
            raise GrowthError("缺少成果证据")
        profile = state["profiles"].get(entry["subject"])
        if profile is None:
            raise GrowthError("尚未完成初始学科定级，无法发放 XP")
        original = (entry["evidence"]["source"], entry["evidence"]["reference"].casefold().strip())
        for award in state.get("awards", []):
            old_e = award["evidence"]
            if award["subject"] == entry["subject"] and (old_e["source"], old_e["reference"].casefold().strip()) == original:
                raise GrowthError("此证据在该学科已获得 XP，禁止重复计奖")
        stat = progress(state, entry["subject"])
        if stat["reason"] == "max_level":
            applied = 0
        elif stat["required_xp"] is None:
            applied = entry["xp"]
        else:
            applied = min(entry["xp"], max(0, stat["required_xp"] - stat["xp"]))
        award = {"id": uuid4().hex[:12], "subject": entry["subject"],
                 "source_type": record_type, "source_id": entry["id"],
                 "requested_xp": entry["xp"], "applied_xp": applied,
                 "stage_id": stage_id(profile), "evidence": entry["evidence"],
                 "reviewer": reviewer, "note": note, "awarded_at": time_now()}
        state.setdefault("awards", []).append(award)
    entry["status"] = "completed" if outcome == "approved" else "rejected"
    entry["reviews"].append({"outcome": outcome, "reviewer": reviewer, "note": note, "at": time_now()})
    return entry


def extend_snapshot(state, view):
    for subject in view["subjects"]:
        subject["progress"] = progress(state, subject["id"])
    view.update({
        "quests": list(reversed(state.get("quests", []))),
        "achievements": list(reversed(state.get("achievements", []))),
        "awards": list(reversed(state.get("awards", []))),
        "xp_requirements": state.get("xp_requirements", {}),
        "quest_kinds": QUEST_KINDS,
    })
    return view
