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


class BrowserBridgeTests(unittest.TestCase):
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
