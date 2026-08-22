import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from media import hls, mpegts


class SharedMediaSessionTests(unittest.TestCase):
    def tearDown(self):
        mpegts._STREAMS.clear()
        hls._SESSIONS.clear()
        hls._TARGETS.clear()
        hls._REFERENCES.clear()

    @patch("media.mpegts.threading.Thread.start")
    @patch("media.mpegts.media_pipeline.acquire_session", return_value="pipeline")
    @patch("media.mpegts.normalized_live_input_args", return_value=["ffmpeg"])
    @patch("media.mpegts.subprocess.Popen")
    def test_mpegts_same_target_uses_one_process(self, popen, _args, _acquire, _start):
        process = Mock(stdout=io.BytesIO())
        process.poll.return_value = None
        popen.return_value = process

        first, first_id, _ = mpegts._subscribe("http://provider/channel.ts")
        second, second_id, _ = mpegts._subscribe("http://provider/channel.ts")

        self.assertIs(first, second)
        self.assertEqual(popen.call_count, 1)
        with patch("media.mpegts.terminate") as terminate, patch("media.mpegts.media_pipeline.release_session") as release:
            mpegts._unsubscribe(first, first_id)
            terminate.assert_not_called()
            mpegts._unsubscribe(second, second_id)
            terminate.assert_called_once_with(process)
            release.assert_called_once_with("pipeline")

    def test_hls_reference_keeps_shared_process_until_last_owner_stops(self):
        process = Mock()
        process.poll.return_value = None
        session = hls.HlsSession(
            token="shared", target="http://provider/channel.ts", directory=Path("unused"),
            process=process, created_monotonic=1.0, last_access_monotonic=1.0,
            pipeline_token="pipeline",
        )
        hls._SESSIONS[session.token] = session
        hls._TARGETS[session.target] = session.token
        hls._REFERENCES[session.token] = 2

        with patch("media.hls.terminate") as terminate, patch("media.hls.media_pipeline.release_session") as release, patch("media.hls._remove_session_files"):
            self.assertTrue(hls.stop_session(session.token))
            terminate.assert_not_called()
            self.assertTrue(hls.stop_session(session.token))
            terminate.assert_called_once_with(process)
            release.assert_called_once_with("pipeline")

    def test_full_inactive_mpegts_subscriber_is_evicted(self):
        process = Mock()
        subscriber = mpegts.Subscriber(last_consumed_monotonic=10.0)
        for _ in range(subscriber.output.maxsize):
            subscriber.output.put_nowait(b"data")
        stream = mpegts.SharedMpegtsStream("target", process, "pipeline", {"viewer": subscriber})
        mpegts._STREAMS[stream.target] = stream

        abandoned = mpegts._evict_stale_subscribers(
            stream, 10.0 + mpegts.STALE_SUBSCRIBER_SECONDS
        )

        self.assertTrue(abandoned)
        self.assertEqual(stream.subscribers, {})


if __name__ == "__main__":
    unittest.main()
