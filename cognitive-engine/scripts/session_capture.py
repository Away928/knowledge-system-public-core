#!/usr/bin/env python3
"""Session Capture V1 for the Knowledge System.

The semantic layer (ChatGPT/Claude) decides whether a topic chain is continuous.
This module provides deterministic persistence, candidate resolution, event capture,
and Cognitive Trace finalization. Time is metadata, never a session boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

EVENT_SPECS: dict[str, dict[str, Any]] = {
    "USER_QUESTION": {"provenance": "USER", "retain": True, "strength": "questioned"},
    "USER_REASONING": {"provenance": "USER", "retain": True, "strength": "reasoned"},
    "USER_CORRECTION": {"provenance": "USER", "retain": True, "strength": "corrected"},
    "USER_CONNECTION": {"provenance": "USER", "retain": True, "strength": "connected"},
    "USER_CONFIRMATION": {"provenance": "USER", "retain": True, "strength": None},
    "USER_REJECTION": {"provenance": "USER", "retain": True, "strength": None},
    "SOURCE_CLAIM": {"provenance": "SOURCE", "retain": True, "strength": None},
    # AI material may be useful as context, but must not become durable user knowledge by default.
    "AGENT_EXPLANATION": {"provenance": "AGENT", "retain": False, "strength": None},
    "AGENT_INFERENCE": {"provenance": "AGENT", "retain": False, "strength": None},
    "OPEN_QUESTION": {"provenance": "USER", "retain": True, "strength": "questioned"},
}

OPEN_STATES = {"active", "paused"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str, max_len: int = 48) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return (s or "topic")[:max_len].rstrip("-")


def topic_tokens(values: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for value in values:
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower())
        out.update(x for x in normalized.split() if x)
    return out


def topic_similarity(a: list[str], b: list[str]) -> float:
    """Deterministic candidate score only; not the canonical session-boundary decision."""
    if [x.strip().lower() for x in a] == [x.strip().lower() for x in b]:
        return 1.0
    ta, tb = topic_tokens(a), topic_tokens(b)
    if not ta or not tb:
        return 0.0
    # Containment matters for topic-chain extensions such as duration -> duration gap.
    overlap = len(ta & tb)
    jaccard = overlap / len(ta | tb)
    containment = overlap / min(len(ta), len(tb))
    return max(jaccard, containment * 0.9)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass
class Resolution:
    session_id: str | None
    candidates: list[dict[str, Any]]
    ambiguous: bool = False


class SessionCapture:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("COGNITIVE_ENGINE_ROOT") or Path(__file__).resolve().parents[1])
        self.threads_path = self.root / "runtime" / "learning-threads.yaml"
        self.sessions_dir = self.root / "runtime" / "sessions"
        self.pending_dir = self.root / "cognitive-inbox" / "pending"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)

    def _load_threads(self) -> dict[str, Any]:
        if not self.threads_path.exists():
            return {"version": 2, "learning_threads": {}, "last_touched_thread": None}
        data = yaml.safe_load(self.threads_path.read_text(encoding="utf-8")) or {}
        data.setdefault("version", 2)
        data.setdefault("learning_threads", {})
        return data

    def _save_threads(self, data: dict[str, Any]) -> None:
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        header = (
            "# Ephemeral routing/runtime state for parallel learning threads and sessions.\n"
            "# Not a knowledge source, not a mastery record, and not a teaching-progress tracker.\n"
        )
        atomic_write_text(self.threads_path, header + text)

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def _meta_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "meta.yaml"

    def _events_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    def _load_meta(self, session_id: str) -> dict[str, Any]:
        path = self._meta_path(session_id)
        if not path.exists():
            raise KeyError(f"Unknown session: {session_id}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _save_meta(self, meta: dict[str, Any]) -> None:
        path = self._meta_path(meta["session_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, yaml.safe_dump(meta, allow_unicode=True, sort_keys=False))

    def _read_events(self, session_id: str) -> list[dict[str, Any]]:
        path = self._events_path(session_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def open_sessions(self, unit_id: str | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not self.sessions_dir.exists():
            return result
        for child in sorted(self.sessions_dir.iterdir()):
            if not child.is_dir() or not (child / "meta.yaml").exists():
                continue
            meta = yaml.safe_load((child / "meta.yaml").read_text(encoding="utf-8")) or {}
            if meta.get("state") not in OPEN_STATES:
                continue
            if unit_id and meta.get("learning_unit_id") != unit_id:
                continue
            result.append(meta)
        return result

    def resolve(self, unit_id: str, topic_chain: list[str]) -> Resolution:
        candidates: list[dict[str, Any]] = []
        for meta in self.open_sessions(unit_id):
            score = topic_similarity(topic_chain, meta.get("topic_chain") or [])
            candidates.append({
                "session_id": meta["session_id"],
                "topic_chain": meta.get("topic_chain") or [],
                "state": meta.get("state"),
                "score": round(score, 4),
            })
        candidates.sort(key=lambda x: x["score"], reverse=True)
        if not candidates or candidates[0]["score"] < 0.55:
            return Resolution(None, candidates, False)
        if len(candidates) > 1 and candidates[1]["score"] >= 0.55 and candidates[0]["score"] - candidates[1]["score"] < 0.15:
            return Resolution(None, candidates, True)
        return Resolution(candidates[0]["session_id"], candidates, False)

    def start(self, unit_id: str, topic_chain: list[str], platform: str | None = None) -> dict[str, Any]:
        if not topic_chain or not any(x.strip() for x in topic_chain):
            raise ValueError("A real cognitive topic is required to start a session")
        data = self._load_threads()
        threads = data["learning_threads"]
        if unit_id not in threads:
            raise KeyError(f"Learning Unit not found in runtime: {unit_id}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        session_id = f"{slugify(unit_id, 32)}--{slugify(topic_chain[-1], 32)}--{stamp}--{uuid.uuid4().hex[:6]}"
        created = now_iso()
        meta = {
            "session_id": session_id,
            "learning_unit_id": unit_id,
            "topic_chain": [x.strip() for x in topic_chain if x.strip()],
            "state": "active",
            "platforms": [platform] if platform else [],
            "created_at": created,
            "last_learning_event": None,
            "event_count": 0,
        }
        self._save_meta(meta)
        thread = threads[unit_id]
        thread.setdefault("sessions", {})
        thread["sessions"][session_id] = {
            "state": "active",
            "topic_chain": meta["topic_chain"],
            "last_learning_event": None,
        }
        thread["recent_session_id"] = session_id
        thread["thread_state"] = "active"
        data["last_touched_thread"] = unit_id
        self._save_threads(data)
        return meta

    def capture(
        self,
        unit_id: str,
        topic_chain: list[str],
        event_type: str,
        content: str,
        *,
        session_id: str | None = None,
        platform: str | None = None,
        source: str | None = None,
        citation: str | None = None,
        evidence_strength: str | None = None,
        retain: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_type = event_type.upper()
        if event_type not in EVENT_SPECS:
            raise ValueError(f"Unsupported cognitive event type: {event_type}")
        if not content.strip():
            raise ValueError("Event content cannot be empty")

        created_session = False
        resolution: Resolution | None = None
        if session_id is None:
            resolution = self.resolve(unit_id, topic_chain)
            if resolution.ambiguous:
                raise ValueError("Ambiguous session candidates; semantic layer must choose a session_id or start a new session")
            session_id = resolution.session_id
            if session_id is None:
                session_id = self.start(unit_id, topic_chain, platform)["session_id"]
                created_session = True

        meta = self._load_meta(session_id)
        if meta.get("learning_unit_id") != unit_id:
            raise ValueError("session_id belongs to a different Learning Unit")
        if meta.get("state") not in OPEN_STATES:
            raise ValueError(f"Session is not open: {session_id}")

        spec = EVENT_SPECS[event_type]
        ts = now_iso()
        event = {
            "event_id": uuid.uuid4().hex,
            "timestamp": ts,
            "event_type": event_type,
            "content": content.strip(),
            "provenance": spec["provenance"],
            "evidence_strength": evidence_strength if evidence_strength is not None else spec["strength"],
            "retain_in_trace": spec["retain"] if retain is None else bool(retain),
            "platform": platform,
            "source": source,
            "citation": citation,
            "metadata": metadata or {},
        }
        path = self._events_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # Topic-chain expansion is explicit and never time-driven.
        merged = list(meta.get("topic_chain") or [])
        for topic in topic_chain:
            topic = topic.strip()
            if topic and topic not in merged:
                merged.append(topic)
        meta["topic_chain"] = merged
        if platform and platform not in meta.setdefault("platforms", []):
            meta["platforms"].append(platform)
        meta["last_learning_event"] = ts
        meta["event_count"] = int(meta.get("event_count") or 0) + 1
        meta["state"] = "active"
        self._save_meta(meta)

        data = self._load_threads()
        thread = data["learning_threads"][unit_id]
        thread.setdefault("sessions", {})
        thread["sessions"][session_id] = {
            "state": "active",
            "topic_chain": merged,
            "last_learning_event": ts,
        }
        thread["recent_session_id"] = session_id
        thread["thread_state"] = "active"
        data["last_touched_thread"] = unit_id
        self._save_threads(data)

        return {
            "session_id": session_id,
            "event_id": event["event_id"],
            "created_session": created_session,
            "resolved_candidates": resolution.candidates if resolution else [],
        }

    def upsert_ledger_event(
        self,
        session_id: str,
        event_type: str,
        content: str,
        *,
        ledger_event_key: str,
        platform: str | None = "ChatGPT",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mirror one Capture Ledger row into a Session idempotently."""
        event_type = event_type.upper()
        if event_type not in EVENT_SPECS:
            raise ValueError(f"Unsupported cognitive event type: {event_type}")
        if not content.strip():
            raise ValueError("Event content cannot be empty")
        if not ledger_event_key.strip():
            raise ValueError("ledger_event_key is required")

        meta = self._load_meta(session_id)
        events = self._read_events(session_id)
        merged_metadata = dict(metadata or {})
        message_id = str(merged_metadata.get("message_id") or "").strip()

        existing_index = next(
            (
                i
                for i, event in enumerate(events)
                if (
                    message_id
                    and str(
                        (event.get("metadata") or {}).get("message_id") or ""
                    ).strip()
                    == message_id
                )
                or (
                    not message_id
                    and (event.get("metadata") or {}).get("ledger_event_key")
                    == ledger_event_key
                )
            ),
            None,
        )

        merged_metadata["ledger_event_key"] = ledger_event_key
        spec = EVENT_SPECS[event_type]

        if existing_index is not None:
            event = dict(events[existing_index])
            old_metadata = dict(event.get("metadata") or {})
            current_update_count = int(
                old_metadata.get("capture_update_count") or 0
            )
            if (
                event.get("content") == content.strip()
                and event.get("event_type") == event_type
                and event.get("platform") == platform
            ):
                return {
                    "session_id": session_id,
                    "event_id": event["event_id"],
                    "inserted": False,
                    "updated": False,
                    "capture_update_count": current_update_count,
                }

            update_count = current_update_count + 1
            old_metadata.update(merged_metadata)
            old_metadata["capture_update_count"] = update_count
            event.update(
                {
                    "event_type": event_type,
                    "content": content.strip(),
                    "provenance": spec["provenance"],
                    "evidence_strength": spec["strength"],
                    "retain_in_trace": spec["retain"],
                    "platform": platform,
                    "metadata": old_metadata,
                }
            )
            events[existing_index] = event
            atomic_write_text(
                self._events_path(session_id),
                "".join(
                    json.dumps(item, ensure_ascii=False) + "\n"
                    for item in events
                ),
            )
            ts = now_iso()
            meta["last_learning_event"] = ts
            self._save_meta(meta)
            self._sync_session_summary(meta)
            return {
                "session_id": session_id,
                "event_id": event["event_id"],
                "inserted": False,
                "updated": True,
                "capture_update_count": update_count,
            }

        result = self.capture(
            meta["learning_unit_id"],
            meta.get("topic_chain") or [],
            event_type,
            content,
            session_id=session_id,
            platform=platform,
            metadata=merged_metadata,
        )
        return {
            "session_id": session_id,
            "event_id": result["event_id"],
            "inserted": True,
            "updated": False,
            "capture_update_count": 0,
        }

    def pause(self, session_id: str) -> dict[str, Any]:
        meta = self._load_meta(session_id)
        if meta.get("state") == "finalized":
            raise ValueError("Cannot pause a finalized session")
        meta["state"] = "paused"
        self._save_meta(meta)
        self._sync_session_summary(meta)
        return meta

    def resume(self, session_id: str) -> dict[str, Any]:
        meta = self._load_meta(session_id)
        if meta.get("state") == "finalized":
            raise ValueError("Cannot resume a finalized session")
        meta["state"] = "active"
        self._save_meta(meta)
        self._sync_session_summary(meta)
        return meta

    def _sync_session_summary(self, meta: dict[str, Any]) -> None:
        data = self._load_threads()
        unit_id = meta["learning_unit_id"]
        thread = data["learning_threads"][unit_id]
        thread.setdefault("sessions", {})
        thread["sessions"][meta["session_id"]] = {
            "state": meta["state"],
            "topic_chain": meta.get("topic_chain") or [],
            "last_learning_event": meta.get("last_learning_event"),
        }
        thread["recent_session_id"] = meta["session_id"]
        data["last_touched_thread"] = unit_id
        self._save_threads(data)

    def finalize(self, session_id: str) -> Path:
        meta = self._load_meta(session_id)
        events = self._read_events(session_id)
        retained = [e for e in events if e.get("retain_in_trace")]

        def contents(*types: str) -> list[Any]:
            rows: list[Any] = []
            for e in retained:
                if e["event_type"] not in types:
                    continue
                base: dict[str, Any] = {"content": e["content"], "provenance": e["provenance"]}
                for key in ("evidence_strength", "platform", "source", "citation"):
                    if e.get(key) is not None:
                        base[key] = e[key]
                if e.get("metadata"):
                    base["metadata"] = e["metadata"]
                rows.append(base)
            return rows

        trace = {
            "schema_version": 1,
            "session_id": session_id,
            "learning_unit_id": meta["learning_unit_id"],
            "topic_chain": meta.get("topic_chain") or [],
            "started_at": meta.get("created_at"),
            "last_learning_event": meta.get("last_learning_event"),
            "platforms": meta.get("platforms") or [],
            "sources": sorted({e["source"] for e in retained if e.get("source")}),
            "user_questions": contents("USER_QUESTION"),
            "user_reasoning": contents("USER_REASONING"),
            "user_corrections": contents("USER_CORRECTION"),
            "user_connections": contents("USER_CONNECTION"),
            "user_confirmations": contents("USER_CONFIRMATION"),
            "user_rejections": contents("USER_REJECTION"),
            "source_claims": contents("SOURCE_CLAIM"),
            "ai_contributions": contents("AGENT_EXPLANATION", "AGENT_INFERENCE"),
            "open_questions": contents("OPEN_QUESTION"),
            "event_count": len(events),
            "retained_event_count": len(retained),
            "status": "pending",
        }
        out = self.pending_dir / f"{session_id}.yaml"
        atomic_write_text(out, yaml.safe_dump(trace, allow_unicode=True, sort_keys=False))

        meta["state"] = "finalized"
        meta["finalized_at"] = now_iso()
        meta["trace_path"] = str(out)
        self._save_meta(meta)
        self._sync_session_summary(meta)
        return out


def parse_topics(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        for part in value.split("->"):
            part = part.strip()
            if part:
                result.append(part)
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Session Capture V1")
    p.add_argument("--root", help="Cognitive Engine root (defaults to script parent)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("resolve")
    r.add_argument("--unit-id", required=True)
    r.add_argument("--topic", action="append", required=True)

    s = sub.add_parser("start")
    s.add_argument("--unit-id", required=True)
    s.add_argument("--topic", action="append", required=True)
    s.add_argument("--platform")

    c = sub.add_parser("capture")
    c.add_argument("--unit-id", required=True)
    c.add_argument("--topic", action="append", required=True)
    c.add_argument("--event-type", required=True, choices=sorted(EVENT_SPECS))
    c.add_argument("--content", required=True)
    c.add_argument("--session-id")
    c.add_argument("--platform")
    c.add_argument("--source")
    c.add_argument("--citation")
    c.add_argument("--evidence-strength")
    retention = c.add_mutually_exclusive_group()
    retention.add_argument("--retain", action="store_true")
    retention.add_argument("--drop", action="store_true")
    c.add_argument("--meta-json")

    for name in ("pause", "resume", "finalize"):
        q = sub.add_parser(name)
        q.add_argument("--session-id", required=True)

    st = sub.add_parser("status")
    st.add_argument("--unit-id")
    return p


def main() -> None:
    args = build_parser().parse_args()
    sc = SessionCapture(args.root)
    if args.command == "resolve":
        r = sc.resolve(args.unit_id, parse_topics(args.topic))
        print(json.dumps({"session_id": r.session_id, "ambiguous": r.ambiguous, "candidates": r.candidates}, ensure_ascii=False, indent=2))
    elif args.command == "start":
        print(json.dumps(sc.start(args.unit_id, parse_topics(args.topic), args.platform), ensure_ascii=False, indent=2))
    elif args.command == "capture":
        retain = True if args.retain else False if args.drop else None
        metadata = json.loads(args.meta_json) if args.meta_json else None
        result = sc.capture(
            args.unit_id, parse_topics(args.topic), args.event_type, args.content,
            session_id=args.session_id, platform=args.platform, source=args.source,
            citation=args.citation, evidence_strength=args.evidence_strength,
            retain=retain, metadata=metadata,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "pause":
        print(json.dumps(sc.pause(args.session_id), ensure_ascii=False, indent=2))
    elif args.command == "resume":
        print(json.dumps(sc.resume(args.session_id), ensure_ascii=False, indent=2))
    elif args.command == "finalize":
        print(str(sc.finalize(args.session_id)))
    elif args.command == "status":
        print(json.dumps(sc.open_sessions(args.unit_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
