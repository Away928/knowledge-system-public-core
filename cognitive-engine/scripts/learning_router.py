#!/usr/bin/env python3
"""Conservative message/segment-level Learning Unit router.

This router deliberately favors unresolved over a wrong assignment. Its
profiles are derived from runtime Learning Units, existing Session topic chains,
and staging filenames rather than hard-coded finance concept maps.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

try:
    import pymupdf
except ImportError:  # Optional: Router falls back to non-PDF evidence.
    pymupdf = None

TOKEN_RE = re.compile(r"[a-z][a-z0-9+.-]{1,}|\d{3,4}[a-z]?|[\u4e00-\u9fff]{2,}", re.I)
DEICTIC_RE = re.compile(r"^(这个|这里|这边|它|这个公式|这个数|这里的|为什么这里|这个\s)", re.I)
COMPARISON_RE = re.compile(r"哪个更|还是|哪个|孰优|better", re.I)
STOP = {
    "the","and","for","with","from","what","why","how","does","this","that","are","is","to","of","in","on",
    "lecture","tutorial","solution","answer","review","course","sample","test","part","introduction","pdf","xlsx",
    "md","html","txt","2026","2610","financial","finance","per","annum","ii","iii","iv","vi",
}


def tokens(text: str) -> list[str]:
    out = []
    for raw in TOKEN_RE.findall(text.lower()):
        raw = raw.strip(".-+")
        if len(raw) < 2 or raw in STOP:
            continue
        out.append(raw)
    return out


class LearningUnitRouter:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.threads_path = self.root / "runtime" / "learning-threads.yaml"
        self._profiles_cache_signature: tuple[Any, ...] | None = None
        self._profiles_cache: dict[str, Counter[str]] | None = None
        self._pdf_phrase_cache_signature: tuple[Any, ...] | None = None
        self._pdf_phrase_cache: dict[str, tuple[set[tuple[str, str]], set[tuple[str, str, str]]]] | None = None

    def _state(self) -> dict[str, Any]:
        if not self.threads_path.exists():
            return {}
        return yaml.safe_load(self.threads_path.read_text(encoding="utf-8")) or {}

    def candidate_units(self) -> list[dict[str, Any]]:
        state = self._state()
        units = []
        for unit_id, item in (state.get("learning_threads") or {}).items():
            if item.get("learning_state") != "Learning":
                continue
            if item.get("thread_state") == "paused":
                continue
            units.append({"unit_id": unit_id, **item})
        return units

    def _staging_names(self, unit_id: str) -> list[str]:
        course = unit_id.split("-")[0].lower()
        path = self.root / "runtime" / f"{course}-staging"
        if not path.exists():
            return []
        return [p.name for p in path.iterdir() if p.is_file()][:1000]

    def _profile_text(self, unit: dict[str, Any]) -> str:
        parts = [unit.get("unit_id", ""), unit.get("title", "")]
        for s in (unit.get("sessions") or {}).values():
            parts.extend(s.get("topic_chain") or [])
        parts.extend(self._staging_names(str(unit.get("unit_id") or "")))
        return " ".join(str(x) for x in parts if x)

    def _profiles_signature(self, units: list[dict[str, Any]]) -> tuple[Any, ...]:
        items: list[Any] = []
        if self.threads_path.exists():
            stat = self.threads_path.stat()
            items.append(("threads", stat.st_mtime_ns, stat.st_size))
        for unit in units:
            if unit.get("sessions"):
                continue
            course = str(unit.get("unit_id") or "").split("-")[0].lower()
            path = self.root / "runtime" / f"{course}-staging"
            if not path.exists():
                continue
            for file in sorted(path.glob("*.md")):
                stat = file.stat()
                items.append((str(file), stat.st_mtime_ns, stat.st_size))
        return tuple(items)

    def profiles(self) -> dict[str, Counter[str]]:
        units = self.candidate_units()
        signature = self._profiles_signature(units)
        if (
            self._profiles_cache is not None
            and self._profiles_cache_signature == signature
        ):
            return self._profiles_cache

        profiles = {
            str(u["unit_id"]): Counter(tokens(self._profile_text(u)))
            for u in units
        }

        # Units with no real Session topic chains are initially profile-poor.
        # Bootstrap only those Units from readable course sidecars. Cap each
        # token's extra frequency so repetitive workbook cells cannot dominate.
        for unit in units:
            if unit.get("sessions"):
                continue
            unit_id = str(unit["unit_id"])
            course = unit_id.split("-")[0].lower()
            path = self.root / "runtime" / f"{course}-staging"
            if not path.exists():
                continue
            extra: Counter[str] = Counter()
            for file in sorted(path.glob("*.md")):
                try:
                    text = file.read_text(encoding="utf-8", errors="ignore")[:50000]
                except OSError:
                    continue
                extra.update(tokens(text))
            for token, count in extra.items():
                profiles[unit_id][token] += min(count, 2)

        self._profiles_cache = profiles
        self._profiles_cache_signature = signature
        return profiles

    @staticmethod
    def _title_alias(title: str) -> str:
        alias = re.sub(r"fin\d{4}[a-z]?", "", title, flags=re.I)
        alias = re.sub(r"\[[^\]]*\]", "", alias)
        return " ".join(alias.lower().split())

    def _pdf_phrase_signature(self, units: list[dict[str, Any]]) -> tuple[Any, ...]:
        items: list[Any] = []
        for unit in units:
            course = str(unit.get("unit_id") or "").split("-")[0].lower()
            path = self.root / "runtime" / f"{course}-staging"
            if not path.exists():
                continue
            for file in sorted(path.glob("*.pdf")):
                stat = file.stat()
                items.append((str(file), stat.st_mtime_ns, stat.st_size))
        return tuple(items)

    def _pdf_phrase_profiles(
        self,
        units: list[dict[str, Any]],
    ) -> dict[str, tuple[set[tuple[str, str]], set[tuple[str, str, str]]]] | None:
        if pymupdf is None:
            return None

        signature = self._pdf_phrase_signature(units)
        if (
            self._pdf_phrase_cache is not None
            and self._pdf_phrase_cache_signature == signature
        ):
            return self._pdf_phrase_cache

        result: dict[str, tuple[set[tuple[str, str]], set[tuple[str, str, str]]]] = {}
        for unit in units:
            unit_id = str(unit["unit_id"])
            course = unit_id.split("-")[0].lower()
            path = self.root / "runtime" / f"{course}-staging"
            bigrams: set[tuple[str, str]] = set()
            trigrams: set[tuple[str, str, str]] = set()
            if path.exists():
                for file in sorted(path.glob("*.pdf")):
                    try:
                        with pymupdf.open(file) as doc:
                            text = "\n".join(page.get_text() for page in doc)
                    except Exception:
                        continue
                    ts = tokens(text[:120000])
                    bigrams.update(zip(ts, ts[1:]))
                    trigrams.update(zip(ts, ts[1:], ts[2:]))
            result[unit_id] = (bigrams, trigrams)

        self._pdf_phrase_cache = result
        self._pdf_phrase_cache_signature = signature
        return result

    def _pdf_phrase_support(
        self,
        content: str,
        units: list[dict[str, Any]],
    ) -> dict[str, int] | None:
        profiles = self._pdf_phrase_profiles(units)
        if profiles is None:
            return None
        ts = tokens(content)
        query_bigrams = set(zip(ts, ts[1:]))
        query_trigrams = set(zip(ts, ts[1:], ts[2:]))
        return {
            unit_id: (
                len(query_bigrams & bigrams)
                + 2 * len(query_trigrams & trigrams)
            )
            for unit_id, (bigrams, trigrams) in profiles.items()
        }

    @staticmethod
    def _course_codes(unit_id: str, title: str) -> set[str]:
        text = f"{unit_id} {title}".lower()
        return set(re.findall(r"fin\d{4}[a-z]?", text))

    def route(
        self,
        content: str,
        *,
        role: str = "user",
        previous_unit_id: str | None = None,
    ) -> dict[str, Any]:
        units = self.candidate_units()
        if not units:
            return {"unit_id": None, "confidence": 0.0, "reason": "no_active_learning_units"}

        # Assistant replies inherit the current learning segment. They do not
        # independently decide course ownership from generated text.
        if role == "assistant" and previous_unit_id:
            return {
                "unit_id": previous_unit_id,
                "confidence": 0.99,
                "reason": "assistant_inherits_current_segment",
            }

        lower = content.lower()
        explicit = []
        for u in units:
            codes = self._course_codes(str(u["unit_id"]), str(u.get("title") or ""))
            if any(code in lower.replace(" ", "") for code in codes):
                explicit.append(str(u["unit_id"]))
        if len(explicit) == 1:
            return {"unit_id": explicit[0], "confidence": 1.0, "reason": "explicit_course_code"}

        normalized_content = " ".join(content.lower().split())
        title_hits = []
        for unit in units:
            alias = self._title_alias(str(unit.get("title") or ""))
            if alias and alias in normalized_content:
                title_hits.append(str(unit["unit_id"]))
        if len(title_hits) == 1:
            return {
                "unit_id": title_hits[0],
                "confidence": 0.995,
                "reason": "explicit_unit_title",
            }

        q_tokens = tokens(content)
        if (
            role == "user"
            and previous_unit_id is None
            and DEICTIC_RE.search(content.strip())
            and len(q_tokens) <= 5
        ):
            return {
                "unit_id": None,
                "confidence": 0.0,
                "reason": "context_required_deictic",
            }

        q = Counter(q_tokens)
        if not q:
            if previous_unit_id:
                return {
                    "unit_id": previous_unit_id,
                    "confidence": 0.66,
                    "reason": "low_information_continue_segment",
                }
            return {"unit_id": None, "confidence": 0.0, "reason": "no_routing_signal"}

        profiles = self.profiles()
        if not profiles:
            return {"unit_id": None, "confidence": 0.0, "reason": "no_profiles"}

        if role == "user" and previous_unit_id is None and COMPARISON_RE.search(content):
            exclusive_units: set[str] = set()
            for token in q:
                owners = [unit_id for unit_id, profile in profiles.items() if token in profile]
                if len(owners) == 1:
                    exclusive_units.add(owners[0])
            if len(exclusive_units) < 2:
                pdf_support = self._pdf_phrase_support(content, units)
                if pdf_support is not None:
                    for unit_id, score in pdf_support.items():
                        if score > 0:
                            exclusive_units.add(unit_id)
            if len(exclusive_units) >= 2:
                return {
                    "unit_id": None,
                    "confidence": 0.0,
                    "reason": "cross_unit_comparison",
                }

        document_frequency = Counter()
        for profile in profiles.values():
            for tok in profile:
                document_frequency[tok] += 1

        n_units = len(profiles)
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        for unit_id, profile in profiles.items():
            score = 0.0
            matched = []
            for tok, qn in q.items():
                if tok not in profile:
                    continue
                idf = math.log((n_units + 1) / (document_frequency[tok] + 0.5)) + 1.0
                tf = 1.0 + math.log(1.0 + profile[tok])
                score += min(qn, 2) * idf * tf
                matched.append(tok)
            if previous_unit_id == unit_id and score > 0:
                score += 0.8
            scores[unit_id] = score
            evidence[unit_id] = matched[:8]

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_id, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top - second

        if top >= 3.0 and margin >= 1.5:
            conf = min(0.97, 0.72 + 0.05 * margin + 0.025 * top)
            result = {
                "unit_id": top_id,
                "confidence": round(conf, 3),
                "reason": "profile_match",
                "evidence": evidence[top_id],
                "scores": {k: round(v, 3) for k, v in scores.items()},
            }

            if (
                role == "user"
                and previous_unit_id is None
                and (margin < 2.0 or len(evidence[top_id]) <= 1)
            ):
                pdf_support = self._pdf_phrase_support(content, units)
                if pdf_support is not None and pdf_support:
                    ranked_pdf = sorted(
                        pdf_support.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                    pdf_id, pdf_score = ranked_pdf[0]
                    pdf_second = ranked_pdf[1][1] if len(ranked_pdf) > 1 else 0
                    if (
                        pdf_score > 0
                        and pdf_id != top_id
                        and pdf_score > pdf_second
                    ):
                        return {
                            **result,
                            "unit_id": pdf_id,
                            "confidence": 0.86,
                            "reason": "pdf_phrase_correction",
                            "pdf_phrase_support": pdf_support,
                        }
                    if margin < 2.0 and max(pdf_support.values(), default=0) == 0:
                        return {
                            **result,
                            "unit_id": None,
                            "confidence": 0.0,
                            "reason": "weak_margin_unresolved",
                        }

            return result

        if previous_unit_id and top_id == previous_unit_id and top >= 1.5 and margin >= 0.4:
            return {
                "unit_id": previous_unit_id,
                "confidence": 0.70,
                "reason": "weak_match_continue_segment",
                "evidence": evidence[top_id],
                "scores": {k: round(v, 3) for k, v in scores.items()},
            }

        return {
            "unit_id": None,
            "confidence": 0.0,
            "reason": "ambiguous",
            "candidate_unit_id": top_id if top > 0 else None,
            "evidence": evidence.get(top_id, []),
            "scores": {k: round(v, 3) for k, v in scores.items()},
        }
