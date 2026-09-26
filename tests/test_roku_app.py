import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from flask import Flask, jsonify
from api import roku_app
from media import roku_movie


class RokuAppTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        roku_app.register_roku_app_routes(self.app)
        self.client = self.app.test_client()
        roku_app.LIVE_LEASES.clear()

    def tearDown(self):
        roku_app.LIVE_LEASES.clear()

    def test_guide_window_deduplicates_and_keeps_restart_without_credentials(self):
        now = 1800000000
        from datetime import datetime, timezone
        def item(start, stop):
            return dict(title='Movie', description='Story',
                start=datetime.fromtimestamp(start, timezone.utc).isoformat(),
                stop=datetime.fromtimestamp(stop, timezone.utc).isoformat(),
                restart_url='/guide/play/restart/opaque', media_type='movie')
        current, future = item(now - 600, now + 600), item(now + 600, now + 1200)
        channel = dict(number=2500, name='Drama', play_url='/guide/play/custom/movies',
            now=current, next=future, upcoming=[current, future, item(now + 40000, now + 42000)],
            private_source='http://private-source/token', logo='/channel.svg')
        payload = roku_app.guide_payload([channel], now=now)
        row = payload['channels'][0]
        self.assertEqual(len(row['programmes']), 2)
        self.assertEqual(row['programmes'][0]['restart_url'], '/guide/play/restart/opaque')
        self.assertNotIn('private-source', json.dumps(payload))
        self.assertEqual(row['logo'], '/static/icons/guide-192.png')
        self.assertEqual(row['number'], '2500')

    def test_empty_guide_still_has_a_selectable_channel(self):
        row = roku_app.guide_payload([dict(name='Manual', play_url='/guide/play/manual/a')], now=100)['channels'][0]
        self.assertEqual(row['programmes'][0]['title'], 'No guide data')
        self.assertEqual(row['play_url'], '/guide/play/manual/a')

    def test_native_guide_reuses_existing_guide_route(self):
        self.app.add_url_rule('/test-guide', 'api_guide_channels',
            lambda: jsonify(channels=[dict(name='Same lineup', number=1000)]))
        result = self.client.get('/api/roku/guide')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['channels'][0]['name'], 'Same lineup')
        self.assertIn('no-store', result.headers['Cache-Control'])

    def test_package_contains_scene_tasks_and_original_home_icon(self):
        with zipfile.ZipFile(roku_app.package('http://tv.local:9998')) as archive:
            self.assertIn('components/MainScene.xml', archive.namelist())
            self.assertIn('components/ApiTask.xml', archive.namelist())
            self.assertIn('source/main.brs', archive.namelist())
            self.assertEqual(archive.read('images/icon.png'),
                (roku_app.REPO / 'static/icons/guide-512.png').read_bytes())
            self.assertEqual(json.loads(archive.read('server.json'))['server'], 'http://tv.local:9998')
            self.assertIn(b'mm_icon_focus_hd=pkg:/images/icon.png', archive.read('manifest'))

    def test_live_playback_uses_relay_and_returns_only_local_media(self):
        callback = Mock()
        with patch.object(roku_app.guide, '_resolve_guide_hls_targets', return_value=(['http://provider/private'], callback)), \
                patch.object(roku_app.hls, 'start_session', return_value=SimpleNamespace(token='opaque')) as start:
            response = self.client.post('/api/roku/playback', json=dict(play_url='/guide/play/manual/a'))
        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with(['http://provider/private'], on_target=callback)
        self.assertEqual(response.json['media_url'], 'http://localhost/guide/roku/opaque/stream.m3u8')
        self.assertNotIn('provider', response.get_data(as_text=True))
        self.assertIn(response.json['lease'], roku_app.LIVE_LEASES)

    def test_restart_keeps_plex_auth_on_server(self):
        source = dict(target='http://plex/private', input_headers={'X-Plex-Token': 'secret'}, title='Movie')
        with patch.object(roku_app.movie_restart, 'resolve', return_value=source), \
                patch.object(roku_movie, 'start', return_value=SimpleNamespace(token='opaque')) as start:
            response = self.client.post('/api/roku/playback', json=dict(mode='movie',
                restart_url='/guide/play/restart/' + 'a' * 24))
        start.assert_called_once_with(source)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['kind'], 'movie')
        self.assertEqual(response.json['media_url'], 'http://localhost/roku/movie/opaque/stream.m3u8')
        self.assertNotIn('secret', response.get_data(as_text=True))
        self.assertNotIn('plex', response.get_data(as_text=True))

    def test_bad_requests_cannot_start_arbitrary_movie_sources(self):
        with patch.object(roku_movie, 'start') as start:
            for data in ([], dict(mode='bad'), dict(mode='movie', restart_url='http://other/file.mp4')):
                self.assertEqual(self.client.post('/api/roku/playback', json=data).status_code, 400)
        start.assert_not_called()
        for suffix in ('stop', 'heartbeat'):
            self.assertEqual(self.client.post('/api/roku/playback/' + suffix, json=[]).status_code, 400)

    def test_start_failure_does_not_expose_private_ffmpeg_error(self):
        with patch.object(roku_app.guide, '_resolve_guide_hls_targets', return_value=(['source'], None)), \
                patch.object(roku_app.hls, 'start_session', side_effect=RuntimeError('http://private/password')):
            response = self.client.post('/api/roku/playback', json=dict(play_url='/guide/play/manual/a'))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('password', response.get_data(as_text=True))

    def test_idle_cleanup_releases_only_abandoned_native_reference(self):
        with patch.object(roku_app.time, 'monotonic', return_value=100):
            idle = roku_app.lease_live('shared')
            active = roku_app.lease_live('shared')
        with patch.object(roku_app.time, 'monotonic', return_value=290), \
                patch.object(roku_app.hls, 'touch_session', return_value=object()), \
                patch.object(roku_app.hls, 'stop_session', return_value=True) as stop:
            self.assertTrue(roku_app.update_live(dict(token='shared', lease=active)))
            roku_app.expire_live_leases()
            stop.assert_called_once_with('shared')
            self.assertNotIn(idle, roku_app.LIVE_LEASES)
            self.assertIn(active, roku_app.LIVE_LEASES)
            self.assertFalse(roku_app.update_live(dict(token='wrong', lease=active), stop=True))
            self.assertTrue(roku_app.update_live(dict(token='shared', lease=active), stop=True))
            self.assertFalse(roku_app.update_live(dict(token='shared', lease=active), stop=True))
            self.assertEqual(stop.call_count, 2)


class RokuMovieTests(unittest.TestCase):
    def tearDown(self):
        for token in list(roku_movie.SESSIONS):
            roku_movie.stop(token)

    def test_movie_hls_retains_segments_for_pause_and_starts_from_beginning(self):
        source = dict(target='http://plex/movie', input_headers={'X-Plex-Token': 'secret'})
        with patch('media.ffmpeg.executable', return_value='ffmpeg'), \
                patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'):
            args = roku_movie.command(source, Path('/tmp/movie'))
        self.assertEqual(args[args.index('-hls_playlist_type') + 1], 'event')
        self.assertEqual(args[args.index('-hls_list_size') + 1], '0')
        self.assertNotIn('delete_segments', ' '.join(args))
        self.assertNotIn('-ss', args)
        self.assertLess(args.index('-re'), args.index('-i'))
        self.assertLess(args.index('-headers'), args.index('-i'))

    def test_safe_files_and_cleanup_after_movie_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / ('a' * 24)
            directory.mkdir()
            (directory / 'stream.m3u8').write_text('#EXTM3U')
            (directory / 'segment_000000.ts').write_bytes(b'segment')
            session = roku_movie.MovieSession('a' * 24, directory, Mock(), 'pipeline', 0, io.BytesIO())
            roku_movie.SESSIONS[session.token] = session
            self.assertIsNotNone(roku_movie.media_file(session.token, 'segment_000000.ts'))
            self.assertIsNone(roku_movie.media_file(session.token, '../encoder.log'))
            self.assertIsNone(roku_movie.media_file(session.token, 'encoder.log'))
            self.assertIsNone(roku_movie.media_file('../', 'stream.m3u8'))
            with patch.object(roku_movie, 'terminate') as terminate, \
                    patch.object(roku_movie.media_pipeline, 'release_session') as release:
                self.assertTrue(roku_movie.stop(session.token))
                self.assertFalse(roku_movie.stop(session.token))
            terminate.assert_called_once_with(session.process)
            release.assert_called_once_with('pipeline')
            self.assertFalse(directory.exists())

    def test_failed_encoder_start_releases_pipeline_slot(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(roku_movie, 'ROOT', Path(temp)), \
                patch.object(roku_movie.media_pipeline, 'acquire_session', return_value='slot'), \
                patch.object(roku_movie.media_pipeline, 'release_session') as release, \
                patch.object(roku_movie, 'command', return_value=['ffmpeg']), \
                patch.object(roku_movie.subprocess, 'Popen', side_effect=OSError('missing')):
            with self.assertRaises(OSError):
                roku_movie.start(dict())
            release.assert_called_once_with('slot')
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
