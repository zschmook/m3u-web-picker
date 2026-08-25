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
    @patch("media.mpegts._command", return_value=["ffmpeg"])
    @patch("media.mpegts.LiveLogoDetector.create")
    @patch("media.mpegts.subprocess.Popen")
    def test_mpegts_same_target_uses_one_process(self, popen, analyzer_create, _command, _acquire, _start):
        process = Mock(stdout=io.BytesIO())
        process.poll.return_value = None
        popen.return_value = process
        analyzer_create.return_value = Mock(frame_pattern=Path("analysis.ts"))

        with patch(
            "media.mpegts.media_pipeline.settings",
            return_value={"commercial_detection_enabled": True},
        ):
            first, first_id, _ = mpegts._subscribe(
                "http://provider/channel.ts",
                identity="manual:abc",
                profile_identity="tvg:nbc.example",
                profile_db_path=Path("profiles.db"),
            )
            second, second_id, _ = mpegts._subscribe(
                "http://provider/channel.ts",
                profile_identity="tvg:nbc.example",
                profile_db_path=Path("profiles.db"),
            )

        self.assertIs(first, second)
        self.assertEqual(popen.call_count, 1)
        with patch("media.mpegts.terminate") as terminate, patch("media.mpegts.media_pipeline.release_session") as release:
            mpegts._unsubscribe(first, first_id)
            terminate.assert_not_called()
            mpegts._unsubscribe(second, second_id)
            terminate.assert_called_once_with(process)
            release.assert_called_once_with("pipeline")

        invoked_command = _command.call_args.args
        self.assertEqual(invoked_command[2], Path("analysis.ts"))
        self.assertEqual(analyzer_create.call_args.kwargs["channel_identity"], "tvg:nbc.example")
        self.assertEqual(analyzer_create.call_args.kwargs["profile_db_path"], Path("profiles.db"))

    @patch("media.mpegts.threading.Thread.start")
    @patch("media.mpegts.media_pipeline.acquire_session", return_value="pipeline")
    @patch("media.mpegts._command", return_value=["ffmpeg"])
    @patch("media.mpegts.LiveLogoDetector.create")
    @patch("media.mpegts.subprocess.Popen")
    def test_mpegts_profile_change_restarts_ffmpeg_with_new_signature(
        self, popen, analyzer_create, _command, _acquire, _start
    ):
        process = Mock(stdout=io.BytesIO())
        process.poll.return_value = None
        popen.return_value = process
        analyzer_create.return_value = Mock(frame_pattern=Path("analysis.ts"))

        with patch(
            "media.mpegts.media_pipeline.settings",
            return_value={"commercial_detection_enabled": True},
        ):
            first, _, _ = mpegts._subscribe(
                "http://provider/channel.ts",
                identity="manual:abc",
                profile_identity="tvg:nbc.example",
                profile_db_path=Path("profiles.db"),
            )
            second, _, _ = mpegts._subscribe(
                "http://provider/channel.ts",
                identity="manual:abc",
                profile_identity="tvg:fox.example",
                profile_db_path=Path("profiles.db"),
            )

        self.assertIsNot(first, second)
        self.assertEqual(popen.call_count, 2)

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

    def test_mpegts_status_exposes_identity_not_provider_target(self):
        process = Mock()
        process.poll.return_value = None
        stream = mpegts.SharedMpegtsStream(
            "http://provider/secret-token", process, "pipeline",
            identity="sports:702", control_address="tcp://127.0.0.1:5555",
        )
        mpegts._STREAMS[stream.target] = stream

        status = mpegts.commercial_status()

        self.assertEqual(status["streams"][0]["identity"], "sports:702")
        self.assertNotIn("provider", str(status))

    def test_active_stream_profile_snapshot_prefers_matching_identity(self):
        stream_one = mpegts.SharedMpegtsStream(
            "target-one",
            Mock(),
            "pipeline-one",
            identity="manual:one",
            created_at=10.0,
            analyzer=Mock(profile_snapshot=Mock(return_value={
                "channel_identity": "tvg:one",
                "sports_generated": False,
            })),
        )
        stream_two = mpegts.SharedMpegtsStream(
            "target-two",
            Mock(),
            "pipeline-two",
            identity="manual:two",
            created_at=20.0,
            analyzer=Mock(profile_snapshot=Mock(return_value={
                "channel_identity": "tvg:two",
                "sports_generated": False,
            })),
        )
        stream_one.process.poll.return_value = None
        stream_two.process.poll.return_value = None
        mpegts._STREAMS.update({
            stream_one.target: stream_one,
            stream_two.target: stream_two,
        })

        snapshot = mpegts.active_stream_profile_snapshot("manual:two")

        self.assertEqual(snapshot["channel_identity"], "tvg:two")

    def test_recycle_streams_finishes_each_active_session(self):
        first_process = Mock()
        first_process.poll.return_value = None
        second_process = Mock()
        second_process.poll.return_value = None
        first = mpegts.SharedMpegtsStream("one", first_process, "pipeline-one")
        second = mpegts.SharedMpegtsStream("two", second_process, "pipeline-two")
        mpegts._STREAMS.update(one=first, two=second)
        with patch("media.mpegts._finish") as finish:
            self.assertEqual(mpegts.recycle_streams(), 2)
            self.assertEqual(finish.call_count, 2)

    def test_last_stream_finish_clears_automatic_detection_state(self):
        process = Mock()
        process.poll.return_value = None
        stream = mpegts.SharedMpegtsStream("one", process, "pipeline")
        mpegts._STREAMS[stream.target] = stream
        with patch("media.mpegts.terminate"), patch(
            "media.mpegts.media_pipeline.release_session"
        ), patch("commercial_detection.clear_logo_state") as clear:
            mpegts._finish(stream)
        clear.assert_called_once_with()

    @patch("media.mpegts.threading.Thread.start")
    @patch("media.mpegts.media_pipeline.acquire_session", return_value="pipeline")
    @patch("media.mpegts.media_pipeline.settings", return_value={"commercial_detection_enabled": False})
    @patch("media.mpegts._command", return_value=["ffmpeg"])
    @patch("media.mpegts.LiveLogoDetector.create")
    @patch("media.mpegts.subprocess.Popen")
    def test_new_stream_does_not_inherit_automatic_commercial(
        self, popen, analyzer_create, command, _settings, _acquire, _start
    ):
        process = Mock(stdout=io.BytesIO())
        process.poll.return_value = None
        popen.return_value = process
        analyzer_create.return_value = Mock(frame_pattern=Path("analysis.ts"))
        with patch("commercial_detection.payload", return_value={"active": True, "source": "logo"}):
            stream, _, _ = mpegts._subscribe("new-stream")
        self.assertFalse(stream.commercial_active)
        self.assertFalse(command.call_args.kwargs["commercial_active"])
        self.assertIs(stream.analyzer, analyzer_create.return_value)
        self.assertEqual(command.call_args.args[2], Path("analysis.ts"))

    def test_disabling_filtering_releases_automatic_overlay_without_stopping_analysis(self):
        process = Mock()
        process.poll.return_value = None
        analyzer = Mock()
        stream = mpegts.SharedMpegtsStream(
            "one", process, "pipeline", commercial_active=True, analyzer=analyzer
        )
        mpegts._STREAMS[stream.target] = stream
        with patch("commercial_detection.payload", return_value={"active": True, "source": "logo"}), patch(
            "media.mpegts._set_stream_commercial", return_value=(True, "")
        ) as set_stream:
            result = mpegts.apply_automatic_filtering_setting(False)
        set_stream.assert_called_once_with(stream, False)
        self.assertIs(stream.analyzer, analyzer)
        self.assertEqual(result["switched_streams"], 1)

    def test_disabling_filtering_resends_clear_when_runtime_flag_is_out_of_sync(self):
        process = Mock()
        process.poll.return_value = None
        stream = mpegts.SharedMpegtsStream(
            "one", process, "pipeline", commercial_active=False, analyzer=Mock()
        )
        mpegts._STREAMS[stream.target] = stream
        with patch("commercial_detection.payload", return_value={"active": True, "source": "logo"}), patch(
            "media.mpegts._set_stream_commercial", return_value=(True, "")
        ) as set_stream:
            result = mpegts.apply_automatic_filtering_setting(False)
        set_stream.assert_called_once_with(stream, False)
        self.assertEqual(result["eligible_streams"], 1)

    def test_filter_setting_does_not_override_manual_test(self):
        process = Mock()
        process.poll.return_value = None
        stream = mpegts.SharedMpegtsStream(
            "one", process, "pipeline", commercial_active=True, analyzer=Mock()
        )
        mpegts._STREAMS[stream.target] = stream
        with patch("commercial_detection.payload", return_value={"active": True, "source": "manual"}), patch(
            "media.mpegts._set_stream_commercial"
        ) as set_stream:
            result = mpegts.apply_automatic_filtering_setting(False)
        set_stream.assert_not_called()
        self.assertTrue(result["manual_override_active"])

    def test_commercial_overlay_uses_live_stream_dimensions(self):
        with patch("media.mpegts.live_input_args", return_value=["ffmpeg"]), patch(
            "media.mpegts.normalized_output_args", return_value=[]
        ):
            command = mpegts._command("hidden", "tcp://127.0.0.1:5555", Path("analysis.ts"))

        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("scale2ref=w=rw:h=rh", graph)
        self.assertNotIn("scale2ref=w=main_w:h=main_h", graph)
        self.assertIn("overlay@commercial_overlay=x=main_w", graph)
        self.assertIn("volume@commercial_audio=volume=1", graph)
        self.assertNotIn("streamselect", graph)
        self.assertIn("fps=2,scale=-2:360", graph)
        self.assertIn("analysis.ts", command)

    def test_disabled_detection_does_not_create_analysis_output(self):
        with patch("media.mpegts.live_input_args", return_value=["ffmpeg"]), patch(
            "media.mpegts.normalized_output_args", return_value=[]
        ):
            command = mpegts._command("hidden", "tcp://127.0.0.1:5555", None)
        graph = command[command.index("-filter_complex") + 1]
        self.assertNotIn("analysis_video", graph)
        self.assertNotIn("image2", command)


if __name__ == "__main__":
    unittest.main()
