import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from media import hls, mpegts


class SharedMediaSessionTests(unittest.TestCase):
    def test_alive_hls_encoder_with_stalled_output_recovers_without_new_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stream.m3u8").write_text("#EXTM3U\n")
            process = Mock()
            process.poll.return_value = None
            session = hls.HlsSession(token="stalled", target="source", directory=root,
                process=process, created_monotonic=1, last_access_monotonic=1,
                pipeline_token="pipeline", targets=("source",))
            hls._SESSIONS[session.token] = session
            with patch("media.hls.time.monotonic", return_value=100):
                self.assertFalse(hls._playlist_stalled(session))
            replacement = Mock()
            replacement.poll.return_value = None
            def recover(item, index, timeout):
                item.process = replacement
                (root / "stream.m3u8").write_text("#EXTM3U\n#EXTINF:2,\nsegment_000004.ts\n")
                return True
            with patch("media.hls.time.monotonic", return_value=116), \
                    patch("media.hls.terminate") as terminate, \
                    patch("media.hls._record_failure"), \
                    patch("media.hls._try_target", side_effect=recover):
                self.assertIs(hls.get_session("stalled"), session)
                session.recovery_worker.join(timeout=1)
                self.assertFalse(session.recovery_worker.is_alive())
                terminate.assert_called_once_with(process)
            self.assertIs(session.process, replacement)
            self.assertEqual(session.recovery_count, 1)

    def test_progressing_playlist_does_not_restart_a_live_encoder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            playlist = root / "stream.m3u8"
            playlist.write_text("#EXTM3U\n")
            process = Mock()
            process.poll.return_value = None
            session = hls.HlsSession(token="progressing", target="source", directory=root,
                process=process, created_monotonic=1, last_access_monotonic=1,
                pipeline_token="pipeline")
            with patch("media.hls.time.monotonic", return_value=100):
                self.assertFalse(hls._playlist_stalled(session))
            playlist.write_text("#EXTM3U\n#EXTINF:2,\nsegment_000005.ts\n")
            # Fast writes can share a Windows filesystem timestamp. Model the
            # later publication explicitly instead of relying on wall time.
            updated = session.playlist_version + 1_000_000_000
            os.utime(playlist, ns=(updated, updated))
            with patch("media.hls.time.monotonic", return_value=116):
                self.assertFalse(hls._playlist_stalled(session))

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
            session.recovery_worker.join(timeout=1)
            self.assertFalse(session.recovery_worker.is_alive())

        self.assertIs(recovered, session)
        self.assertEqual(recovered.token, "stable")
        self.assertEqual(recovered.target, "fallback")
        self.assertEqual(recovered.recovery_count, 1)

    def test_retained_downloads_and_lease_do_not_wait_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'stream.m3u8').write_text('#EXTM3U\n#EXTINF:2,\nsegment_000000.ts\n')
            segment = root/'segment_000000.ts'
            segment.write_bytes(b'already published footage')
            dead = Mock()
            dead.poll.return_value = 1
            live = Mock()
            live.poll.return_value = None
            session = hls.HlsSession('nonblocking', 'source', root, dead, 1, 1, 'pipeline', targets=('source',))
            hls._SESSIONS[session.token] = session
            entered, release = threading.Event(), threading.Event()
            def recover(item, index, timeout):
                entered.set()
                release.wait(3)
                item.process = live
                return True
            with patch('media.hls._record_failure'), patch('media.hls._try_target', side_effect=recover) as attempt:
                try:
                    self.assertIs(hls.get_session(session.token), session)
                    self.assertTrue(entered.wait(1))
                    started = time.monotonic()
                    for _ in range(20):
                        self.assertEqual(hls.safe_media_file(session.token, segment.name), segment)
                        self.assertEqual(hls.safe_media_file(session.token, 'stream.m3u8'), root/'receiver.m3u8')
                        self.assertIs(hls.touch_session(session.token), session)
                    self.assertLess(time.monotonic()-started, .5,
                        'Playable media must remain immediately available while source recovery is blocked.')
                    attempt.assert_called_once()
                    self.assertEqual(segment.read_bytes(), b'already published footage')
                finally:
                    release.set()
                    session.recovery_worker.join(timeout=2)
            self.assertFalse(session.recovery_worker.is_alive())

    def test_failed_recovery_retains_media_and_retries_until_source_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segment = root/'segment_000000.ts'
            segment.write_bytes(b'buffer survives outage')
            dead = Mock()
            dead.poll.return_value = 1
            live = Mock()
            live.poll.return_value = None
            session = hls.HlsSession('retry', 'source', root, dead, 1, 1, 'pipeline', targets=('source',))
            hls._SESSIONS[session.token] = session
            first_failed = threading.Event()
            attempts = []
            def recover(item, index, timeout):
                attempts.append(index)
                if len(attempts)==1:
                    first_failed.set()
                    return False
                item.process = live
                return True
            with patch('media.hls._record_failure'), patch('media.hls._try_target', side_effect=recover):
                try:
                    self.assertIs(hls.get_session(session.token), session)
                    self.assertTrue(first_failed.wait(1))
                    self.assertEqual(hls.safe_media_file(session.token, segment.name), segment)
                    self.assertEqual(segment.read_bytes(), b'buffer survives outage')
                    session.recovery_worker.join(timeout=3)
                    self.assertFalse(session.recovery_worker.is_alive())
                    self.assertEqual(attempts, [0, 0])
                    self.assertEqual(session.recovery_count, 1)
                finally:
                    session.recovery_cancelled.set()
                    session.recovery_worker.join(timeout=1)

    def test_receiver_manifest_keeps_encoder_input_untouched_and_last_valid_view(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-DISCONTINUITY\n#EXTINF:2,\nsegment_000000.ts\n'
            source = root/'stream.m3u8'
            source.write_text(raw)
            process = Mock()
            process.poll.return_value = None
            session = hls.HlsSession('manifest', 'source', root, process, 1, 1, 'pipeline')
            hls._SESSIONS[session.token] = session
            receiver = hls.safe_media_file(session.token, 'stream.m3u8')
            self.assertEqual(receiver, root/'receiver.m3u8')
            first = receiver.read_text()
            self.assertIn('#EXT-X-DISCONTINUITY-SEQUENCE:1\n', first)
            self.assertEqual(source.read_text(), raw)
            source.write_text('#EXTM3U\n')
            self.assertEqual(hls.safe_media_file(session.token, 'stream.m3u8').read_text(), first)
            source.write_text('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:invalid\n#EXTINF:2,\nsegment_000000.ts\n')
            self.assertEqual(hls.safe_media_file(session.token, 'stream.m3u8').read_text(), first)
            self.assertIsNone(hls.safe_media_file(session.token, 'receiver.m3u8'))
            self.assertIsNone(hls.safe_media_file(session.token, '../stream.m3u8'))

    def test_stopping_during_recovery_cancels_worker_before_removing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'session'
            root.mkdir()
            (root/'segment_000000.ts').write_bytes(b'buffer')
            dead = Mock()
            dead.poll.return_value = 1
            session = hls.HlsSession('cancel', 'source', root, dead, 1, 1, 'pipeline', targets=('source',))
            hls._SESSIONS[session.token] = session
            entered = threading.Event()
            def recover(item, index, timeout):
                entered.set()
                item.recovery_cancelled.wait(3)
                return False
            with patch('media.hls._record_failure'), patch('media.hls._try_target', side_effect=recover), \
                    patch('media.hls.terminate'), patch('media.hls.media_pipeline.release_session') as release:
                self.assertIs(hls.get_session(session.token), session)
                self.assertTrue(entered.wait(1))
                self.assertTrue(hls.stop_session(session.token))
                session.recovery_worker.join(timeout=1)
                self.assertFalse(session.recovery_worker.is_alive())
                release.assert_called_once_with('pipeline')
            self.assertFalse(root.exists())
            self.assertIsNone(hls.get_session(session.token))

    def test_buffer_counts_only_completed_segments_and_waits_for_more(self):
        import threading
        import time
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'segment_000000.ts').write_bytes(b'media')
            (root / 'stream.m3u8').write_text('#EXTM3U\n#EXTINF:2.0,\nsegment_000000.ts\n#EXTINF:2.0,\nsegment_000001.ts\n')
            self.assertEqual(hls.buffered_seconds(root), 2)
            process = Mock()
            process.poll.return_value = None
            self.assertFalse(hls.wait_for_buffer(root, process, 4, timeout=0))
            def publish():
                time.sleep(.03)
                (root / 'segment_000001.ts').write_bytes(b'more media')
            producer = threading.Thread(target=publish)
            producer.start()
            self.assertTrue(hls.wait_for_buffer(root, process, 4, timeout=1))
            producer.join()
            self.assertEqual(hls.buffered_seconds(root), 4)

    def test_buffer_wait_does_not_hang_on_encoder_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Mock()
            process.poll.return_value = 1
            self.assertFalse(hls.wait_for_buffer(Path(directory), process, 32))

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

    def test_low_latency_session_is_separate_from_phased_read_ahead(self):
        live = Mock()
        live.poll.return_value = None
        def ready(session, index, timeout):
            session.process = live
            return True
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(hls, 'HLS_ROOT', Path(directory)), \
                patch('media.hls._try_target', side_effect=ready) as attempt, \
                patch('media.hls.media_pipeline.acquire_session', return_value='pipeline'):
            sports = hls.start_session('same source')
            buffered = hls.start_session('same source', read_ahead=True)
            again = hls.start_session('same source', read_ahead=True)
        self.assertIsNot(sports, buffered)
        self.assertIs(buffered, again)
        self.assertEqual(attempt.call_count, 2)
        self.assertFalse(sports.read_ahead)
        self.assertTrue(buffered.read_ahead)

    def test_each_movie_viewer_owns_private_history_separate_from_rolling_modes(self):
        live = Mock()
        live.poll.return_value = None
        def ready(session, index, timeout):
            session.process = live
            return True
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(hls, 'HLS_ROOT', Path(directory)), \
                patch('media.hls._try_target', side_effect=ready) as attempt, \
                patch('media.hls.media_pipeline.acquire_session', return_value='pipeline'):
            sports = hls.start_session('same source')
            rolling = hls.start_session('same source', read_ahead=True)
            movie = hls.start_session('same source', read_ahead=True, retain_history=True)
            second_movie = hls.start_session('same source', read_ahead=True, retain_history=True)
            self.assertIsNot(movie, second_movie)
            self.assertEqual(len({item.token for item in (sports, rolling, movie, second_movie)}), 4)
            self.assertEqual(attempt.call_count, 4)
            self.assertEqual(hls._REFERENCES[movie.token], 1)
            self.assertEqual(hls._REFERENCES[second_movie.token], 1)
            self.assertTrue(movie.retain_history)
            self.assertFalse(rolling.retain_history)
            with patch('media.hls.terminate'), patch('media.hls.media_pipeline.release_session'):
                self.assertTrue(hls.stop_session(movie.token))
            self.assertNotIn(movie.token, hls._SESSIONS)
            self.assertIn(second_movie.token, hls._SESSIONS)
            self.assertNotIn(hls._target_key('same source', True, True), hls._TARGETS)
            self.assertEqual(hls._TARGETS[hls._target_key('same source', True)], rolling.token)
            self.assertEqual(hls._TARGETS[hls._target_key('same source', False)], sports.token)

    def test_new_channel_does_not_evict_active_paused_movie_history(self):
        live = Mock()
        live.poll.return_value = None
        def ready(session, index, timeout):
            session.process = live
            return True
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(hls, 'HLS_ROOT', Path(directory)), \
                patch('media.hls._try_target', side_effect=ready), \
                patch('media.hls.media_pipeline.acquire_session', return_value='pipeline'), \
                patch('media.hls.stop_session') as stop:
            for index in range(4):
                token = str(index)
                hls._SESSIONS[token] = hls.HlsSession(token, token, Path(directory)/token,
                    live, index + 1, 100, 'pipeline', retain_history=index == 0)
            hls.start_session('another source')
            stop.assert_not_called()
            self.assertIn('0', hls._SESSIONS)

    def test_retained_hls_uses_event_history_and_preserves_timestamp_recovery_flags(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'):
            rolling = hls._hls_command('source', Path(directory))
            retained = hls._hls_command('source', Path(directory), retain_history=True)
        self.assertEqual(rolling[rolling.index('-hls_list_size') + 1], str(hls.PLAYLIST_SEGMENTS))
        self.assertEqual(retained[retained.index('-hls_list_size') + 1], '0')
        self.assertEqual(retained[retained.index('-hls_playlist_type') + 1], 'event')
        self.assertIn('delete_segments', rolling[rolling.index('-hls_flags') + 1])
        self.assertNotIn('delete_segments', retained[retained.index('-hls_flags') + 1])
        for flag in ('append_list', 'discont_start', 'temp_file'):
            self.assertIn(flag, retained[retained.index('-hls_flags') + 1])
        self.assertLess(retained.index('-copyts'), retained.index('-i'))
        self.assertLess(retained.index('-start_at_zero'), retained.index('-i'))
        self.assertEqual(retained[retained.index('-af') + 1], 'aresample=async=1:first_pts=0')

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
