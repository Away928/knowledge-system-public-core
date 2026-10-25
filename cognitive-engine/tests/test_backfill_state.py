from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.backfill_state import BackfillState


class BackfillStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "runtime").mkdir()
        self.state = BackfillState(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_begin_and_mark_progression(self):
        data = self.state.begin(
            window_start="2026-10-04T13:00:00+08:00",
            window_end="2026-10-11T13:00:00+08:00",
            request_submitted_at="2026-10-11",
        )
        self.assertEqual(data["stage"], "export_requested")

        data = self.state.mark(
            "confirmation_seen",
            confirmation_seen_at="2026-10-11T14:10:19+08:00",
        )
        self.assertEqual(data["stage"], "confirmation_seen")
        self.assertEqual(
            data["window_start"],
            "2026-10-04T13:00:00+08:00",
        )
        self.assertEqual(
            data["confirmation_seen_at"],
            "2026-10-11T14:10:19+08:00",
        )

    def test_invalid_stage_rejected(self):
        with self.assertRaises(ValueError):
            self.state.mark("not-a-stage")


if __name__ == "__main__":
    unittest.main()
