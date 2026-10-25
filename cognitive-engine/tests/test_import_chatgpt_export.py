from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from scripts.capture_gateway import Gateway
from scripts.import_chatgpt_export import (
    ChatGPTExportImporter,
    iter_messages,
    previous_complete_week,
    weekly_window,
)


UNIT_3703 = "fin3703a-financial-markets-2610"
UNIT_3701 = "fin3701a-corporate-finance-2610"


def message(mid, role, text, create_time):
    return {
        "id": mid,
        "author": {"role": role},
        "create_time": create_time,
        "content": {"content_type": "text", "parts": [text]},
    }


class ChatGPTExportImporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        runtime = self.root / "runtime"
        runtime.mkdir()
        (runtime / "fin3701a-staging").mkdir()
        (runtime / "fin3703a-staging").mkdir()
        state = {
            "version": 2,
            "learning_threads": {
                UNIT_3701: {
                    "unit_id": UNIT_3701,
                    "title": "FIN3701A Corporate Finance [2610]",
                    "learning_state": "Learning",
                    "thread_state": "active",
                    "sessions": {},
                },
                UNIT_3703: {
                    "unit_id": UNIT_3703,
                    "title": "FIN3703A Financial Markets [2610]",
                    "learning_state": "Learning",
                    "thread_state": "active",
                    "sessions": {},
                },
            },
        }
        (runtime / "learning-threads.yaml").write_text(
            yaml.safe_dump(state, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.importer = ChatGPTExportImporter(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def conversation_fixture(self):
        return {
            "id": "conv-export-1",
            "title": "FIN3703A study",
            "current_node": "a1",
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": ["u1"],
                    "message": None,
                },
                "u1": {
                    "id": "u1",
                    "parent": "root",
                    "children": ["a1", "a-old"],
                    "message": message(
                        "msg-user-1",
                        "user",
                        "FIN3703A repo haircut 是什么意思？",
                        1700000000,
                    ),
                },
                "a1": {
                    "id": "a1",
                    "parent": "u1",
                    "children": [],
                    "message": message(
                        "msg-assistant-1",
                        "assistant",
                        "Repo haircut is a collateral discount.",
                        1700000001,
                    ),
                },
                "a-old": {
                    "id": "a-old",
                    "parent": "u1",
                    "children": [],
                    "message": message(
                        "msg-abandoned",
                        "assistant",
                        "This abandoned branch must not be imported.",
                        1700000002,
                    ),
                },
            },
        }

    def test_week_window_starts_sunday_at_1300_singapore(self):
        start, end = weekly_window("2026-10-05")
        self.assertEqual(start.isoformat(), "2026-10-04T13:00:00+08:00")
        self.assertEqual(end.isoformat(), "2026-10-11T13:00:00+08:00")

        before = datetime(
            2026, 10, 11, 12, 59,
            tzinfo=ZoneInfo("Asia/Singapore"),
        )
        after = datetime(
            2026, 10, 11, 13, 0,
            tzinfo=ZoneInfo("Asia/Singapore"),
        )
        before_start, _ = weekly_window(before)
        after_start, _ = weekly_window(after)
        self.assertEqual(
            before_start.isoformat(),
            "2026-10-04T13:00:00+08:00",
        )
        self.assertEqual(
            after_start.isoformat(),
            "2026-10-11T13:00:00+08:00",
        )

    def test_previous_complete_week_uses_sunday_1300_boundary(self):
        now = datetime(
            2026, 10, 11, 14, 0,
            tzinfo=ZoneInfo("Asia/Singapore"),
        )
        start, end = previous_complete_week(now)
        self.assertEqual(start.isoformat(), "2026-10-04T13:00:00+08:00")
        self.assertEqual(end.isoformat(), "2026-10-11T13:00:00+08:00")

    def test_iter_messages_uses_canonical_current_branch(self):
        rows = list(iter_messages(self.conversation_fixture()))
        self.assertEqual(
            [row["message_id"] for row in rows],
            ["msg-user-1", "msg-assistant-1"],
        )
        self.assertEqual([row["ordinal"] for row in rows], [0, 1])

    def test_import_routes_backfill_through_existing_gateway_pipeline(self):
        result = self.importer.import_conversations([self.conversation_fixture()])
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["errors"], [])

        rows = self.importer.gateway.ledger.events_for_conversation(
            "conv-export-1"
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["capture_mode"] == "backfill" for row in rows))
        self.assertTrue(
            all(row["learning_unit_id"] == UNIT_3703 for row in rows)
        )
        self.assertTrue(all(row["session_id"] for row in rows))
        self.assertEqual(rows[0]["session_id"], rows[1]["session_id"])
        self.assertEqual(rows[0]["original_timestamp"], "2023-11-14T22:13:20+00:00")
        self.assertEqual(rows[1]["original_timestamp"], "2023-11-14T22:13:21+00:00")

    def test_second_import_is_idempotent(self):
        first = self.importer.import_conversations([self.conversation_fixture()])
        second = self.importer.import_conversations([self.conversation_fixture()])
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["already_present"], 2)

    def test_import_file_accepts_export_zip(self):
        archive_path = self.root / "chatgpt-export.zip"
        payload = json.dumps(
            [self.conversation_fixture()],
            ensure_ascii=False,
        ).encode("utf-8")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("export/conversations.json", payload)
        result = self.importer.import_file(archive_path, dry_run=True)
        self.assertEqual(result["conversations_seen"], 1)
        self.assertEqual(result["messages_seen"], 2)
        self.assertEqual(result["inserted"], 0)
        self.assertTrue(result["dry_run"])

    def test_week_filter_is_message_level(self):
        start, end = weekly_window("2023-11-14")
        result = self.importer.import_conversations(
            [self.conversation_fixture()],
            dry_run=True,
            window_start=start,
            window_end=end,
        )
        self.assertEqual(result["messages_seen"], 2)
        self.assertEqual(result["messages_in_window"], 2)
        self.assertEqual(result["messages_outside_window"], 0)
        self.assertEqual(result["conversations_with_messages"], 1)
        self.assertEqual(
            result["selected_conversations"],
            [
                {
                    "conversation_id": "conv-export-1",
                    "title": "FIN3703A study",
                    "messages_in_window": 2,
                }
            ],
        )
        self.assertEqual(
            result["window_start"],
            "2023-11-12T13:00:00+08:00",
        )
        self.assertEqual(
            result["window_end"],
            "2023-11-19T13:00:00+08:00",
        )

        other_start, other_end = weekly_window("2023-11-21")
        outside = self.importer.import_conversations(
            [self.conversation_fixture()],
            dry_run=True,
            window_start=other_start,
            window_end=other_end,
        )
        self.assertEqual(outside["messages_in_window"], 0)
        self.assertEqual(outside["messages_outside_window"], 2)
        self.assertEqual(outside["conversations_with_messages"], 0)

    def test_existing_backfill_row_is_never_rewritten(self):
        first = self.importer.import_conversations([self.conversation_fixture()])
        self.assertEqual(first["inserted"], 2)

        changed = self.conversation_fixture()
        changed["mapping"]["u1"]["message"]["content"]["parts"] = [
            "FIN3703A changed export content"
        ]
        second = self.importer.import_conversations([changed])
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["already_present"], 2)

        rows = self.importer.gateway.ledger.events_for_conversation(
            "conv-export-1"
        )
        user = next(row for row in rows if row["role"] == "user")
        self.assertEqual(
            user["content"],
            "FIN3703A repo haircut 是什么意思？",
        )

    def test_backfill_never_overwrites_realtime_row(self):
        gateway = Gateway(self.root)
        realtime = gateway.ingest_event(
            {
                "conversation_id": "conv-export-1",
                "message_id": "msg-user-1",
                "ordinal": 0,
                "role": "user",
                "content": "FIN3703A realtime authoritative content",
                "url": "https://chatgpt.com/c/conv-export-1",
                "capture_mode": "realtime",
            }
        )
        self.assertTrue(realtime["inserted"])

        result = self.importer.import_conversations([self.conversation_fixture()])
        self.assertEqual(result["preserved_realtime"], 1)

        row = self.importer.gateway.ledger.event_by_key(realtime["event_key"])
        self.assertEqual(row["capture_mode"], "realtime")
        self.assertEqual(
            row["content"],
            "FIN3703A realtime authoritative content",
        )


if __name__ == "__main__":
    unittest.main()
