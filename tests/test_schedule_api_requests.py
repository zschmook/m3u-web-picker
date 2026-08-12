from __future__ import annotations

import json
import unittest
import urllib.parse
from datetime import date
from unittest.mock import MagicMock, patch

import sports
from sports import schedule_api_requests


class _FakeResponse:
    def __init__(self, payload: dict, headers: dict | None = None):
        self._raw = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size: int = -1):
        return self._raw


class ScheduleApiRequestCompatibilityTests(unittest.TestCase):
    def test_american_football_date_url_does_not_guess_remote_season(self):
        dataset = dict(sports.SCHEDULE_API_DATASETS["nfl"])
        url = schedule_api_requests._american_football_games_url(
            dataset,
            schedule_date=date(2026, 8, 10),
            timezone="America/New_York",
        )
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)

        self.assertEqual(parsed.netloc, "v1.american-football.api-sports.io")
        self.assertEqual(parsed.path, "/games")
        self.assertEqual(query["date"], ["2026-08-10"])
        self.assertEqual(query["timezone"], ["America/New_York"])
        self.assertNotIn("season", query)
        self.assertNotIn("league", query)

    def test_api_nfl_opener_removes_urllib_default_user_agent(self):
        opener = MagicMock()
        opener.addheaders = [("User-agent", "Python-urllib/test")]
        opener.open.return_value = object()
        request = object()

        with patch.object(
            schedule_api_requests.urllib.request,
            "build_opener",
            return_value=opener,
        ):
            result = schedule_api_requests._open_api_nfl(request, timeout=17)

        self.assertEqual(opener.addheaders, [])
        opener.open.assert_called_once_with(request, timeout=17)
        self.assertIs(result, opener.open.return_value)

    def test_facade_uses_compatibility_fetcher(self):
        self.assertIs(
            sports._fetch_schedule_api_dataset_date,
            schedule_api_requests._fetch_schedule_api_dataset_date,
        )

    def test_baseball_fetch_path_is_left_unchanged(self):
        dataset = dict(sports.SCHEDULE_API_DATASETS["mlb"])
        sentinel = {"dataset": "mlb", "games": 3}
        with patch.object(
            schedule_api_requests._base,
            "_fetch_schedule_api_dataset_date",
            return_value=sentinel,
        ) as original:
            result = schedule_api_requests._fetch_schedule_api_dataset_date(
                "/tmp/unused.db",
                dataset=dataset,
                api_key="test-key",
                schedule_date=date(2026, 8, 10),
                season=2026,
                timezone="America/New_York",
                fetched_on="2026-08-10",
            )

        self.assertEqual(result, sentinel)
        original.assert_called_once()

    def test_api_level_error_preserves_safe_service_detail(self):
        dataset = dict(sports.SCHEDULE_API_DATASETS["nfl"])
        response = _FakeResponse(
            {
                "errors": {
                    "season": "The requested season is not available."
                },
                "response": [],
            }
        )
        with patch.object(
            schedule_api_requests,
            "_open_api_nfl",
            return_value=response,
        ) as opened:
            with self.assertRaisesRegex(
                ValueError,
                "season: The requested season is not available",
            ):
                schedule_api_requests._fetch_schedule_api_dataset_date(
                    "/tmp/unused.db",
                    dataset=dataset,
                    api_key="super-secret-key",
                    schedule_date=date(2026, 8, 10),
                    season=2026,
                    timezone="America/New_York",
                    fetched_on="2026-08-10",
                )

        request = opened.call_args.args[0]
        self.assertNotIn("season=", request.full_url)
        self.assertNotIn("league=", request.full_url)
        self.assertNotIn("super-secret-key", str(request.full_url))

    def test_error_formatter_redacts_key_if_service_echoes_it(self):
        text = schedule_api_requests._api_error_text(
            {"auth": "bad key super-secret-key"},
            api_key="super-secret-key",
        )
        self.assertIn("[redacted]", text)
        self.assertNotIn("super-secret-key", text)


if __name__ == "__main__":
    unittest.main()
