from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
        self.now = datetime(
            2026,
            8,
            10,
            7,
            30,
            tzinfo=ZoneInfo("America/New_York"),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _insert_cache(self, *, fetched_on: str, fetched_at: str, result_count: int) -> None:
        dataset = sports.SCHEDULE_API_DATASETS["nfl"]
        settings = sports.get_settings(self.db_path)
        season = sports._schedule_api_dataset_season(dataset, self.now)
        required_dates = sports._schedule_api_required_dates(self.now, settings)
        timezone_name = settings["timezone"]
        with sports.closing(sports._connect(self.db_path)) as conn:
            for schedule_date in required_dates:
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
                        season,
                        schedule_date.isoformat(),
                        sports._schedule_api_request_key(
                            dataset,
                            schedule_date=schedule_date,
                            season=season,
                            timezone=timezone_name,
                        ),
                        fetched_on,
                        fetched_at,
                        result_count,
                        93,
                    ),
                )
            conn.commit()

    def test_successful_zero_game_fetch_is_still_a_current_cache(self):
        self._insert_cache(
            fetched_on="2026-08-10",
            fetched_at="2026-08-10T07:30:00-04:00",
            result_count=0,
        )

        payload = sports.schedule_api_status_payload(self.db_path, now=self.now)
        nfl = next(item for item in payload["apis"] if item["id"] == "nfl")
        self.assertEqual(nfl["status_code"], "cached")
        self.assertEqual(nfl["status_label"], "Cached")
        self.assertEqual(nfl["cached_event_count"], 0)
        self.assertTrue(nfl["cache_current"])
        self.assertIsNotNone(nfl["last_fetch_at"])
        self.assertEqual(payload["dataset_summary"]["cached"], 1)

    def test_old_cache_is_reported_as_stale_not_current(self):
        self._insert_cache(
            fetched_on="2026-08-09",
            fetched_at="2026-08-09T07:30:00-04:00",
            result_count=4,
        )

        payload = sports.schedule_api_status_payload(self.db_path, now=self.now)
        nfl = next(item for item in payload["apis"] if item["id"] == "nfl")
        self.assertEqual(nfl["status_code"], "stale")
        self.assertEqual(nfl["status_label"], "Stale cache")
        self.assertFalse(nfl["cache_current"])
        self.assertEqual(payload["dataset_summary"]["cached"], 0)
        self.assertEqual(payload["dataset_summary"]["issues"], 1)


if __name__ == "__main__":
    unittest.main()
