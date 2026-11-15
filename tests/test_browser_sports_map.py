from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, Response
from api import guide, roku_app


class BrowserSportsMapTests(unittest.TestCase):
    identity = 'm3u-picker-sports-abc123'
    path = '/guide/play/sports/id/' + identity

    def setUp(self):
        self.app = Flask(__name__)
        guide.register_guide_routes(self.app)
        roku_app.register_roku_app_routes(self.app)
        self.client = self.app.test_client()
        core = patch.object(guide, 'core', SimpleNamespace(DB_PATH=Path('/tmp/map-test.db')), create=True)
        core.start()
        self.addCleanup(core.stop)

    def test_browser_and_roku_share_the_same_prepared_cache(self):
        payload = dict(source='picker-generated', states=[], totalGames=6505, cacheLoading=False)
        cache = SimpleNamespace(snapshot=Mock(return_value=payload))
        self.app.extensions['sports_map_daily_cache'] = cache
        for route in ['/api/guide/sports-map', '/api/roku/sports-map']:
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, payload)
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(cache.snapshot.call_count, 2)

    def test_cached_identity_resolves_new_number_under_publication_lock(self):
        rows = [dict(tvg_id=self.identity, assigned_number=900001)]

        def target(_db, number):
            self.assertTrue(guide.generated_publish_lock._is_owned())
            self.assertEqual(number, 900001)
            return 'http://provider.invalid/current-feed'

        with patch.object(guide.sports, 'generated_rows', return_value=rows), \
                patch.object(guide.sports, 'generated_stream_target', side_effect=target), \
                patch.object(guide.browser, 'response_for', return_value=Response('video')) as play:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        play.assert_called_once_with('http://provider.invalid/current-feed')

    def test_removed_identity_never_plays_reused_number(self):
        rows = [dict(tvg_id='m3u-picker-sports-def456', assigned_number=3000)]
        with patch.object(guide.sports, 'generated_rows', return_value=rows), \
                patch.object(guide.sports, 'generated_stream_target') as resolve, \
                patch.object(guide.browser, 'response_for') as play:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 410)
        resolve.assert_not_called()
        play.assert_not_called()

    def test_stable_paths_work_for_existing_hls_destinations(self):
        rows = [dict(tvg_id=self.identity, assigned_number=7200)]
        with patch.object(guide.sports, 'generated_rows', return_value=rows), \
                patch.object(guide.sports, 'generated_stream_target', return_value='http://provider.invalid/feed'), \
                patch.object(guide.hls, 'start_session', return_value=SimpleNamespace(token='test-session')) as start:
            response = self.client.post('/api/guide/cast/start', json=dict(play_url=self.path))
        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with(['http://provider.invalid/feed'], on_target=None)
        self.assertNotIn('provider.invalid', response.get_data(as_text=True))

    def test_malformed_ids_and_arbitrary_urls_cannot_resolve_streams(self):
        with patch.object(guide.sports, 'generated_rows') as rows:
            for path in ['/guide/play/sports/id/other', '/guide/play/sports/id/m3u-picker-sports-zzzz',
                         'https://example.invalid/feed', '/guide/play/sports/id/../../../etc/passwd']:
                self.assertEqual(guide._resolve_guide_play_target(path), '')
        rows.assert_not_called()


if __name__ == '__main__':
    unittest.main()
