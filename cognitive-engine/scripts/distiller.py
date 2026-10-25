#!/usr/bin/env python3
"""Distiller V1: prepare semantic comparison packets and persist validated Knowledge Deltas.

Design: deterministic code retrieves relevant current knowledge and enforces schemas.
A semantic agent performs the actual NEW/REFINE/CORRECT/CONNECT/... judgment.
The distiller never writes to the Obsidian Vault.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

DELTA_TYPES = {"NEW", "REFINE", "CORRECT", "CONNECT", "INSIGHT_CANDIDATE", "QUESTION", "CONFLICT"}
REVIEW_STATES = {"pending", "approved", "rejected"}
PROVENANCE = {"USER", "SOURCE", "AGENT", "MIXED"}
SEARCH_DIRS = (
    "wiki/concepts",
    "wiki/entities",
    "wiki/analyses",
    "wiki/comparisons",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def normalize_tokens(text: str) -> set[str]:
    # English words and individual CJK sequences are good enough for candidate retrieval.
    text = text.lower()
    en = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", text)
    zh = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "into", "what", "why", "how",
        "user", "source", "agent", "content", "provenance", "question", "reasoning",
    }
    return {x for x in en + zh if x not in stop}


def collect_trace_text(trace: dict[str, Any]) -> str:
    chunks: list[str] = []
    chunks.extend(trace.get("topic_chain") or [])
    for key in (
        "user_questions", "user_reasoning", "user_corrections", "user_connections",
        "user_confirmations", "user_rejections", "source_claims", "open_questions",
    ):
        for item in trace.get(key) or []:
            if isinstance(item, dict):
                chunks.append(str(item.get("content") or ""))
            else:
                chunks.append(str(item))
    return "\n".join(chunks)


def excerpt(text: str, query_tokens: set[str], max_chars: int = 3200) -> str:
    if len(text) <= max_chars:
        return text
    lower = text.lower()
    positions = [lower.find(t.lower()) for t in query_tokens if lower.find(t.lower()) >= 0]
    start = max(0, (min(positions) if positions else 0) - 500)
    return text[start:start + max_chars]


class Distiller:
    def __init__(self, engine_root: str | Path | None = None, vault_root: str | Path | None = None):
        self.engine = Path(engine_root or os.environ.get("COGNITIVE_ENGINE_ROOT") or Path(__file__).resolve().parents[1])
        self.vault = Path(vault_root or os.environ.get("KNOWLEDGE_VAULT_ROOT") or self.engine.parent / "obsidian-vault")
        self.pending_traces = self.engine / "cognitive-inbox" / "pending"
        self.processed_traces = self.engine / "cognitive-inbox" / "processed"
        self.packet_dir = self.engine / "runtime" / "distillation"
        self.delta_root = self.engine / "knowledge-deltas"
        for p in [self.pending_traces, self.processed_traces, self.packet_dir, *(self.delta_root / s for s in REVIEW_STATES)]:
            p.mkdir(parents=True, exist_ok=True)

    def resolve_trace(self, trace: str | Path) -> Path:
        p = Path(trace)
        if p.exists():
            return p
        candidate = self.pending_traces / f"{trace}.yaml"
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"Trace not found: {trace}")

    def load_trace(self, trace: str | Path) -> tuple[Path, dict[str, Any]]:
        path = self.resolve_trace(trace)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("status") != "pending":
            raise ValueError("Distiller only prepares pending Cognitive Traces")
        if not data.get("session_id") or not data.get("learning_unit_id"):
            raise ValueError("Trace missing session_id or learning_unit_id")
        return path, data

    def retrieve_current_knowledge(self, trace: dict[str, Any], top_k: int = 8) -> list[dict[str, Any]]:
        query_text = collect_trace_text(trace)
        q = normalize_tokens(query_text)
        rows: list[dict[str, Any]] = []
        for rel_root in SEARCH_DIRS:
            root = self.vault / rel_root
            if not root.exists():
                continue
            for path in root.rglob("*.md"):
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                toks = normalize_tokens(path.stem.replace("-", " ") + "\n" + text)
                overlap = q & toks
                if not overlap:
                    continue
                # Simple transparent retrieval score; semantic comparison happens later.
                score = len(overlap) / max(1, len(q))
                rows.append({
                    "path": str(path.relative_to(self.vault)),
                    "score": round(score, 4),
                    "matched_tokens": sorted(overlap)[:20],
                    "excerpt": excerpt(text, overlap),
                })
        rows.sort(key=lambda x: (x["score"], len(x["matched_tokens"])), reverse=True)
        return rows[:top_k]

    def prepare(self, trace: str | Path, top_k: int = 8) -> Path:
        trace_path, data = self.load_trace(trace)
        current = self.retrieve_current_knowledge(data, top_k=top_k)
        packet = {
            "schema_version": 1,
            "created_at": now_iso(),
            "session_id": data["session_id"],
            "learning_unit_id": data["learning_unit_id"],
            "trace_path": str(trace_path),
            "trace": data,
            "current_knowledge": current,
            "instructions": {
                "goal": "Compare the Cognitive Trace against current durable knowledge and emit only genuine changes.",
                "allowed_delta_types": sorted(DELTA_TYPES),
                "rules": [
                    "Do not summarize the session; emit only knowledge-state changes.",
                    "Do not treat AGENT content as USER belief.",
                    "INSIGHT_CANDIDATE is never auto-approved.",
                    "If the trace adds no durable change, emit an empty delta list.",
                    "Every delta must cite trace evidence and, when relevant, current knowledge refs.",
                ],
            },
        }
        out = self.packet_dir / f"{data['session_id']}.yaml"
        atomic_write(out, yaml.safe_dump(packet, allow_unicode=True, sort_keys=False))
        return out

    def validate_delta(self, delta: dict[str, Any], session_id: str) -> dict[str, Any]:
        required = ["type", "target", "after", "reason", "provenance", "confidence"]
        missing = [k for k in required if k not in delta]
        if missing:
            raise ValueError(f"Delta missing required fields: {', '.join(missing)}")
        dtype = str(delta["type"]).upper()
        if dtype not in DELTA_TYPES:
            raise ValueError(f"Unsupported delta type: {dtype}")
        prov = str(delta["provenance"]).upper()
        if prov not in PROVENANCE:
            raise ValueError(f"Unsupported provenance: {prov}")
        try:
            confidence = float(delta["confidence"])
        except Exception as e:
            raise ValueError("confidence must be numeric") from e
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0,1]")
        if dtype == "INSIGHT_CANDIDATE" and prov not in {"USER", "MIXED"}:
            raise ValueError("INSIGHT_CANDIDATE must be grounded in USER evidence")
        review_status = str(delta.get("review_status") or "pending").lower()
        if review_status != "pending":
            raise ValueError("Distiller may only emit pending deltas; approval requires Human Review")
        trace_evidence = delta.get("trace_evidence") or []
        if not trace_evidence:
            raise ValueError("Every delta requires trace_evidence")
        result = {
            "delta_id": delta.get("delta_id") or uuid.uuid4().hex,
            "type": dtype,
            "target": str(delta["target"]).strip(),
            "before": delta.get("before"),
            "after": delta.get("after"),
            "reason": str(delta["reason"]).strip(),
            "provenance": prov,
            "confidence": confidence,
            "trace_evidence": trace_evidence,
            "current_knowledge_refs": delta.get("current_knowledge_refs") or [],
            "source_session_ids": sorted(set((delta.get("source_session_ids") or []) + [session_id])),
            "review_status": "pending",
            "review_note": None,
            "proposed_by": "AGENT",
        }
        if not result["target"] or not str(result["after"] or "").strip():
            raise ValueError("target and after must be non-empty")
        return result

    def emit(self, packet: str | Path, proposals: list[dict[str, Any]], summary: str | None = None) -> Path:
        packet_path = Path(packet)
        pdata = yaml.safe_load(packet_path.read_text(encoding="utf-8")) or {}
        session_id = pdata.get("session_id")
        if not session_id:
            raise ValueError("Distillation packet missing session_id")
        trace_path = Path(pdata["trace_path"])
        trace = yaml.safe_load(trace_path.read_text(encoding="utf-8")) or {}
        if trace.get("status") != "pending":
            raise ValueError("Trace is no longer pending")
        deltas = [self.validate_delta(d, session_id) for d in proposals]
        batch = {
            "schema_version": 1,
            "batch_id": uuid.uuid4().hex,
            "created_at": now_iso(),
            "learning_unit_id": pdata.get("learning_unit_id"),
            "source_session_ids": [session_id],
            "distillation_packet": str(packet_path),
            "summary": summary or ("No durable knowledge change detected." if not deltas else None),
            "review_status": "pending" if deltas else "no_change",
            "deltas": deltas,
        }
        out = self.delta_root / "pending" / f"{session_id}.yaml"
        atomic_write(out, yaml.safe_dump(batch, allow_unicode=True, sort_keys=False))

        # Distillation is complete once a semantic agent has emitted a batch, even if empty.
        trace["status"] = "processed"
        trace["processed_at"] = now_iso()
        trace["knowledge_delta_batch"] = str(out)
        dest = self.processed_traces / trace_path.name
        atomic_write(dest, yaml.safe_dump(trace, allow_unicode=True, sort_keys=False))
        if trace_path.resolve() != dest.resolve():
            trace_path.unlink()
        return out

    def review(self, delta_file: str | Path, delta_id: str, decision: str, note: str | None = None) -> Path:
        decision = decision.lower()
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        path = Path(delta_file)
        batch = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        found = False
        for d in batch.get("deltas") or []:
            if d.get("delta_id") == delta_id:
                d["review_status"] = "approved" if decision == "approve" else "rejected"
                d["review_note"] = note
                d["reviewed_at"] = now_iso()
                found = True
                break
        if not found:
            raise KeyError(f"Delta not found: {delta_id}")

        statuses = {d.get("review_status") for d in batch.get("deltas") or []}
        if statuses <= {"approved"}:
            batch["review_status"] = "approved"
            target_dir = self.delta_root / "approved"
        elif statuses <= {"rejected"}:
            batch["review_status"] = "rejected"
            target_dir = self.delta_root / "rejected"
        elif "pending" in statuses:
            batch["review_status"] = "pending"
            target_dir = self.delta_root / "pending"
        else:
            # Mixed approved/rejected with no pending is resolved and ready for KM on approved subset.
            batch["review_status"] = "reviewed"
            target_dir = self.delta_root / "approved"
        target = target_dir / path.name
        atomic_write(target, yaml.safe_dump(batch, allow_unicode=True, sort_keys=False))
        if path.resolve() != target.resolve() and path.exists():
            path.unlink()
        return target


def load_json_arg(value: str) -> Any:
    p = Path(value)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(value)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Distiller V1")
    p.add_argument("--root", help="Cognitive Engine root")
    p.add_argument("--vault-root", help="Obsidian Vault root")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("prepare")
    a.add_argument("--trace", required=True, help="Trace path or session id")
    a.add_argument("--top-k", type=int, default=8)

    e = sub.add_parser("emit")
    e.add_argument("--packet", required=True)
    e.add_argument("--proposals-json", required=True, help="JSON array or path to JSON file")
    e.add_argument("--summary")

    r = sub.add_parser("review")
    r.add_argument("--delta-file", required=True)
    r.add_argument("--delta-id", required=True)
    r.add_argument("--decision", choices=["approve", "reject"], required=True)
    r.add_argument("--note")

    return p


def main() -> None:
    args = parser().parse_args()
    d = Distiller(args.root, args.vault_root)
    if args.command == "prepare":
        print(d.prepare(args.trace, args.top_k))
    elif args.command == "emit":
        proposals = load_json_arg(args.proposals_json)
        if not isinstance(proposals, list):
            raise ValueError("proposals-json must contain a JSON array")
        print(d.emit(args.packet, proposals, args.summary))
    elif args.command == "review":
        print(d.review(args.delta_file, args.delta_id, args.decision, args.note))


if __name__ == "__main__":
    main()
