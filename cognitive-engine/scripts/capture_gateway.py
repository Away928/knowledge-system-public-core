#!/usr/bin/env python3
"""Local Capture Gateway for ChatGPT learning events."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from learning_router import LearningUnitRouter
from semantic_session_router import SemanticSessionRouter


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CaptureLedger:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        runtime = self.root / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.db_path = runtime / "capture-ledger.sqlite3"
        self._init_db()

    @contextlib.contextmanager
    def connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS bindings (
                    conversation_id TEXT PRIMARY KEY,
                    learning_unit_id TEXT NOT NULL,
                    bound_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'explicit_hint'
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_key TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT,
                    observed_at TEXT NOT NULL,
                    original_timestamp TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    url TEXT,
                    capture_mode TEXT NOT NULL DEFAULT 'realtime',
                    learning_unit_id TEXT,
                    routing_status TEXT NOT NULL,
                    session_id TEXT,
                    received_at TEXT NOT NULL,
                    routing_confidence REAL,
                    routing_reason TEXT,
                    segment_id TEXT,
                    ordinal INTEGER,
                    routing_attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT,
                    content_update_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS adapter_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT,
                    observed_at TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    url TEXT,
                    page_message_count INTEGER,
                    extension_version TEXT,
                    diagnostics_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_conversation
                    ON events(conversation_id, received_at);
                CREATE INDEX IF NOT EXISTS idx_events_routing
                    ON events(routing_status, learning_unit_id);
                CREATE INDEX IF NOT EXISTS idx_adapter_observed
                    ON adapter_observations(observed_at);
                CREATE INDEX IF NOT EXISTS idx_adapter_conversation
                    ON adapter_observations(conversation_id, observed_at);
                """
            )
            # Safe migration for Ledgers created by Capture Gateway V1.
            columns = {
                row["name"]
                for row in con.execute("PRAGMA table_info(events)").fetchall()
            }
            additions = {
                "routing_confidence": "REAL",
                "routing_reason": "TEXT",
                "segment_id": "TEXT",
                "ordinal": "INTEGER",
                "routing_attempts": "INTEGER NOT NULL DEFAULT 0",
                "updated_at": "TEXT",
                "content_update_count": "INTEGER NOT NULL DEFAULT 0",
                "session_routing_confidence": "REAL",
                "session_routing_reason": "TEXT",
            }
            adapter_columns = {
                row["name"]
                for row in con.execute("PRAGMA table_info(adapter_observations)").fetchall()
            }
            if "diagnostics_json" not in adapter_columns:
                con.execute(
                    "ALTER TABLE adapter_observations ADD COLUMN diagnostics_json TEXT"
                )
            for name, sql_type in additions.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE events ADD COLUMN {name} {sql_type}")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_segment "
                "ON events(conversation_id, segment_id)"
            )

    def last_routed_user(self, conversation_id: str) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute(
                """
                SELECT learning_unit_id, segment_id, routing_status, session_id
                FROM events
                WHERE conversation_id=? AND role='user'
                  AND learning_unit_id IS NOT NULL
                  AND routing_status IN ('unit_routed','session_routed')
                ORDER BY COALESCE(ordinal, -1) DESC, received_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()

    def last_user_event(self, conversation_id: str) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute(
                """
                SELECT learning_unit_id, segment_id, routing_status, session_id
                FROM events
                WHERE conversation_id=? AND role='user'
                ORDER BY COALESCE(ordinal, -1) DESC, received_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()

    @staticmethod
    def make_event_key(payload: dict[str, Any]) -> str:
        message_id = str(payload.get("message_id") or "").strip()
        if message_id:
            basis = f"{payload.get('conversation_id','')}|{message_id}"
        else:
            basis = "|".join(
                [
                    str(payload.get("conversation_id") or ""),
                    str(payload.get("role") or ""),
                    str(payload.get("ordinal") or ""),
                    str(payload.get("content") or ""),
                ]
            )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def ingest(
        self,
        payload: dict[str, Any],
        routing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversation_id = str(payload.get("conversation_id") or "").strip()
        role = str(payload.get("role") or "").strip().lower()
        content = str(payload.get("content") or "").strip()
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError("unsupported role")
        if not content:
            raise ValueError("content is required")

        event_key = self.make_event_key(payload)
        routing = routing or {}
        unit_id = routing.get("unit_id")
        session_id = routing.get("session_id")
        routing_status = (
            "session_routed"
            if unit_id and session_id
            else "unit_routed"
            if unit_id
            else "unresolved"
        )
        confidence = float(routing.get("confidence") or 0.0)
        reason = str(routing.get("reason") or "not_routed")
        segment_id = routing.get("segment_id")
        received_at = now_iso()
        observed_at = str(payload.get("observed_at") or received_at)
        ordinal = payload.get("ordinal")
        try:
            ordinal = int(ordinal) if ordinal is not None else None
        except (TypeError, ValueError):
            ordinal = None

        inserted = False
        updated = False
        content_update_count = 0
        with self.connect() as con:
            existing = con.execute(
                """
                SELECT content, content_update_count, capture_mode
                FROM events
                WHERE event_key=?
                """,
                (event_key,),
            ).fetchone()
            if existing is not None:
                content_update_count = int(existing["content_update_count"] or 0)

            incoming_mode = str(payload.get("capture_mode") or "realtime")
            preserve_existing_backfill_target = (
                existing is not None and incoming_mode == "backfill"
            )
            preserve_realtime = (
                preserve_existing_backfill_target
                and existing["capture_mode"] == "realtime"
            )

            if existing is None:
                con.execute(
                    """
                    INSERT INTO events(
                        event_key, conversation_id, message_id, observed_at,
                        original_timestamp, role, content, url, capture_mode,
                        learning_unit_id, routing_status, session_id, received_at,
                        routing_confidence, routing_reason, segment_id, ordinal,
                        routing_attempts, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        event_key,
                        conversation_id,
                        payload.get("message_id"),
                        observed_at,
                        payload.get("original_timestamp"),
                        role,
                        content,
                        payload.get("url"),
                        str(payload.get("capture_mode") or "realtime"),
                        unit_id,
                        routing_status,
                        session_id,
                        received_at,
                        confidence,
                        reason,
                        segment_id,
                        ordinal,
                        received_at,
                    ),
                )
                inserted = True
            elif (
                not preserve_existing_backfill_target
                and existing["content"] != content
            ):
                con.execute(
                    """
                    UPDATE events
                    SET content=?, observed_at=?, url=?, ordinal=?,
                        learning_unit_id=?, routing_status=?,
                        session_id=?, routing_confidence=?, routing_reason=?,
                        segment_id=?, updated_at=?,
                        content_update_count=content_update_count+1
                    WHERE event_key=?
                    """,
                    (
                        content,
                        observed_at,
                        payload.get("url"),
                        ordinal,
                        unit_id,
                        routing_status,
                        session_id,
                        confidence,
                        reason,
                        segment_id,
                        received_at,
                        event_key,
                    ),
                )
                updated = True
                content_update_count += 1
        return {
            "event_key": event_key,
            "inserted": inserted,
            "updated": updated,
            "learning_unit_id": unit_id,
            "routing_status": routing_status,
            "routing_confidence": confidence,
            "routing_reason": reason,
            "segment_id": segment_id,
            "session_id": session_id,
            "content_update_count": content_update_count,
            "preserved_realtime": preserve_realtime,
        }

    def assign_session(
        self,
        event_key: str,
        *,
        session_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """
                UPDATE events
                SET session_id=?, routing_status='session_routed',
                    session_routing_confidence=?, session_routing_reason=?,
                    updated_at=?
                WHERE event_key=? AND learning_unit_id IS NOT NULL
                """,
                (session_id, confidence, reason, now_iso(), event_key),
            )

    def pending_session_users(
        self,
        conversation_id: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT *
            FROM events
            WHERE role='user'
              AND learning_unit_id IS NOT NULL
              AND routing_status='unit_routed'
              AND session_id IS NULL
        """
        params: tuple[Any, ...] = ()
        if conversation_id:
            sql += " AND conversation_id=?"
            params = (conversation_id,)
        sql += " ORDER BY conversation_id, COALESCE(ordinal, 2147483647), received_at"
        with self.connect() as con:
            return con.execute(sql, params).fetchall()

    def event_by_key(self, event_key: str) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM events WHERE event_key=?",
                (event_key,),
            ).fetchone()

    def unresolved_count(self) -> int:
        with self.connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM events WHERE routing_status='unresolved'"
            ).fetchone()
        return int(row["n"])

    def conversation_status(self, conversation_id: str) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute(
                """
                SELECT COUNT(*) AS n, MAX(received_at) AS last_received,
                       COUNT(DISTINCT CASE WHEN segment_id IS NOT NULL THEN segment_id END) AS segments,
                       SUM(CASE WHEN content_update_count > 0 THEN 1 ELSE 0 END) AS streaming_updated_events,
                       MAX(content_update_count) AS max_content_update_count
                FROM events WHERE conversation_id=?
                """,
                (conversation_id,),
            ).fetchone()
            units = [
                x["learning_unit_id"]
                for x in con.execute(
                    """
                    SELECT DISTINCT learning_unit_id
                    FROM events
                    WHERE conversation_id=? AND learning_unit_id IS NOT NULL
                    ORDER BY learning_unit_id
                    """,
                    (conversation_id,),
                ).fetchall()
            ]
        return {
            "conversation_id": conversation_id,
            "event_count": int(row["n"] or 0),
            "last_received": row["last_received"],
            "segment_count": int(row["segments"] or 0),
            "streaming_updated_events": int(row["streaming_updated_events"] or 0),
            "max_content_update_count": int(row["max_content_update_count"] or 0),
            "learning_unit_ids": units,
        }

    def events_for_conversation(self, conversation_id: str) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """
                SELECT *
                FROM events
                WHERE conversation_id=?
                ORDER BY COALESCE(ordinal, 2147483647), received_at, event_key
                """,
                (conversation_id,),
            ).fetchall()

    def unresolved_users(
        self,
        conversation_id: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT *
            FROM events
            WHERE role='user' AND routing_status='unresolved'
        """
        params: tuple[Any, ...] = ()
        if conversation_id:
            sql += " AND conversation_id=?"
            params = (conversation_id,)
        sql += " ORDER BY conversation_id, COALESCE(ordinal, 2147483647), received_at"
        with self.connect() as con:
            return con.execute(sql, params).fetchall()

    def update_routing(
        self,
        event_key: str,
        *,
        learning_unit_id: str | None,
        routing_status: str,
        confidence: float,
        reason: str,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """
                UPDATE events
                SET learning_unit_id=?, routing_status=?,
                    routing_confidence=?, routing_reason=?,
                    routing_attempts=COALESCE(routing_attempts,0)+1,
                    updated_at=?
                WHERE event_key=?
                """,
                (
                    learning_unit_id,
                    routing_status,
                    confidence,
                    reason,
                    now_iso(),
                    event_key,
                ),
            )

    def set_segment(self, event_key: str, segment_id: str | None) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE events SET segment_id=?, updated_at=? WHERE event_key=?",
                (segment_id, now_iso(), event_key),
            )

    def record_adapter_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        surface = str(payload.get("surface") or "unknown").strip().lower()
        if surface not in {"browser", "pwa", "unknown"}:
            surface = "unknown"
        conversation_id = str(payload.get("conversation_id") or "").strip() or None
        observed_at = str(payload.get("observed_at") or now_iso())
        page_message_count = payload.get("page_message_count")
        try:
            page_message_count = int(page_message_count)
        except (TypeError, ValueError):
            page_message_count = None
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO adapter_observations(
                    conversation_id, observed_at, surface, url,
                    page_message_count, extension_version, diagnostics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    observed_at,
                    surface,
                    payload.get("url"),
                    page_message_count,
                    str(payload.get("extension_version") or ""),
                    json.dumps(payload.get("diagnostics") or {}, ensure_ascii=False),
                ),
            )
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "surface": surface,
            "observed_at": observed_at,
        }

    def adapter_status(
        self,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        sql = """
            SELECT conversation_id, observed_at, surface, url,
                   page_message_count, extension_version, diagnostics_json
            FROM adapter_observations
        """
        params: tuple[Any, ...] = ()
        if conversation_id:
            sql += " WHERE conversation_id=?"
            params = (conversation_id,)
        sql += " ORDER BY id DESC LIMIT 1"
        with self.connect() as con:
            row = con.execute(sql, params).fetchone()
        return dict(row) if row else {}

    def review_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT event_key, conversation_id, ordinal, content,
                       routing_attempts, routing_reason, received_at
                FROM events
                WHERE role='user' AND routing_status='needs_review'
                ORDER BY received_at
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


class Gateway:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.ledger = CaptureLedger(self.root)
        self.router = LearningUnitRouter(self.root)
        self.session_router = SemanticSessionRouter(self.root)
        self.threads_path = self.root / "runtime" / "learning-threads.yaml"

    def learning_units(self) -> list[dict[str, str]]:
        result = []
        for item in self.router.candidate_units():
            result.append(
                {
                    "unit_id": item["unit_id"],
                    "title": item.get("title") or item["unit_id"],
                    "thread_state": item.get("thread_state") or "unknown",
                }
            )
        return result

    @staticmethod
    def _event_type_for_capture(role: str, content: str) -> str | None:
        role = role.lower().strip()
        text = " ".join(content.strip().split())
        if role == "assistant":
            return "AGENT_EXPLANATION"
        if role != "user" or not text:
            return None

        lower = text.lower()
        if lower in {"对", "对的", "是的", "没错", "明白了", "懂了", "理解了"}:
            return "USER_CONFIRMATION"
        if re.search(r"(不需要|不要这样|不是这个|我不同意|拒绝)", text):
            return "USER_REJECTION"
        if re.search(r"(不对|更正|纠正|我刚才说错|应该改成|不是.+而是)", text):
            return "USER_CORRECTION"
        if re.search(r"(也就是说|换句话说|相当于|联系起来|所以.+可以理解为)", text):
            return "USER_CONNECTION"
        if (
            "?" in text
            or "？" in text
            or re.search(
                r"(为什么|怎么|如何|什么|是否|是不是|哪里|哪个|哪一个|多少|区别|是什么意思|吗$|呢$)",
                text,
            )
        ):
            return "USER_QUESTION"
        return "USER_REASONING"

    def sync_session_events(
        self,
        conversation_id: str,
    ) -> dict[str, int]:
        inserted = 0
        updated = 0
        unchanged = 0
        skipped = 0

        for row in self.ledger.events_for_conversation(conversation_id):
            if (
                row["routing_status"] != "session_routed"
                or not row["session_id"]
            ):
                continue
            event_type = self._event_type_for_capture(
                str(row["role"]),
                str(row["content"]),
            )
            if event_type is None:
                skipped += 1
                continue

            result = self.session_router.capture.upsert_ledger_event(
                str(row["session_id"]),
                event_type,
                str(row["content"]),
                ledger_event_key=str(row["event_key"]),
                platform="ChatGPT",
                metadata={
                    "conversation_id": row["conversation_id"],
                    "message_id": row["message_id"],
                    "ordinal": row["ordinal"],
                    "segment_id": row["segment_id"],
                    "capture_mode": row["capture_mode"],
                    "role": row["role"],
                },
            )
            if result["inserted"]:
                inserted += 1
            elif result["updated"]:
                updated += 1
            else:
                unchanged += 1

        return {
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "skipped": skipped,
        }

    @staticmethod
    def _new_segment_id(
        conversation_id: str,
        unit_id: str,
        event_key: str,
    ) -> str:
        raw = f"{conversation_id}|{unit_id}|{event_key}".encode("utf-8")
        return "seg-" + hashlib.sha256(raw).hexdigest()[:16]

    def context_window(
        self,
        conversation_id: str,
        event_key: str,
        radius: int = 2,
    ) -> list[dict[str, Any]]:
        users = [
            row for row in self.ledger.events_for_conversation(conversation_id)
            if row["role"] == "user"
        ]
        index = next(
            (i for i, row in enumerate(users) if row["event_key"] == event_key),
            None,
        )
        if index is None:
            return []
        lo = max(0, index - radius)
        hi = min(len(users), index + radius + 1)
        return [dict(row) for row in users[lo:hi]]

    def rebuild_segments(self, conversation_id: str) -> dict[str, int]:
        rows = self.ledger.events_for_conversation(conversation_id)
        current_unit: str | None = None
        current_segment: str | None = None
        last_user_unit: str | None = None
        last_user_segment: str | None = None
        last_user_session: str | None = None
        segment_count = 0
        repaired_assistants = 0

        with self.ledger.connect() as con:
            for row in rows:
                role = row["role"]
                if role == "user":
                    unit_id = row["learning_unit_id"]
                    routed = row["routing_status"] in {"unit_routed", "session_routed"}
                    if routed and unit_id:
                        if current_unit != unit_id or current_segment is None:
                            current_segment = self._new_segment_id(
                                conversation_id, unit_id, row["event_key"]
                            )
                            current_unit = unit_id
                            segment_count += 1
                        con.execute(
                            "UPDATE events SET segment_id=?, updated_at=? WHERE event_key=?",
                            (current_segment, now_iso(), row["event_key"]),
                        )
                        last_user_unit = unit_id
                        last_user_segment = current_segment
                        last_user_session = row["session_id"]
                    else:
                        con.execute(
                            "UPDATE events SET segment_id=NULL, updated_at=? WHERE event_key=?",
                            (now_iso(), row["event_key"]),
                        )
                        current_unit = None
                        current_segment = None
                        last_user_unit = None
                        last_user_segment = None
                        last_user_session = None
                    continue

                if role == "assistant":
                    if last_user_unit and last_user_segment:
                        desired_status = (
                            "session_routed" if last_user_session else "unit_routed"
                        )
                        if (
                            row["learning_unit_id"] != last_user_unit
                            or row["segment_id"] != last_user_segment
                            or row["session_id"] != last_user_session
                            or row["routing_status"] != desired_status
                        ):
                            con.execute(
                                """
                                UPDATE events
                                SET learning_unit_id=?, routing_status=?,
                                    routing_confidence=0.99,
                                    routing_reason='assistant_inherits_preceding_user',
                                    segment_id=?, session_id=?,
                                    session_routing_confidence=?,
                                    session_routing_reason=?, updated_at=?
                                WHERE event_key=?
                                """,
                                (
                                    last_user_unit,
                                    desired_status,
                                    last_user_segment,
                                    last_user_session,
                                    0.99 if last_user_session else None,
                                    (
                                        "assistant_inherits_preceding_user"
                                        if last_user_session
                                        else None
                                    ),
                                    now_iso(),
                                    row["event_key"],
                                ),
                            )
                            repaired_assistants += 1
                    else:
                        con.execute(
                            """
                            UPDATE events
                            SET learning_unit_id=NULL, routing_status='unresolved',
                                routing_confidence=0.0,
                                routing_reason='preceding_user_unresolved',
                                segment_id=NULL, session_id=NULL,
                                session_routing_confidence=NULL,
                                session_routing_reason=NULL, updated_at=?
                            WHERE event_key=?
                            """,
                            (now_iso(), row["event_key"]),
                        )
                    continue

                con.execute(
                    "UPDATE events SET segment_id=NULL, updated_at=? WHERE event_key=?",
                    (now_iso(), row["event_key"]),
                )

        return {
            "segment_count": segment_count,
            "repaired_assistants": repaired_assistants,
        }

    def reroute_unresolved(
        self,
        conversation_id: str | None = None,
        *,
        radius: int = 2,
        max_attempts: int = 3,
    ) -> dict[str, int]:
        targets = self.ledger.unresolved_users(conversation_id)
        resolved = 0
        review = 0
        still_unresolved = 0
        touched_conversations: set[str] = set()

        for row in targets:
            cid = row["conversation_id"]
            window = self.context_window(cid, row["event_key"], radius=radius)
            if not window:
                continue
            touched_conversations.add(cid)
            index = next(
                i for i, item in enumerate(window)
                if item["event_key"] == row["event_key"]
            )
            before = list(reversed(window[:index]))
            after = window[index + 1:]
            prev = next(
                (
                    item for item in before
                    if item["learning_unit_id"]
                    and item["routing_status"] in {"unit_routed", "session_routed"}
                ),
                None,
            )
            nxt = next(
                (
                    item for item in after
                    if item["learning_unit_id"]
                    and item["routing_status"] in {"unit_routed", "session_routed"}
                ),
                None,
            )
            previous_unit = prev["learning_unit_id"] if prev else None

            chosen_unit: str | None = None
            confidence = 0.0
            reason = "contextual_retry_pending"

            if (
                prev
                and nxt
                and prev["learning_unit_id"] == nxt["learning_unit_id"]
            ):
                chosen_unit = prev["learning_unit_id"]
                confidence = 0.94
                reason = "context_sandwich_same_unit"
            else:
                context_text = "\n".join(
                    re.sub(
                        r"fin\s*\d{4}[a-z]?",
                        "",
                        str(item["content"]),
                        flags=re.I,
                    )
                    for item in window
                )
                routed = self.router.route(
                    context_text,
                    role="user",
                    previous_unit_id=previous_unit,
                )
                candidate = routed.get("unit_id")
                candidate_conf = float(routed.get("confidence") or 0.0)
                neighbor_units = {
                    item["learning_unit_id"]
                    for item in (prev, nxt)
                    if item and item["learning_unit_id"]
                }
                if after and candidate and candidate_conf >= 0.90 and (
                    candidate in neighbor_units or candidate_conf >= 0.96
                ):
                    chosen_unit = str(candidate)
                    confidence = candidate_conf
                    reason = "contextual_profile_match"

            if chosen_unit:
                self.ledger.update_routing(
                    row["event_key"],
                    learning_unit_id=chosen_unit,
                    routing_status="unit_routed",
                    confidence=confidence,
                    reason=reason,
                )
                resolved += 1
                continue

            attempts = int(row["routing_attempts"] or 0) + 1
            later_user_count = sum(
                1
                for item in after
                if item["role"] == "user"
            )
            status = (
                "needs_review"
                if attempts >= max_attempts and later_user_count >= 2
                else "unresolved"
            )
            self.ledger.update_routing(
                row["event_key"],
                learning_unit_id=None,
                routing_status=status,
                confidence=0.0,
                reason=(
                    "contextual_retry_exhausted"
                    if status == "needs_review"
                    else reason
                ),
            )
            if status == "needs_review":
                review += 1
            else:
                still_unresolved += 1

        for cid in touched_conversations:
            self.rebuild_segments(cid)

        return {
            "resolved": resolved,
            "needs_review": review,
            "still_unresolved": still_unresolved,
        }

    def route_pending_sessions(
        self,
        conversation_id: str | None = None,
    ) -> dict[str, int]:
        reused = 0
        created = 0
        unresolved = 0

        for row in self.ledger.pending_session_users(conversation_id):
            unit_id = str(row["learning_unit_id"])
            decision = self.session_router.route(unit_id, str(row["content"]))

            session_id: str | None = None
            confidence = decision.confidence
            reason = decision.reason

            if decision.action == "reuse_existing_session":
                session_id = decision.session_id
                reused += 1
            elif (
                decision.action == "start_new_session"
                and row["routing_reason"] == "context_sandwich_same_unit"
                and row["segment_id"]
            ):
                users = [
                    item
                    for item in self.ledger.events_for_conversation(
                        str(row["conversation_id"])
                    )
                    if item["role"] == "user"
                ]
                index = next(
                    (
                        i
                        for i, item in enumerate(users)
                        if item["event_key"] == row["event_key"]
                    ),
                    None,
                )
                previous_session = None
                if index is not None:
                    for item in reversed(users[:index]):
                        if item["segment_id"] != row["segment_id"]:
                            break
                        if item["session_id"]:
                            previous_session = str(item["session_id"])
                            break
                if previous_session:
                    session_id = previous_session
                    confidence = 0.90
                    reason = "context_sandwich_session_continuity"
                    reused += 1
                else:
                    started = self.session_router.capture.start(
                        unit_id,
                        decision.suggested_topic_chain,
                        platform="ChatGPT",
                    )
                    session_id = str(started["session_id"])
                    created += 1
            elif decision.action == "start_new_session":
                started = self.session_router.capture.start(
                    unit_id,
                    decision.suggested_topic_chain,
                    platform="ChatGPT",
                )
                session_id = str(started["session_id"])
                created += 1
            else:
                unresolved += 1

            if session_id:
                self.ledger.assign_session(
                    row["event_key"],
                    session_id=session_id,
                    confidence=confidence,
                    reason=reason,
                )

        return {
            "reused": reused,
            "created": created,
            "unresolved": unresolved,
        }

    def capture_status(
        self,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "adapter": self.ledger.adapter_status(conversation_id),
            "unresolved_count": self.ledger.unresolved_count(),
            "needs_review": self.ledger.review_queue(),
        }
        if conversation_id:
            result["conversation"] = self.ledger.conversation_status(conversation_id)
        return result

    def ingest_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(payload.get("conversation_id") or "").strip()
        role = str(payload.get("role") or "").strip().lower()
        event_key = self.ledger.make_event_key(payload)

        if role == "assistant":
            last_user = self.ledger.last_user_event(conversation_id)
            if last_user and last_user["learning_unit_id"]:
                routing = {
                    "unit_id": last_user["learning_unit_id"],
                    "confidence": 0.99,
                    "reason": "assistant_inherits_preceding_user",
                    "segment_id": last_user["segment_id"],
                    "session_id": last_user["session_id"],
                }
            else:
                routing = {
                    "unit_id": None,
                    "confidence": 0.0,
                    "reason": "preceding_user_unresolved",
                    "segment_id": None,
                }
            result = self.ledger.ingest(payload, routing)
            if result["inserted"] or result["updated"]:
                self.rebuild_segments(conversation_id)
                result["session_sync"] = self.sync_session_events(
                    conversation_id
                )
            return result

        previous = self.ledger.last_routed_user(conversation_id)
        previous_unit = previous["learning_unit_id"] if previous else None
        previous_segment = previous["segment_id"] if previous else None

        routing = self.router.route(
            str(payload.get("content") or ""),
            role=role,
            previous_unit_id=previous_unit,
        )
        unit_id = routing.get("unit_id")
        if unit_id:
            if previous and previous["learning_unit_id"] == unit_id and previous_segment:
                routing["segment_id"] = previous_segment
            else:
                routing["segment_id"] = self._new_segment_id(
                    conversation_id, unit_id, event_key
                )
        else:
            routing["segment_id"] = None

        result = self.ledger.ingest(payload, routing)
        if result["inserted"] or result["updated"]:
            reconciliation = self.reroute_unresolved(conversation_id)
            self.rebuild_segments(conversation_id)
            session_reconciliation = self.route_pending_sessions(conversation_id)
            self.rebuild_segments(conversation_id)
            result["reconciliation"] = reconciliation
            result["session_reconciliation"] = session_reconciliation
            result["session_sync"] = self.sync_session_events(
                conversation_id
            )

            final_row = self.ledger.event_by_key(event_key)
            if final_row is not None:
                result["learning_unit_id"] = final_row["learning_unit_id"]
                result["routing_status"] = final_row["routing_status"]
                result["segment_id"] = final_row["segment_id"]
                result["session_id"] = final_row["session_id"]
        return result


def make_handler(gateway: Gateway):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KnowledgeCaptureGateway/1.0"

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "https://chatgpt.com")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, status: int, obj: Any) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._cors()
            self.end_headers()

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2_000_000:
                raise ValueError("invalid body length")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json(
                    200,
                    {
                        "ok": True,
                        "unresolved_count": gateway.ledger.unresolved_count(),
                        "time": now_iso(),
                    },
                )
                return
            if parsed.path == "/units":
                self._json(200, {"learning_units": gateway.learning_units()})
                return
            if parsed.path == "/capture-status":
                params = parse_qs(parsed.query)
                conversation_id = (params.get("conversation_id") or [None])[0]
                self._json(200, gateway.capture_status(conversation_id))
                return
            if parsed.path == "/review-queue":
                self._json(200, {"events": gateway.ledger.review_queue()})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                payload = self._read_json()
                if self.path == "/event":
                    self._json(200, gateway.ingest_event(payload))
                    return
                if self.path == "/adapter-observation":
                    self._json(200, gateway.ledger.record_adapter_observation(payload))
                    return
                if self.path == "/reroute":
                    conversation_id = str(
                        payload.get("conversation_id") or ""
                    ).strip() or None
                    self._json(
                        200,
                        gateway.reroute_unresolved(conversation_id),
                    )
                    return
                self._json(404, {"error": "not found"})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local Knowledge Capture Gateway")
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    return p


def main() -> None:
    args = build_parser().parse_args()
    gateway = Gateway(args.root)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(gateway))
    print(f"Capture Gateway listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
