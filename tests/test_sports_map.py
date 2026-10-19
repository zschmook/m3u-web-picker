from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from flask import Flask, jsonify
import sports_map as sm
from api import roku_app

NOW = datetime(2026, 10, 18, 19, tzinfo=timezone.utc)


def feed(number=22000, identity='a', title='Providence Bruins at Hershey Bears', league='AHL', description=''):
    return dict(number=number, tvg_id='m3u-picker-sports-' + identity, generated=True,
                name=f'{league} • {title} — Bears Feed', play_url=f'/guide/play/sports/{number}',
                available=None, now=dict(title=title, description=description,
                                         start='2026-10-18T19:00:00Z', stop='2026-10-18T22:00:00Z'))


class SportsMapTests(unittest.TestCase):
    def games(self, payload):
        return [g for region in payload['states'] for g in region['games']]

    def test_only_generated_identity_and_exact_local_route_define_membership(self):
        good = feed()
        cases = [dict(good, generated=False), dict(good, tvg_id='manual-abc'),
                 dict(good, play_url='http://private/password'), dict(good, number='22000.5'),
                 dict(good, play_url='/guide/play/sports/22001')]
        payload = sm.make_map([good, *cases], now=NOW)
        self.assertEqual(payload['totalChannels'], 1)
        self.assertEqual(len(payload['states']), 66)
        self.assertEqual(payload['source'], 'picker-generated')
        self.assertNotIn('private', json.dumps(payload))
        self.assertEqual(self.games(payload)[0]['feeds'][0]['generated_id'], good['tvg_id'])

    def test_alt_feeds_share_bubble_and_explicit_home_then_second_team_locate_hockey(self):
        channels = [feed(), feed(22001, 'b'), feed(22002, 'c', title='Hershey Bears (home) vs Providence Bruins')]
        payload = sm.make_map(channels, now=NOW)
        games = self.games(payload)
        self.assertEqual(len(games), 2)
        self.assertEqual(sorted(len(g['feeds']) for g in games), [1, 2])
        self.assertTrue(all(g['region'] == 'PA' for g in games))
        self.assertTrue(all(g['locationInferred'] for g in games))

    def test_explicit_venue_wins_and_away_team_never_sets_region(self):
        channel = feed(description='From a neutral arena in Toronto, Ontario, Canada.')
        game = self.games(sm.make_map([channel], now=NOW))[0]
        self.assertEqual(game['region'], 'ON')
        self.assertFalse(game['locationInferred'])
        channel['now']['description'] = 'Providence, Rhode Island visits Hershey, Pennsylvania.'
        self.assertEqual(self.games(sm.make_map([channel], now=NOW))[0]['region'], 'PA')

    def test_us_province_and_foreign_venue_parsing(self):
        for text, region in [('From Prestonwood Country Club in Cary, N.C.', 'NC'),
                             ('From Toyota Arena in Ontario, CA.', 'CA'),
                             ('From Gregory Gymnasium in Austin, Texas.', 'TX'),
                             ('From an arena in Montréal, Québec, Canada.', 'QC'),
                             ('From a course in Shanghai, China.', 'INTL')]:
            with self.subTest(text=text):
                self.assertEqual(sm.description_location(text)['region'], region)
        self.assertIsNone(sm.description_location('A team from Texas visits London.'))
        self.assertEqual(sm.region_for({'address': {'country': 'USA', 'state': 'ON'}}), '??')
        self.assertEqual(sm.region_for({'address': {'country': 'CA', 'state': 'Québec'}}), 'QC')

    def test_enrichment_matches_both_teams_and_time_but_cannot_add_a_scheduled_game(self):
        event = dict(id='AHL:one', league='AHL', date='2026-10-18T19:00:00Z', name='PRO @ HER',
                     home=dict(name='Hershey Bears', aliases=['Hershey Bears'], score='2'),
                     away=dict(name='Providence Bruins', aliases=['Providence Bruins'], score='1'),
                     venue=dict(fullName='Neutral Arena', address=dict(country='Canada', state='AB')),
                     state='in', clock='2nd 10:22')
        self.assertEqual(sm.make_map([], events=[event], now=NOW)['totalGames'], 0)
        game = self.games(sm.make_map([feed()], events=[event], now=NOW))[0]
        self.assertEqual((game['region'], game['score'], game['phase']), ('AB', '1–2', 'live'))
        event['date'] = '2026-10-19T19:00:00Z'
        self.assertEqual(self.games(sm.make_map([feed()], events=[event], now=NOW))[0]['region'], 'PA')

    def test_unknown_location_stays_visible_and_stale_record_cannot_locate_another_game(self):
        c = feed(title='Unknown event', league='NCAA Volleyball')
        stale = dict(assigned_number=22000, display_name='Previous game', home_team_name='Penn State')
        game = self.games(sm.make_map([c], [stale], now=NOW))[0]
        self.assertEqual(game['region'], '??')
        self.assertEqual(len(game['feeds']), 1)

    def test_metadata_snapshot_returns_without_network_and_reuses_thirty_minute_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = sm.MetadataCache(Path(temp) / 'metadata.json', fetch=Mock())
            with patch('sports_map.threading.Thread') as thread:
                snapshot = cache.snapshot([feed(league='NFL')])
                self.assertTrue(snapshot['loading'])
                thread.return_value.start.assert_called_once()
                cache.fetch.assert_not_called()
            cache.loading = False
            with patch('sports_map.time.time', return_value=1000):
                cache.cached.update(at=500, leagues=['NFL'])
                with patch('sports_map.threading.Thread') as thread:
                    cache.snapshot([feed(league='NFL')])
                    thread.assert_not_called()

    def test_failed_metadata_refresh_preserves_previous_venues_and_saves_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'metadata.json'
            cache = sm.MetadataCache(path, fetch=Mock(side_effect=OSError('offline')))
            old = dict(id='NFL:old', league='NFL', date='2026-10-18T19:00:00Z')
            cache.cached['events'] = [old]
            cache.refresh({'NFL'}, [])
            self.assertEqual(cache.cached['events'], [old])
            self.assertTrue(cache.cached['stale'])
            self.assertFalse(cache.loading)
            self.assertEqual(sm.MetadataCache(path).cached['events'], [old])


class DailyMapCacheTests(unittest.TestCase):
    def test_cold_load_is_nonblocking_and_one_refresh_serves_concurrent_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = sm.DailyMapCache(Path(temp) / 'daily.json')
            started, release = threading.Event(), threading.Event()
            def build():
                started.set()
                release.wait(5)
                return sm.make_map([feed()], now=NOW)
            build = Mock(side_effect=build)
            try:
                first = cache.snapshot(build)
                self.assertTrue(started.wait(2))
                self.assertTrue(first['cacheLoading'])
                self.assertEqual(first['totalGames'], 0)
                for _ in range(10):
                    self.assertTrue(cache.snapshot(build)['cacheLoading'])
                self.assertEqual(build.call_count, 1)
            finally:
                release.set()
                cache.worker.join(5)
            self.assertEqual(cache.snapshot(build)['totalChannels'], 1)
            self.assertFalse(cache.snapshot(build)['cacheLoading'])

    def test_saved_map_survives_restart_and_refresh_replaces_scan_membership(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'daily.json'
            cache = sm.DailyMapCache(path)
            cache.refresh(lambda: sm.make_map([feed()], now=NOW))
            restored = sm.DailyMapCache(path)
            build = Mock(return_value=sm.make_map([feed(22001, 'b'), feed(22002, 'c')], now=NOW))
            self.assertEqual(restored.snapshot(build)['totalChannels'], 1)
            build.assert_not_called()
            restored.at -= 31
            restored.snapshot(build)
            restored.worker.join(5)
            self.assertEqual(restored.snapshot(build)['totalChannels'], 2)
            ids = {f['generated_id'] for r in restored.cached['states'] for g in r['games'] for f in g['feeds']}
            self.assertNotIn(feed()['tvg_id'], ids)

    def test_daily_rollover_revalidates_and_failure_retains_last_good_map(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = sm.DailyMapCache(Path(temp) / 'daily.json')
            cache.refresh(lambda: sm.make_map([feed()], now=NOW))
            cache.day = '2000-01-01'
            build = Mock(side_effect=OSError('offline'))
            with self.assertLogs('sports_map', level='WARNING'):
                result = cache.snapshot(build)
                cache.worker.join(5)
            self.assertTrue(result['cacheStale'])
            result = cache.snapshot(build)
            self.assertEqual(result['totalChannels'], 1)
            self.assertTrue(result['cacheRefreshFailed'])
            self.assertFalse(result['cacheLoading'])
            self.assertEqual(build.call_count, 1)

    def test_corrupt_saved_map_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'daily.json'
            path.write_text('{broken')
            self.assertIsNone(sm.DailyMapCache(path).cached)


class NativeSportsMapApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        roku_app.register_roku_app_routes(self.app)
        self.client = self.app.test_client()
        self.rows = Mock(return_value=[])
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.core = SimpleNamespace(DB_PATH=Path(temp.name) / 'test.db', sports=SimpleNamespace(generated_rows=self.rows),
                                    saved_manual_guide_channels=lambda: [], channel_key=lambda c: 'manual:a')
        patcher = patch.object(roku_app.guide, 'core', self.core)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_map_uses_existing_guide_and_keeps_private_generated_metadata_server_side(self):
        channel = feed()
        self.rows.return_value = [dict(assigned_number=22000, display_name=channel['name'], url='http://secret/password')]
        self.app.add_url_rule('/guide-fixture', 'api_guide_channels', lambda: jsonify(channels=[channel]))
        cache = Mock()
        cache.snapshot.return_value = dict(events=[], teams=[], loading=False)
        with patch.object(roku_app, 'SPORTS_MAP_CACHE', cache):
            response = self.client.get('/api/roku/sports-map')
            self.app.extensions['sports_map_daily_cache'].worker.join(timeout=5)
            response = self.client.get('/api/roku/sports-map')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.json['totalChannels'], 1)
        self.assertNotIn('secret', response.get_data(as_text=True))
        self.assertEqual(response.json['refreshSeconds'], 30)
        self.assertTrue((self.core.DB_PATH.parent / 'sports-map-daily.json').exists())
        self.rows.assert_called_once()

    def test_playback_resolves_current_slot_for_stable_identity_and_rejects_removed_identity(self):
        identity = feed()['tvg_id']
        self.rows.return_value = [dict(tvg_id=identity, assigned_number=900001)]
        with patch.object(roku_app.guide, '_resolve_guide_hls_targets', return_value=(['source'], None)) as resolve, \
             patch.object(roku_app.hls, 'start_session', return_value=SimpleNamespace(token='opaque', directory=Path('unused'), process=Mock())), \
             patch.object(roku_app.hls, 'wait_for_buffer', return_value=True):
            response = self.client.post('/api/roku/playback', json=dict(play_url='/guide/play/sports/22000', generated_id=identity))
        self.assertEqual(response.status_code, 200)
        resolve.assert_called_once_with('/guide/play/sports/900001')
        self.assertEqual(response.json['live_delay_seconds'], 4)
        self.rows.return_value = []
        with patch.object(roku_app.guide, '_resolve_guide_hls_targets') as resolve:
            self.assertEqual(self.client.post('/api/roku/playback', json=dict(play_url='/guide/play/sports/22000', generated_id=identity)).status_code, 410)
            resolve.assert_not_called()

    def test_malformed_identity_cannot_resolve_arbitrary_sources(self):
        with patch.object(roku_app.guide, '_resolve_guide_hls_targets') as resolve:
            for identity in ('http://secret', [], {}, 17):
                response = self.client.post('/api/roku/playback', json=dict(play_url='http://arbitrary/source', generated_id=identity))
                self.assertEqual(response.status_code, 400)
            resolve.assert_not_called()

    def test_native_zip_contains_separate_map_components_circle_assets_and_view_tabs(self):
        with zipfile.ZipFile(roku_app.package('http://picker:9999')) as archive:
            for item in ('SportsMap', 'SportsMapLayoutTask'):
                self.assertIn('components/' + item + '.xml', archive.namelist())
            self.assertIn('images/sports-bubble-focused.png', archive.namelist())
            main = archive.read('components/MainScene.xml').decode()
            self.assertIn('id="viewTabs"', main)
            self.assertIn('id="sportsMap"', main)
            self.assertIn('SPORTS MAP', archive.read('components/SportsMapView.brs').decode())
            self.assertNotIn(':10002', ''.join(archive.read(n).decode() for n in archive.namelist() if n.endswith('.brs')))


if __name__ == '__main__':
    unittest.main()
