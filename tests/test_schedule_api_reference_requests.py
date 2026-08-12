from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import sports
from sports import schedule_api_reference_requests


class ScheduleApiReferenceRequestTests(unittest.TestCase):
    def test_facade_uses_reference_compatibility_fetcher(self):
        self.assertIs(
            sports._refresh_ncaa_reference_metadata_if_needed,
            schedule_api_reference_requests._refresh_ncaa_reference_metadata_if_needed,
        )

    def test_reference_fetch_uses_clean_api_nfl_opener(self):
        # Stop before any database mutation.  This only verifies that a network
        # attempt is routed through the shared API-NFL-safe opener rather than
        # urllib's default opener.
        with patch.object(
            sports,
            "_connect",
        ) as connect, patch.object(
            schedule_api_reference_requests,
            "_open_api_nfl",
            side_effect=RuntimeError("network-stop"),
        ) as opened:
            cursor = MagicMock()
            cursor.execute.return_value.fetchone.return_value = None
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.__exit__.return_value = False
            connection.execute.return_value.fetchone.return_value = None
            connect.return_value = connection

            with self.assertRaisesRegex(ValueError, "RuntimeError"):
                schedule_api_reference_requests._refresh_ncaa_reference_metadata_if_needed(
                    "/tmp/unused.db",
                    api_key="test-key",
                    season=2026,
                )

        opened.assert_called_once()


if __name__ == "__main__":
    unittest.main()
