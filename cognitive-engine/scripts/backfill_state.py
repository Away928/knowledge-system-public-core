#!/usr/bin/env python3
"""Persist machine-local ChatGPT weekly backfill orchestration state."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Singapore"
STAGES = (
    "idle",
    "export_requested",
    "confirmation_seen",
    "export_ready_seen",
    "zip_available",
    "dry_run_complete",
    "import_complete",
)


class BackfillState:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "runtime" / "chatgpt-backfill-state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": 1,
                "timezone": DEFAULT_TIMEZONE,
                "stage": "idle",
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("backfill state must be a JSON object")
        return data

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
        return data

    def begin(
        self,
        *,
        window_start: str,
        window_end: str,
        request_submitted_at: str | None = None,
    ) -> dict[str, Any]:
        data = {
            "version": 1,
            "timezone": DEFAULT_TIMEZONE,
            "stage": "export_requested",
            "window_start": window_start,
            "window_end": window_end,
            "request_submitted_at": request_submitted_at
            or datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(),
            "confirmation_seen_at": None,
            "export_ready_seen_at": None,
            "zip_path": None,
            "dry_run_summary_path": None,
            "import_completed_at": None,
        }
        return self.save(data)

    def mark(self, stage: str, **updates: Any) -> dict[str, Any]:
        if stage not in STAGES:
            raise ValueError(f"invalid stage: {stage}")
        data = self.load()
        data["stage"] = stage
        data.update(updates)
        return self.save(data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("status")

    begin = sub.add_parser("begin")
    begin.add_argument("--window-start", required=True)
    begin.add_argument("--window-end", required=True)
    begin.add_argument("--request-submitted-at")

    mark = sub.add_parser("mark")
    mark.add_argument("stage", choices=STAGES)
    mark.add_argument("--confirmation-seen-at")
    mark.add_argument("--export-ready-seen-at")
    mark.add_argument("--zip-path")
    mark.add_argument("--dry-run-summary-path")
    mark.add_argument("--import-completed-at")

    args = ap.parse_args()
    state = BackfillState(args.root)

    if args.command == "status":
        result = state.load()
    elif args.command == "begin":
        result = state.begin(
            window_start=args.window_start,
            window_end=args.window_end,
            request_submitted_at=args.request_submitted_at,
        )
    else:
        updates = {
            key: value
            for key, value in {
                "confirmation_seen_at": args.confirmation_seen_at,
                "export_ready_seen_at": args.export_ready_seen_at,
                "zip_path": args.zip_path,
                "dry_run_summary_path": args.dry_run_summary_path,
                "import_completed_at": args.import_completed_at,
            }.items()
            if value is not None
        }
        result = state.mark(args.stage, **updates)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
