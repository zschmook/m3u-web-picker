import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from media import hls
from media.live_read_ahead import BufferedHlsProcess, MAX_AHEAD_SECONDS, STARTUP_SECONDS, publication_delay


class ReadAheadBudgetTests(unittest.TestCase):
    def test_failed_producer_start_finishes_muxer_even_when_termination_times_out(self):
        muxer = Mock()
        muxer.poll.return_value = None
        muxer.wait.side_effect = [subprocess.TimeoutExpired('synthetic', 2), 0]
        with patch('media.live_read_ahead.subprocess.Popen', return_value=muxer), \
                patch('media.live_read_ahead.BufferedSegment', side_effect=OSError('synthetic startup failure')):
            with self.assertRaisesRegex(OSError, 'synthetic startup failure'):
                BufferedHlsProcess([], [], Mock())
        muxer.terminate.assert_called_once()
        muxer.kill.assert_called_once()
        self.assertEqual(muxer.wait.call_count, 2)
        muxer.stdin.close.assert_called_once()

    def test_poll_never_joins_producer_on_media_request_thread(self):
        process = BufferedHlsProcess.__new__(BufferedHlsProcess)
        process.muxer = Mock()
        process.muxer.poll.return_value = 1
        process.cancelled = threading.Event()
        process.close_source = Mock(side_effect=AssertionError('Cleanup ran on media request thread'))
        self.assertEqual(process.poll(), 1)
        self.assertTrue(process.cancelled.is_set())
        process.close_source.assert_not_called()

    def test_quick_start_growth_and_final_bound(self):
        self.assertEqual(publication_delay(STARTUP_SECONDS, 0), 0)
        self.assertGreater(publication_delay(STARTUP_SECONDS + 1, 0), 0)
        # Thirty seconds of viewing permits twelve seconds of headroom.
        self.assertEqual(publication_delay(42, 30), 0)
        self.assertGreater(publication_delay(43, 30), 0)
        # A long-running file cannot outrun the eighty-second rolling playlist.
        self.assertEqual(publication_delay(300 + MAX_AHEAD_SECONDS, 300), 0)
        self.assertAlmostEqual(publication_delay(301 + MAX_AHEAD_SECONDS, 300), 1)


@unittest.skipUnless(shutil.which('ffmpeg'), 'Requires FFmpeg')
class LiveReadAheadTests(unittest.TestCase):
    def make_source(self, path, seconds, color='black', offset=0):
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', f'color=c={color}:s=64x36:r=20:d={seconds}',
            '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={seconds}',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-bf', '0', '-g', '40', '-c:a', 'aac', '-output_ts_offset', str(offset),
            '-f', 'segment', '-segment_time', '2', '-segment_format', 'mpegts',
            '-reset_timestamps', '0', str(path)], capture_output=True, check=True, timeout=15)

    def test_starts_before_full_buffer_then_grows_with_bounded_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_source(root / 'source_%03d.ts', 90)
            source = root / 'source.ts'
            source.write_bytes(b''.join(p.read_bytes() for p in sorted(root.glob('source_*.ts'))))
            started = time.monotonic()
            with patch.object(hls, 'HLS_ROOT', root / 'sessions'), \
                    patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'), \
                    patch('media.hls.media_pipeline.acquire_session', return_value='slot'):
                session = hls.start_session(str(source), read_ahead=True)
            process = session.process
            try:
                self.assertLess(time.monotonic()-started, 5, 'Playback must not wait for forty-five seconds.')
                self.assertTrue(hls.wait_for_buffer(session.directory, process, 6, timeout=5))
                self.assertLess(hls.buffered_seconds(session.directory), 12)
                initial_ahead = hls.buffered_seconds(session.directory) - (time.monotonic()-started)
                # At .2 seconds per second, allow five seconds of growth: a
                # completed HLS segment and one small TS block can each lag.
                time.sleep(25)
                elapsed = time.monotonic()-started
                published = hls.buffered_seconds(session.directory)
                self.assertGreater(published-elapsed, initial_ahead+1,
                    'Read-ahead should build after playback starts.')
                self.assertLessEqual(published-elapsed, STARTUP_SECONDS+elapsed*.2+2)
                self.assertIsNone(process.poll())
                self.assertLessEqual(process.producer.output.qsize(), process.producer.output.maxsize)
            finally:
                with patch('media.hls.media_pipeline.release_session'):
                    hls.stop_session(session.token)
            self.assertFalse(process.feeder.is_alive())
            self.assertFalse(process.producer.reader.is_alive())
            self.assertFalse(process.producer.errors.is_alive())

    def test_true_source_clock_reset_recovers_to_fresh_content_with_same_token(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_source(root / 'old_%03d.ts', 8, offset=120)
            self.make_source(root / 'fresh_%03d.ts', 30, color='white')
            old = [p.read_bytes() for p in sorted(root.glob('old_*.ts'))]
            fresh = [p.read_bytes() for p in sorted(root.glob('fresh_*.ts'))]
            cancelled = threading.Event()
            requests = []
            class Source(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    requests.append(time.monotonic())
                    self.send_response(200)
                    self.send_header('Content-Type', 'video/mp2t')
                    self.end_headers()
                    chunks = old+fresh if len(requests)==1 else fresh
                    try:
                        for chunk in chunks:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            if cancelled.wait(2):
                                break
                    except (OSError, BrokenPipeError):
                        pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Source)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            session = None
            try:
                with patch.object(hls, 'HLS_ROOT', root / 'sessions'), \
                        patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'), \
                        patch('media.hls.media_pipeline.acquire_session', return_value='slot'), \
                        patch.object(hls, 'OUTPUT_STALL_SECONDS', 3):
                    session = hls.start_session(f'http://127.0.0.1:{server.server_port}/live.ts', read_ahead=True)
                    token = session.token
                    original_segments = set(session.directory.glob('segment_*.ts'))
                    deadline = time.monotonic()+30
                    while session.recovery_count==0 and time.monotonic()<deadline:
                        self.assertIs(hls.get_session(token), session)
                        time.sleep(.1)
                    self.assertGreater(session.recovery_count, 0, 'A new clock must not be discarded forever.')
                    self.assertEqual(session.token, token)
                    self.assertGreaterEqual(len(requests), 2)
                    new_segments = sorted(set(session.directory.glob('segment_*.ts'))-original_segments)
                    self.assertGreaterEqual(len(new_segments), 2)
                    self.assertIn('#EXT-X-DISCONTINUITY', (session.directory/'stream.m3u8').read_text())
                    latest = new_segments[-1]
                    pixels = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(latest),
                        '-frames:v', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'],
                        capture_output=True, check=True, timeout=10).stdout
                    self.assertGreater(sum(pixels)/len(pixels), 180, 'Recovered media must contain fresh white footage.')
            finally:
                if session is not None:
                    with patch('media.hls.media_pipeline.release_session'):
                        hls.stop_session(session.token)
                cancelled.set()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_buffered_encoder_discards_repeated_source_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_source(root/'source_%03d.ts', 8, color='white')
            original = b''.join(p.read_bytes() for p in sorted(root.glob('source_*.ts')))
            source = root/'repeated.ts'
            source.write_bytes(original*2)
            with patch.object(hls, 'HLS_ROOT', root/'sessions'), \
                    patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'), \
                    patch('media.hls.media_pipeline.acquire_session', return_value='slot'):
                session = hls.start_session(str(source), read_ahead=True)
            try:
                self.assertEqual(session.process.wait(timeout=10), 0)
                combined = root/'output.ts'
                combined.write_bytes(b''.join(p.read_bytes() for p in sorted(session.directory.glob('segment_*.ts'))))
                for track, frame_bytes, units_per_second, output_format, extra in (
                        ('0:v:0', 64*36, 20, 'rawvideo', ['-pix_fmt', 'gray']),
                        ('0:a:0', 2, 48000, 's16le', ['-ac', '1', '-ar', '48000'])):
                    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(combined),
                        '-map', track, *extra, '-f', output_format, 'pipe:1'],
                        capture_output=True, check=True, timeout=10).stdout
                    self.assertAlmostEqual(len(decoded)/frame_bytes/units_per_second, 8, delta=.2,
                        msg='Growing the buffer must not replay older source packets.')
            finally:
                with patch('media.hls.media_pipeline.release_session'):
                    hls.stop_session(session.token)


if __name__ == '__main__':
    unittest.main()
