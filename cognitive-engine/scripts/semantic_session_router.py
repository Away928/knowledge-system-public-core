#!/usr/bin/env python3
"""Conservative Semantic Session Router.

Decides whether a unit-routed learning message should reuse an existing Session,
start a new Session, or remain session-unresolved. Conversation and segment IDs
are deliberately not used as Session identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from learning_router import tokens
from session_capture import SessionCapture


@dataclass
class SessionDecision:
    action: str
    session_id: str | None
    confidence: float
    reason: str
    candidates: list[dict[str, Any]]
    suggested_topic_chain: list[str]


class SemanticSessionRouter:
    REUSE_THRESHOLD = 0.55
    REUSE_MARGIN = 0.20
    NEW_THRESHOLD = 0.15

    @staticmethod
    def _session_tokens(text: str) -> list[str]:
        return [
            token
            for token in tokens(text)
            if not re.fullmatch(r"fin\d{4}[a-z]?", token, flags=re.I)
        ]

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.capture = SessionCapture(self.root)

    @staticmethod
    def _topic_seed(content: str) -> list[str]:
        cleaned = re.sub(r"fin\s*\d{4}[a-z]?", "", content, flags=re.I)
        cleaned = " ".join(cleaned.strip().split())
        if not cleaned:
            return []
        return [cleaned[:120]]

    @staticmethod
    def _score(content: str, topic_chain: list[str]) -> dict[str, Any]:
        query_tokens = set(SemanticSessionRouter._session_tokens(content))
        session_tokens = set(
            SemanticSessionRouter._session_tokens(" ".join(topic_chain))
        )
        if not query_tokens or not session_tokens:
            return {
                "score": 0.0,
                "overlap": [],
                "best_topic": None,
                "best_topic_score": 0.0,
            }

        overlap_tokens = sorted(query_tokens & session_tokens)
        query_coverage = len(overlap_tokens) / len(query_tokens)

        best_topic_score = 0.0
        best_topic: str | None = None
        for topic in topic_chain:
            topic_set = set(SemanticSessionRouter._session_tokens(topic))
            if not topic_set:
                continue
            overlap = len(query_tokens & topic_set)
            if overlap == 0:
                continue
            # A short canonical topic fully contained in the query is strong
            # evidence even if the user's question contains extra words.
            topic_match = max(
                overlap / len(query_tokens),
                overlap / len(topic_set),
            )
            if topic_match > best_topic_score:
                best_topic_score = topic_match
                best_topic = topic

        score = 0.65 * query_coverage + 0.35 * best_topic_score
        return {
            "score": round(score, 4),
            "overlap": overlap_tokens,
            "best_topic": best_topic,
            "best_topic_score": round(best_topic_score, 4),
        }

    def candidates(self, unit_id: str, content: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for meta in self.capture.open_sessions(unit_id):
            detail = self._score(content, meta.get("topic_chain") or [])
            rows.append({
                "session_id": meta["session_id"],
                "topic_chain": meta.get("topic_chain") or [],
                "state": meta.get("state"),
                **detail,
            })
        rows.sort(key=lambda row: row["score"], reverse=True)
        return rows

    def route(self, unit_id: str, content: str) -> SessionDecision:
        topic_seed = self._topic_seed(content)
        if not topic_seed:
            return SessionDecision(
                action="unresolved_session",
                session_id=None,
                confidence=0.0,
                reason="no_cognitive_content",
                candidates=[],
                suggested_topic_chain=[],
            )

        candidates = self.candidates(unit_id, content)
        if not candidates:
            return SessionDecision(
                action="start_new_session",
                session_id=None,
                confidence=0.92,
                reason="no_open_session_for_unit",
                candidates=[],
                suggested_topic_chain=topic_seed,
            )

        best = candidates[0]
        second_score = candidates[1]["score"] if len(candidates) > 1 else 0.0
        margin = best["score"] - second_score

        if best["score"] >= self.REUSE_THRESHOLD and margin >= self.REUSE_MARGIN:
            confidence = min(
                0.97,
                0.72 + 0.25 * best["score"] + 0.10 * margin,
            )
            return SessionDecision(
                action="reuse_existing_session",
                session_id=best["session_id"],
                confidence=round(confidence, 3),
                reason="semantic_topic_match",
                candidates=candidates,
                suggested_topic_chain=topic_seed,
            )

        if best["score"] <= self.NEW_THRESHOLD:
            return SessionDecision(
                action="start_new_session",
                session_id=None,
                confidence=0.88,
                reason="no_semantic_session_match",
                candidates=candidates,
                suggested_topic_chain=topic_seed,
            )

        return SessionDecision(
            action="unresolved_session",
            session_id=None,
            confidence=0.0,
            reason="ambiguous_session_candidates",
            candidates=candidates,
            suggested_topic_chain=topic_seed,
        )
