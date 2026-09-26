import io
import threading
import unittest
from unittest.mock import patch

from flask import Flask

from media import browser


class _BlockingStdout:
    def __init__(self, stopped: threading.Event):
        self.stopped = stopped

    def read(self, _size):
        self.stopped.wait(2.0)
        return b""

    def close(self):
        pass


class _Process:
    def __init__(self):
        self.stopped = threading.Event()
        self.stdout = _BlockingStdout(self.stopped)
        self.stderr = None


class BrowserBridgeTests(unittest.TestCase):
    def test_video_disconnect_releases_worker_while_source_read_is_blocked(self):
        app = Flask(__name__)
        process = _Process()
        disconnected = threading.Event()
        released = threading.Event()
        with app.test_request_context("/guide/play", environ_overrides={
            "waitress.client_disconnected": disconnected.is_set,
        }), patch.object(browser.media_pipeline, "acquire_session", return_value="token"), patch.object(
            browser.media_pipeline, "release_session", side_effect=lambda _: released.set()
        ) as release, patch.object(browser.subprocess, "Popen", return_value=process), patch.object(
            browser, "normalized_live_input_args", return_value=["ffmpeg", "pipe:1"]
        ), patch.object(browser, "terminate", side_effect=lambda _: process.stopped.set()) as terminate:
            response = browser.response_for("http://source.test/live")
            disconnected.set()
            self.assertTrue(released.wait(2), "Disconnected viewer kept its video session")
            response.close()
        terminate.assert_called_once_with(process)
        release.assert_called_once_with("token")

    def test_audio_disconnect_releases_worker_while_source_read_is_blocked(self):
        app = Flask(__name__)
        process = _Process()
        disconnected = threading.Event()
        released = threading.Event()
        with app.test_request_context("/guide/listen", environ_overrides={
            "waitress.client_disconnected": disconnected.is_set,
        }), patch.object(browser.media_pipeline, "acquire_session", return_value="token"), patch.object(
            browser.media_pipeline, "release_session", side_effect=lambda _: released.set()
        ) as release, patch.object(browser.subprocess, "Popen", return_value=process), patch.object(
            browser, "audio_only_mp3_args", return_value=["ffmpeg", "pipe:1"]
        ), patch.object(browser, "terminate", side_effect=lambda _: process.stopped.set()) as terminate:
            response = browser.response_for("http://source.test/live", audio_only=True)
            disconnected.set()
            self.assertTrue(released.wait(2), "Disconnected listener kept its audio session")
            response.close()
        terminate.assert_called_once_with(process)
        release.assert_called_once_with("token")

    def test_source_ending_after_audio_started_is_reported(self):
        app = Flask(__name__)
        processes = [_Process() for _ in range(4)]
        for index, process in enumerate(processes):
            process.stdout = io.BytesIO(b"audio" if index == 0 else b"")
        with app.test_request_context("/guide/listen"), patch.object(
            browser.media_pipeline, "acquire_session", return_value="token"
        ), patch.object(browser.media_pipeline, "release_session"), patch.object(
            browser.media_pipeline, "record_output_error"
        ) as record, patch.object(browser.subprocess, "Popen", side_effect=processes), patch.object(
            browser, "audio_only_mp3_args", return_value=["ffmpeg", "pipe:1"]
        ), patch.object(browser, "terminate"), patch.object(browser, "AUDIO_RECONNECT_DELAY_SECONDS", 0):
            response = browser.response_for("http://source.test/live", audio_only=True)
            self.assertEqual(b"".join(response.response), b"audio")
            response.close()
        record.assert_called_once_with("browser-audio", "The live audio source ended after playback started.")

    def test_source_ending_after_video_started_is_reported(self):
        app = Flask(__name__)
        process = _Process()
        process.stdout = io.BytesIO(b"video")
        with app.test_request_context("/guide/play"), patch.object(
            browser.media_pipeline, "acquire_session", return_value="token"
        ), patch.object(browser.media_pipeline, "release_session"), patch.object(
            browser.media_pipeline, "record_output_error"
        ) as record, patch.object(browser.subprocess, "Popen", return_value=process), patch.object(
            browser, "normalized_live_input_args", return_value=["ffmpeg", "pipe:1"]
        ), patch.object(browser, "terminate"):
            response = browser.response_for("http://source.test/live")
            self.assertEqual(b"".join(response.response), b"video")
            response.close()
        record.assert_called_once_with("browser", "The live video source ended after playback started.")

    def test_audio_reopens_source_in_same_response_and_releases_all_workers(self):
        app = Flask(__name__)
        processes = [_Process(), _Process()]
        for process, data in zip(processes, (b"first", b"second")):
            process.stdout = io.BytesIO(data)
        with app.test_request_context("/guide/listen"), patch.object(
            browser.media_pipeline, "acquire_session", return_value="token"
        ) as acquire, patch.object(browser.media_pipeline, "release_session") as release, patch.object(
            browser.subprocess, "Popen", side_effect=processes
        ) as spawn, patch.object(browser, "audio_only_mp3_args", return_value=["ffmpeg", "pipe:1"]), patch.object(
            browser, "terminate"
        ) as terminate, patch.object(browser, "AUDIO_RECONNECT_DELAY_SECONDS", 0):
            response = browser.response_for("http://source.test/live", audio_only=True)
            iterator = iter(response.response)
            self.assertEqual(next(iterator), b"first")
            self.assertEqual(next(iterator), b"second")
            response.close()
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(terminate.call_count, 2)
        acquire.assert_called_once()
        release.assert_called_once_with("token")

    def test_closing_unstarted_response_terminates_process_and_releases_session(self):
        app = Flask(__name__)
        process = _Process()

        with app.test_request_context("/guide/listen"), patch.object(
            browser.media_pipeline, "acquire_session", return_value="token"
        ), patch.object(
            browser.media_pipeline, "release_session"
        ) as release_session, patch.object(
            browser.subprocess, "Popen", return_value=process
        ), patch.object(
            browser, "normalized_live_input_args", return_value=["ffmpeg", "pipe:1"]
        ), patch.object(
            browser, "terminate", side_effect=lambda _process: process.stopped.set()
        ) as terminate:
            response = browser.response_for("/tmp/live/stream.m3u8")
            response.close()

        terminate.assert_called_once_with(process)
        release_session.assert_called_once_with("token")

    def test_termination_failure_still_releases_session(self):
        app = Flask(__name__)
        process = _Process()

        with app.test_request_context("/guide/listen"), patch.object(
            browser.media_pipeline, "acquire_session", return_value="token"
        ), patch.object(
            browser.media_pipeline, "release_session"
        ) as release_session, patch.object(
            browser.subprocess, "Popen", return_value=process
        ), patch.object(
            browser, "normalized_live_input_args", return_value=["ffmpeg", "pipe:1"]
        ), patch.object(
            browser, "terminate", side_effect=OSError("process access denied")
        ):
            response = browser.response_for("/tmp/live/stream.m3u8")
            with self.assertRaises(OSError):
                response.close()

        release_session.assert_called_once_with("token")


if __name__ == "__main__":
    unittest.main()
