import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, Response
import stream_monitor as monitor
from api.stream_monitor import register_stream_monitor_routes


class StreamMonitorTests(unittest.TestCase):
    def setUp(self):
        with monitor.LOCK:
            monitor.CLIENTS.clear()
            monitor.EVENTS.clear()
        self.app = Flask(__name__)
        register_stream_monitor_routes(self.app)
        self.private = 'https://provider.example/live/secret-user/secret-password/123.ts'

        @self.app.get('/continuous')
        def continuous():
            return monitor.attach(Response(iter([b'abc', b'def']), content_type='video/mp2t'),
                                  self.private, 'passthrough', 'MPEG-TS', 'shared')

        @self.app.get('/hls')
        def hls():
            return monitor.attach(Response(b'abcdefgh', content_type='video/mp2t'),
                                  self.private, 'direct', 'HLS', segmented=True)

        @self.app.get('/untracked')
        def untracked():
            return Response(b'logo', content_type='image/png')

        self.client = self.app.test_client()
        catalog = patch.object(monitor, 'source_details', return_value=('WGAL', 'Primary'))
        catalog.start()
        self.addCleanup(catalog.stop)

    def test_continuous_transfer_counts_bytes_and_closes_original(self):
        response = self.client.get('/continuous', buffered=False,
            environ_base={'REMOTE_ADDR': '100.80.1.2'}, headers={'User-Agent': 'VLC/3'})
        data = monitor.snapshot()
        self.assertEqual(data['observed_stream_count'], 1)
        row = data['streams'][0]
        self.assertEqual((row['client_ip'], row['client'], row['mode']), ('100.80.1.2', 'VLC', 'passthrough'))
        self.assertGreater(row['bytes_sent'], 0)
        self.assertEqual(response.data, b'abcdef')
        response.close()
        data = monitor.snapshot()
        self.assertEqual(data['observed_stream_count'], 0)
        self.assertEqual(data['events'][0]['message'], 'Stream response closed')
        serialized = json.dumps(data)
        self.assertNotIn('secret', serialized)
        self.assertNotIn('provider.example', serialized)

    def test_multiple_hls_segments_group_and_expire_without_polling_source(self):
        for _ in range(3):
            response = self.client.get('/hls', environ_base={'REMOTE_ADDR': '10.0.0.30'})
            self.assertEqual(response.data, b'abcdefgh')
            response.close()
        data = monitor.snapshot()
        self.assertEqual(data['observed_stream_count'], 1)
        self.assertEqual(data['streams'][0]['bytes_sent'], 24)
        self.assertEqual(data['streams'][0]['transport'], 'HLS')
        self.assertNotIn('secret', json.dumps(data))
        future = data['sampled_at'] + 31
        self.assertEqual(monitor.snapshot(now=future)['observed_stream_count'], 0)
        self.assertEqual(monitor.snapshot(now=future)['events'][0]['message'], 'No recent HLS requests')

    def test_forwarded_headers_and_non_media_are_not_trusted(self):
        response = self.client.get('/hls', environ_base={'REMOTE_ADDR': '10.0.0.30'},
                                   headers={'X-Forwarded-For': '1.2.3.4'})
        response.data
        response.close()
        self.client.get('/untracked').close()
        data = monitor.snapshot()
        self.assertEqual(len(data['streams']), 1)
        self.assertEqual(data['streams'][0]['client_ip'], '10.0.0.30')

    def test_rate_falls_to_zero_without_new_data(self):
        response = self.client.get('/hls')
        response.data
        response.close()
        data = monitor.snapshot()
        row = monitor.snapshot(now=data['sampled_at'] + 16)['streams'][0]
        self.assertEqual(row['mbps'], 0)
        self.assertEqual(row['state'], 'waiting')

    def test_client_rates_sum_independent_connections_with_correct_units(self):
        first = self.client.get('/continuous', buffered=False)
        second = self.client.get('/continuous', buffered=False)
        with monitor.LOCK:
            for row in monitor.CLIENTS.values():
                row['started_at'] = 100
                row['last_data'] = row['last_seen'] = 110
                row['samples'].clear()
                row['samples'].append((109, 10_000_000))
        data = monitor.snapshot(now=110)
        self.assertEqual(len(data['streams']), 2)
        self.assertEqual(len(data['clients']), 1)
        self.assertEqual(data['clients'][0]['connections'], 2)
        self.assertEqual(data['clients'][0]['bytes_per_second'], 2_000_000)
        self.assertEqual(data['streams'][0]['mbps'], 8)
        first.close()
        second.close()

    def test_close_before_first_byte_releases_underlying_iterator(self):
        closed = []
        class Source:
            def __iter__(self): return self
            def __next__(self): return b'chunk'
            def close(self): closed.append(True)
        with self.app.test_request_context('/continuous'):
            response = monitor.attach(Response(Source()), self.private, 'direct', 'HTTP')
            monitor.observe(response)
            response.close()
        self.assertTrue(closed)
        self.assertEqual(monitor.snapshot()['observed_stream_count'], 0)

    def test_monitor_failure_does_not_break_playback(self):
        with patch.object(monitor, 'source_details', side_effect=ValueError('Unavailable')):
            response = self.client.get('/hls')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b'abcdefgh')

    def test_endpoint_returns_network_and_counts_without_remote_requests(self):
        import core, media_pipeline, vpn_runtime
        from media import hls, upstream_relay
        with (patch('api.stream_monitor.load_settings', return_value=SimpleNamespace(lan_host='10.0.0.18')),
             patch.object(vpn_runtime, 'status', return_value={'app_vpn_active': True, 'network_protected': True, 'enabled': True, 'vpn_public_ip': '195.181.163.29'}),
             patch.object(vpn_runtime, 'protection_missing', return_value=False),
             patch.object(media_pipeline, 'status', return_value={'runtime': {'active_sessions': 2}}),
             patch.object(upstream_relay, 'active_count', return_value=1),
             patch.dict(hls._SESSIONS, {}, clear=True)):
            response = self.client.get('/api/streams')
            data = response.get_json()
        self.assertEqual(data['network']['route'], 'vpn')
        self.assertEqual(data['network']['lan_ip'], '10.0.0.18')
        self.assertEqual(data['network']['vpn_ip'], '195.181.163.29')
        self.assertEqual(data['ffmpeg_sessions'], 2)
        self.assertEqual(data['relay_requests'], 1)
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_hls_resources_inherit_channel_identity_without_returning_source(self):
        from media import upstream_relay
        text = upstream_relay.rewrite_hls('#EXTM3U\n#EXTINF:2,\nsegment.ts\n',
                    'https://cdn.example/redirected/media.m3u8', self.private)
        token = text.splitlines()[-1].split('/')[-1].split('.')[0]
        self.assertEqual(upstream_relay._ROOTS[token], self.private)
        self.assertNotIn('secret', text)
        self.assertNotIn('cdn.example', text)


if __name__ == '__main__':
    unittest.main()
