import io
import tempfile
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

    def test_hls_command_appends_with_discontinuity_and_new_segment_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "segment_000003.ts").write_bytes(b"old")
            with patch("media.hls.normalized_live_input_args", return_value=["ffmpeg", "-i", "source"]):
                command = hls._hls_command("source", root)

        flags = command[command.index("-hls_flags") + 1]
        self.assertIn("append_list", flags)
        self.assertIn("discont_start", flags)
        self.assertEqual(command[command.index("-start_number") + 1], "4")

    def test_dead_hls_session_moves_to_next_candidate_without_changing_token(self):
        dead = Mock()
        dead.poll.return_value = 1
        live = Mock()
        live.poll.return_value = None
        session = hls.HlsSession(
            token="stable", target="primary", directory=Path("unused"),
            process=dead, created_monotonic=1.0, last_access_monotonic=1.0,
            pipeline_token="pipeline", targets=("primary", "fallback"),
        )
        hls._SESSIONS[session.token] = session
        hls._TARGETS.update({target: session.token for target in session.targets})
        hls._REFERENCES[session.token] = 1

        def recover(item, index, _timeout):
            self.assertIs(item, session)
            self.assertEqual(index, 1)
            item.process = live
            item.target = item.targets[index]
            item.target_index = index
            return True

        with patch("media.hls._record_failure"), patch("media.hls._try_target", side_effect=recover):
            recovered = hls.get_session("stable")

        self.assertIs(recovered, session)
        self.assertEqual(recovered.token, "stable")
        self.assertEqual(recovered.target, "fallback")
        self.assertEqual(recovered.recovery_count, 1)

    def test_hls_candidate_sets_share_one_session(self):
        live = Mock()
        live.poll.return_value = None

        def ready(session, index, _timeout):
            session.process = live
            session.target = session.targets[index]
            session.target_index = index
            return True

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(hls, "HLS_ROOT", Path(directory)), \
                patch("media.hls._try_target", side_effect=ready) as attempt, \
                patch("media.hls.media_pipeline.acquire_session", return_value="pipeline") as acquire:
            first = hls.start_session(["primary", "fallback"])
            second = hls.start_session(["fallback", "primary"])

        self.assertIs(first, second)
        self.assertEqual(attempt.call_count, 1)
        acquire.assert_called_once_with("hls")
        self.assertEqual(hls._REFERENCES[first.token], 2)

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
