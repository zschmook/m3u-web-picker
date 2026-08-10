from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sports


class ScheduleApiCacheStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "sports.db"
        sports.init_db(self.db_path)
        sports.add_rule(
            self.db_path,
            {"scope_type": "league", "scope_id": "nfl", "feed_preference": "best"},
        )
        sports.update_schedule_api_config(
            self.db_path,
            enabled=True,
            api_key="test-api-key",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_successful_zero_game_fetch_is_still_a_cache(self):
        dataset = sports.SCHEDULE_API_DATASETS["nfl"]
        with sports.closing(sports._connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO sports_schedule_api_cache
                    (source, league_id, season, schedule_date, request_key,
                     fetched_on, fetched_at, result_count, remaining_quota)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset["source"],
                    dataset["league_id"],
                    2026,
                    "2026-08-10",
                    json.dumps({"test": True}),
                    "2026-08-10",
                    "2026-08-10T07:30:00-04:00",
                    0,
                    93,
                ),
            )
            conn.commit()

        payload = sports.schedule_api_status_payload(self.db_path)
        nfl = next(item for item in payload["apis"] if item["id"] == "nfl")
        self.assertEqual(nfl["status_code"], "cached")
        self.assertEqual(nfl["status_label"], "Cached")
        self.assertEqual(nfl["cached_event_count"], 0)
        self.assertIsNotNone(nfl["last_fetch_at"])
        self.assertEqual(payload["dataset_summary"]["cached"], 1)


if __name__ == "__main__":
    unittest.main()
