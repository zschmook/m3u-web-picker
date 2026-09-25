import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from flask import Flask
from api import commercials


class CommercialChannelTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        commercials.register_commercial_routes(self.app)

    def test_playlist_and_head_never_start_ffmpeg(self):
        with patch.object(commercials, 'clip_paths', return_value=[Path('/clips/ad_0001.mp4')]), patch.object(commercials.subprocess, 'Popen') as start:
            client = self.app.test_client()
            response = client.get('/playlist/commercials.m3u', base_url='http://example:9998')
            self.assertIn('tvg-chno="0.01"', response.text)
            self.assertIn('http://example:9998/stream/temp/commercials.ts', response.text)
            self.assertEqual(client.head('/stream/temp/commercials.ts').status_code, 200)
            start.assert_not_called()

    def test_stream_starts_lazily_and_stops_on_close(self):
        process = Mock()
        process.stderr = io.BytesIO()
        process.stdout.read.return_value = b'G' * 188
        with patch.object(commercials, 'DebugSession'), patch.object(commercials.subprocess, 'Popen', return_value=process) as start, patch.object(commercials, 'terminate') as stop:
            generator = commercials.stream_chunks([Path('/clips/ad_0001.mp4')])
            start.assert_not_called()
            self.assertEqual(len(next(generator)), 188)
            start.assert_called_once()
            generator.close()
            stop.assert_called_once_with(process)
            process.stdout.close.assert_called_once()

    def test_manifest_excludes_outro_and_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'M3U_COMMERCIALS_DIR': directory}):
            root = Path(directory)
            for name in ['ad_0001.mp4', 'non_ad_outro.mp4']:
                (root/name).touch()
            (root/'manifest.json').write_text(json.dumps({'clips': [
                {'clip':'ad_0001.mp4','include_in_ad_pool':True},
                {'clip':'non_ad_outro.mp4','include_in_ad_pool':False},
                {'clip':'../ad_0001.mp4','include_in_ad_pool':True},
            ]}))
            self.assertEqual(commercials.clip_paths(), [(root/'ad_0001.mp4').resolve()])

    def test_empty_library_returns_unavailable(self):
        with patch.object(commercials, 'clip_paths', return_value=[]):
            self.assertEqual(self.app.test_client().get('/stream/temp/commercials.ts').status_code, 503)

    def test_guide_lists_channel_first_without_starting_playback(self):
        from api import guide
        guide.register_guide_routes(self.app)
        with patch.object(commercials, 'clip_paths', return_value=[Path('/clips/ad_0001.mp4')]), patch.object(guide.core, 'curated_channels_for_guide', return_value=[]), patch.object(guide.sports, 'get_settings', return_value={}), patch.object(guide, 'enrich_guide_channels', return_value=([], {})), patch.object(commercials.subprocess, 'Popen') as start:
            row = self.app.test_client().get('/api/guide/channels').json['channels'][0]
            self.assertEqual(row['number'], '0.01')
            self.assertEqual(row['play_url'], commercials.PLAY_URL)
            self.assertEqual(guide._resolve_guide_play_target(row['play_url']), commercials.local_stream_url())
            start.assert_not_called()

    def test_play_uses_browser_compatible_output(self):
        with patch('media.browser.response_for', return_value='browser video') as play:
            self.assertEqual(self.app.test_client().get(commercials.PLAY_URL).status_code, 200)
            play.assert_called_once_with(commercials.local_stream_url())

    def test_debug_tracks_filenames_boundaries_and_interruption(self):
        from media import commercials_debug as debug
        with debug.LOCK:
            debug.SESSIONS.clear()
            debug.PLAYS.clear()
        tracker = debug.DebugSession([Path('ad_0001.mp4'),Path('ad_0002.mp4')], {'ad_0001.mp4':15,'ad_0002.mp4':30})
        tracker.advance(19,1234)
        tracker.close(0)
        state = debug.snapshot()
        self.assertEqual(state['plays'][0]['filename'],'ad_0002.mp4')
        self.assertEqual(state['plays'][0]['streamed_seconds'],4)
        self.assertEqual(state['plays'][0]['status'],'interrupted')
        self.assertEqual(state['plays'][1]['status'],'completed')
        self.assertIsNotNone(state['plays'][0]['stopped_at'])
        self.assertEqual(state['sessions'][0]['output_bytes'],1234)

    def test_debug_tracks_loop_cycle_and_stats_do_not_start_playback(self):
        from media import commercials_debug as debug
        tracker = debug.DebugSession([Path('ad_0001.mp4')], {'ad_0001.mp4':15})
        tracker.advance(31)
        self.assertEqual(tracker.play['cycle'],3)
        self.assertEqual(tracker.play['streamed_seconds'],1)
        tracker.close()
        with patch.object(commercials.subprocess, 'Popen') as start:
            self.assertEqual(self.app.test_client().get('/api/commercials/stats').status_code,200)
            start.assert_not_called()

if __name__ == '__main__':
    unittest.main()
