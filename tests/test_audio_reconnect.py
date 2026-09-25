"""Exercise Listen against a real HTTP source that closes between audio bursts."""
import shutil
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from flask import Flask

from media.ffmpeg import audio_only_mp3_args
from media import browser


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for stream recovery tests")
class AudioReconnectTests(unittest.TestCase):
    def test_rolling_playlist_advances_segments_without_restarting_worker(self):
        clip = subprocess.run([
            shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=44100", "-t", "1", "-c:a", "aac",
            "-f", "mpegts", "pipe:1",
        ], capture_output=True, check=True, timeout=10).stdout
        started = time.monotonic()
        segments = []

        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith(".m3u8"):
                    latest = int(time.monotonic() - started)
                    first = max(0, latest - 2)
                    text = f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:{first}\n"
                    text += "".join(f"#EXTINF:1,\nsegment/{n}\n" for n in range(first, latest + 1))
                    data = text.encode()
                else:
                    segments.append(int(self.path.rsplit("/", 1)[-1]))
                    data = clip
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            args = audio_only_mp3_args(f"http://127.0.0.1:{server.server_port}/live.m3u8")
            args[-1:-1] = ["-t", "4"]
            result = subprocess.run(args, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertGreater(len(result.stdout), 60000)
            self.assertGreaterEqual(len(segments), 4)
            self.assertEqual(segments, sorted(set(segments)), "Replayed an already-read live segment")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_completed_hls_window_reopens_without_ending_listener_response(self):
        clip = subprocess.run([
            shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=44100", "-t", "0.8", "-c:a", "aac",
            "-f", "mpegts", "pipe:1",
        ], capture_output=True, check=True, timeout=10).stdout
        playlist = (b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n"
                    b"#EXTINF:0.8,\nclip.ts\n#EXT-X-ENDLIST\n")

        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                data = playlist if self.path.endswith(".m3u8") else clip
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        real_popen = subprocess.Popen
        processes = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        try:
            with Flask(__name__).test_request_context("/guide/listen"), patch.object(
                browser.media_pipeline, "acquire_session", return_value="test-session"
            ), patch.object(browser.media_pipeline, "release_session") as release, patch.object(
                browser.subprocess, "Popen", side_effect=spawn
            ), patch.object(browser, "AUDIO_RECONNECT_DELAY_SECONDS", 0):
                response = browser.response_for(
                    f"http://127.0.0.1:{server.server_port}/live.m3u8", audio_only=True,
                )
                try:
                    size = 0
                    for chunk in response.response:
                        size += len(chunk)
                        if len(processes) >= 2 or size > 100000:
                            break
                    self.assertGreaterEqual(len(processes), 2)
                    self.assertGreater(size, 12000)
                finally:
                    response.close()
            release.assert_called_once_with("test-session")
            self.assertTrue(all(p.poll() is not None for p in processes))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_audio_continues_after_eof_and_truncated_http_response(self):
        clip = subprocess.run([
            shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=44100", "-t", "0.8", "-c:a", "aac",
            "-f", "mpegts", "pipe:1",
        ], capture_output=True, check=True, timeout=10).stdout
        for truncated in (False, True):
            with self.subTest(truncated=truncated):
                requests = []

                class Source(BaseHTTPRequestHandler):
                    def do_GET(self):
                        requests.append(time.monotonic())
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp2t")
                        self.send_header("Content-Length", str(len(clip) + (1000 if truncated else 0)))
                        self.end_headers()
                        try:
                            self.wfile.write(clip)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        self.close_connection = True

                    def log_message(self, *args):
                        pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    with Flask(__name__).test_request_context("/guide/listen"), patch.object(
                        browser.media_pipeline, "acquire_session", return_value="test-session"
                    ), patch.object(browser.media_pipeline, "release_session"), patch.object(
                        browser, "AUDIO_RECONNECT_DELAY_SECONDS", 0
                    ):
                        response = browser.response_for(
                            f"http://127.0.0.1:{server.server_port}/live.ts", audio_only=True,
                        )
                        size = 0
                        try:
                            for chunk in response.response:
                                size += len(chunk)
                                if size > 40000:
                                    break
                        finally:
                            response.close()
                    self.assertGreater(len(requests), 1, "FFmpeg failed to reconnect")
                    self.assertGreater(size, 40000, "MP3 ended after the first audio burst")
                    if not truncated:
                        self.assertGreaterEqual(
                            requests[1] - requests[0], 0.65,
                            "Reopened the rolling live window before playing its audio",
                        )
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=2)

    def test_finite_local_audio_still_ends(self):
        # Recovery is for HTTP live streams; finite local inputs must not loop.
        args = audio_only_mp3_args("/tmp/recording.ts")
        self.assertNotIn("-reconnect_at_eof", args)
        self.assertNotIn("-rw_timeout", args)
