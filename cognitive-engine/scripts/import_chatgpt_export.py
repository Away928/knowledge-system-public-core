#!/usr/bin/env python3
"""Import ChatGPT account Data Export conversations into Capture Gateway.

Principles:
- only backfill messages missing from the Capture Ledger;
- never overwrite realtime provenance;
- preserve original conversation/message identities and timestamps;
- do not invent Session boundaries: imported messages go through Gateway routing.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from capture_gateway import Gateway


SUPPORTED_ROLES = {"user", "assistant"}
DEFAULT_TIMEZONE = "Asia/Singapore"


def weekly_window(
    reference: str | date | datetime,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> tuple[datetime, datetime]:
    """Return the Sunday 13:00 -> next Sunday 13:00 local weekly window."""
    tz = ZoneInfo(timezone_name)
    if isinstance(reference, str):
        ref_dt = datetime.combine(
            date.fromisoformat(reference),
            time(hour=13),
            tzinfo=tz,
        )
    elif isinstance(reference, datetime):
        ref_dt = (
            reference.replace(tzinfo=tz)
            if reference.tzinfo is None
            else reference.astimezone(tz)
        )
    else:
        ref_dt = datetime.combine(reference, time(hour=13), tzinfo=tz)

    # Python weekday: Monday=0 ... Sunday=6.
    days_since_sunday = (ref_dt.weekday() + 1) % 7
    start_date = ref_dt.date() - timedelta(days=days_since_sunday)
    start = datetime.combine(start_date, time(hour=13), tzinfo=tz)
    if ref_dt < start:
        start -= timedelta(days=7)
    end = start + timedelta(days=7)
    return start, end


def previous_complete_week(
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone_name)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    current_start, _ = weekly_window(local_now, timezone_name)
    end = current_start
    start = end - timedelta(days=7)
    return start, end


def iso_from_export_time(value: Any) -> str | None:
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def extract_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""

    out: list[str] = []
    for part in parts:
        if isinstance(part, str):
            text = part.strip()
            if text:
                out.append(text)
        elif isinstance(part, dict):
            # Some export variants wrap textual payloads in objects.
            for key in ("text", "content"):
                value = part.get(key)
                if isinstance(value, str) and value.strip():
                    out.append(value.strip())
                    break
    return "\n\n".join(out).strip()


def canonical_node_ids(conversation: dict[str, Any]) -> list[str]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []

    current = conversation.get("current_node")
    if isinstance(current, str) and current in mapping:
        chain: list[str] = []
        seen: set[str] = set()
        node_id: str | None = current
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            chain.append(node_id)
            parent = mapping[node_id].get("parent")
            node_id = parent if isinstance(parent, str) else None
        chain.reverse()
        return chain

    # Fallback for exports lacking current_node: keep message-bearing nodes in
    # create-time order. This does not fabricate conversation/Session boundaries.
    rows: list[tuple[float, str]] = []
    for node_id, node in mapping.items():
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        try:
            created = float(message.get("create_time") or 0.0)
        except (TypeError, ValueError):
            created = 0.0
        rows.append((created, str(node_id)))
    rows.sort(key=lambda item: (item[0], item[1]))
    return [node_id for _, node_id in rows]


def iter_messages(conversation: dict[str, Any]) -> Iterable[dict[str, Any]]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return

    ordinal = 0
    for node_id in canonical_node_ids(conversation):
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = (
            str(author.get("role") or "").strip().lower()
            if isinstance(author, dict)
            else ""
        )
        if role not in SUPPORTED_ROLES:
            continue
        text = extract_text(message.get("content"))
        if not text:
            continue

        message_id = str(message.get("id") or node_id).strip()
        yield {
            "message_id": message_id,
            "ordinal": ordinal,
            "role": role,
            "content": text,
            "original_timestamp": iso_from_export_time(message.get("create_time")),
        }
        ordinal += 1


class ChatGPTExportImporter:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.gateway = Gateway(self.root)

    def import_conversations(
        self,
        conversations: list[dict[str, Any]],
        *,
        dry_run: bool = False,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        timezone_name: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        if (window_start is None) != (window_end is None):
            raise ValueError("window_start and window_end must be provided together")
        if window_start is not None and window_start.tzinfo is None:
            raise ValueError("window_start must be timezone-aware")
        if window_end is not None and window_end.tzinfo is None:
            raise ValueError("window_end must be timezone-aware")

        summary = {
            "conversations_seen": 0,
            "conversations_with_messages": 0,
            "messages_seen": 0,
            "messages_in_window": 0,
            "messages_outside_window": 0,
            "inserted": 0,
            "updated": 0,
            "preserved_realtime": 0,
            "already_present": 0,
            "errors": [],
            "timezone": timezone_name,
            "window_start": window_start.isoformat() if window_start else None,
            "window_end": window_end.isoformat() if window_end else None,
            "selected_conversations": [],
        }

        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            conversation_id = str(
                conversation.get("id")
                or conversation.get("conversation_id")
                or ""
            ).strip()
            if not conversation_id:
                continue
            summary["conversations_seen"] += 1
            conversation_has_message = False
            conversation_message_count = 0

            for msg in iter_messages(conversation):
                summary["messages_seen"] += 1
                if window_start is not None and window_end is not None:
                    raw_ts = msg.get("original_timestamp")
                    if not raw_ts:
                        summary["messages_outside_window"] += 1
                        continue
                    created = datetime.fromisoformat(str(raw_ts))
                    if not (window_start <= created.astimezone(window_start.tzinfo) < window_end):
                        summary["messages_outside_window"] += 1
                        continue

                summary["messages_in_window"] += 1
                conversation_has_message = True
                conversation_message_count += 1
                payload = {
                    "conversation_id": conversation_id,
                    "message_id": msg["message_id"],
                    "ordinal": msg["ordinal"],
                    "role": msg["role"],
                    "content": msg["content"],
                    "url": f"https://chatgpt.com/c/{conversation_id}",
                    "capture_mode": "backfill",
                    "observed_at": msg["original_timestamp"],
                    "original_timestamp": msg["original_timestamp"],
                }

                if dry_run:
                    continue

                try:
                    result = self.gateway.ingest_event(payload)
                except Exception as exc:
                    summary["errors"].append(
                        {
                            "conversation_id": conversation_id,
                            "message_id": msg["message_id"],
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue

                if result.get("preserved_realtime"):
                    summary["preserved_realtime"] += 1
                elif result.get("inserted"):
                    summary["inserted"] += 1
                elif result.get("updated"):
                    summary["updated"] += 1
                else:
                    summary["already_present"] += 1

            if conversation_has_message:
                summary["conversations_with_messages"] += 1
                summary["selected_conversations"].append(
                    {
                        "conversation_id": conversation_id,
                        "title": str(conversation.get("title") or "").strip(),
                        "messages_in_window": conversation_message_count,
                    }
                )

        return summary

    @staticmethod
    def load_export(path: str | Path) -> list[dict[str, Any]]:
        source = Path(path)
        if source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as archive:
                candidates = [
                    name
                    for name in archive.namelist()
                    if Path(name).name == "conversations.json"
                ]
                if not candidates:
                    raise ValueError(
                        "ChatGPT export ZIP does not contain conversations.json"
                    )
                # Prefer the shortest path if an export contains nested copies.
                name = sorted(candidates, key=lambda item: (item.count("/"), len(item)))[0]
                data = json.loads(archive.read(name).decode("utf-8"))
        else:
            data = json.loads(source.read_text(encoding="utf-8"))

        if not isinstance(data, list):
            raise ValueError("conversations.json must contain a JSON array")
        return data

    def import_file(
        self,
        path: str | Path,
        *,
        dry_run: bool = False,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        timezone_name: str = DEFAULT_TIMEZONE,
    ) -> dict[str, Any]:
        source = Path(path)
        data = self.load_export(source)
        result = self.import_conversations(
            data,
            dry_run=dry_run,
            window_start=window_start,
            window_end=window_end,
            timezone_name=timezone_name,
        )
        result["source"] = str(source)
        result["dry_run"] = dry_run
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("conversations_json", type=Path)
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="IANA timezone for weekly windows (default: Asia/Singapore)",
    )
    group = ap.add_mutually_exclusive_group()
    group.add_argument(
        "--week",
        metavar="YYYY-MM-DD",
        help="Import only the Sunday 13:00 -> next Sunday 13:00 week containing this date",
    )
    group.add_argument(
        "--current-week",
        action="store_true",
        help="Import the current local Sunday 13:00 -> Sunday 13:00 week",
    )
    group.add_argument(
        "--previous-week",
        action="store_true",
        help="Import the previous complete local Sunday 13:00 -> Sunday 13:00 week",
    )
    args = ap.parse_args()

    window_start = None
    window_end = None
    if args.week:
        window_start, window_end = weekly_window(args.week, args.timezone)
    elif args.current_week:
        window_start, window_end = weekly_window(
            datetime.now(ZoneInfo(args.timezone)),
            args.timezone,
        )
    elif args.previous_week:
        window_start, window_end = previous_complete_week(
            timezone_name=args.timezone
        )

    importer = ChatGPTExportImporter(args.root)
    result = importer.import_file(
        args.conversations_json,
        dry_run=args.dry_run,
        window_start=window_start,
        window_end=window_end,
        timezone_name=args.timezone,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
