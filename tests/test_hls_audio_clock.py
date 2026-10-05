"""Exercise decoded audio timing across a source interruption."""
from pathlib import Path
import array
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from media import hls, roku_movie


@unittest.skipUnless(shutil.which('ffmpeg'), 'Requires FFmpeg')
class HlsAudioClockTests(unittest.TestCase):
    def tearDown(self):
        for token in list(roku_movie.SESSIONS):
            roku_movie.stop(token)

    def test_audio_visual_cue_keeps_source_alignment_with_delayed_track(self):
        for video_offset, audio_offset, private_movie in ((0, .7, False), (.7, 0, False), (0, .7, True), (.7, 0, True)):
            with self.subTest(video_offset=video_offset, audio_offset=audio_offset, private_movie=private_movie), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                source = directory / 'offset.mkv'
                visual_cue = 2 - video_offset
                audible_cue = 2 - audio_offset
                subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-itsoffset', str(video_offset), '-f', 'lavfi', '-i',
                    f"color=c=black:s=160x90:r=20:d=4,drawbox=color=white:t=fill:enable='between(t,{visual_cue},{visual_cue + .3})'",
                    '-itsoffset', str(audio_offset), '-f', 'lavfi', '-i',
                    f"aevalsrc=if(between(t\\,{audible_cue}\\,{audible_cue + .3})\\,sin(2*PI*440*t)\\,0):s=48000:d=4",
                    '-t', '4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-fps_mode:v', 'passthrough',
                    '-c:a', 'aac', str(source)], check=True, timeout=15)
                output = directory / 'relay'
                output.mkdir()
                with patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'):
                    if private_movie:
                        with patch.object(roku_movie, 'ROOT', output):
                            session = roku_movie.start(dict(target=str(source), input_headers={}), live=True)
                        self.assertEqual(session.process.wait(timeout=15), 0)
                        output = session.directory
                    else:
                        subprocess.run(hls._hls_command(str(source), output), check=True, timeout=15)
                combined = directory / 'relay.ts'
                combined.write_bytes(b''.join(path.read_bytes() for path in sorted(output.glob('segment_*.ts'))))
                timing = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                    'stream=codec_type,start_time', '-of', 'json', str(combined)],
                    capture_output=True, check=True, timeout=10).stdout)
                starts = {stream['codec_type']: float(stream['start_time']) for stream in timing['streams']}
                video = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-i', str(combined), '-map', '0:v:0', '-pix_fmt', 'gray', '-fps_mode', 'passthrough',
                    '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True, timeout=10).stdout
                audio = array.array('h', subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-i', str(combined), '-map', '0:a:0', '-ac', '1', '-ar', '48000',
                    '-f', 's16le', 'pipe:1'], capture_output=True, check=True, timeout=10).stdout)
                frame_size = 160 * 90
                flash = next(index for index in range(len(video) // frame_size)
                    if sum(video[index * frame_size:(index + 1) * frame_size]) / frame_size > 128)
                tone = next(index for index in range(0, len(audio) - 960, 960)
                    if sum(abs(value) for value in audio[index:index + 960]) / 960 > 500)
                flash_time = starts['video'] + flash / 20
                tone_time = starts['audio'] + tone / 48000
                self.assertAlmostEqual(flash_time, tone_time, delta=.1,
                    msg='Rebasing each track separately must not shift a synchronized flash and tone.')

    def test_live_movie_paused_segment_remains_decodable_as_encoder_advances(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'movie.mkv'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=20:duration=8',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, timeout=15)
            output = directory / 'private'
            output.mkdir()
            with patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'), \
                    patch.object(roku_movie, 'ROOT', output):
                session = roku_movie.start(dict(target=str(source), input_headers={}), live=True)
            encoder = session.process
            output = session.directory
            try:
                paused_file = output / 'segment_000000.ts'
                paused_bytes = paused_file.read_bytes()
                self.assertEqual(encoder.wait(timeout=12), 0)
                self.assertGreaterEqual(len(list(output.glob('segment_*.ts'))), 4)
                self.assertEqual(paused_file.read_bytes(), paused_bytes)
                self.assertIn(paused_file.name, (output / 'stream.m3u8').read_text())
                decoded = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-i', str(paused_file), '-map', '0:v:0', '-f', 'null', '-'], capture_output=True, timeout=10)
                self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors='replace'))
                self.assertEqual(decoded.stderr, b'')
            finally:
                roku_movie.stop(session.token)

    def test_movie_starts_before_full_cushion_and_producer_stays_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'long-movie.mkv'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=20:duration=90',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=90',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, timeout=20)
            started = time.monotonic()
            with patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'), \
                    patch.object(roku_movie, 'ROOT', directory / 'sessions'):
                session = roku_movie.start(dict(target=str(source), input_headers={}))
            try:
                self.assertLess(time.monotonic() - started, 5, 'Do not wait for thirty seconds before returning playback.')
                deadline = time.monotonic() + 5
                while hls.buffered_seconds(session.directory) < 8 and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertGreaterEqual(hls.buffered_seconds(session.directory), 8,
                    'The cushion should fill after the first segment is ready.')
                time.sleep(.5)
                self.assertLessEqual(hls.buffered_seconds(session.directory),
                    roku_movie.READ_AHEAD_SECONDS + time.monotonic() - started + 2,
                    'An ongoing movie must not be encoded all the way to its end immediately.')
                self.assertIsNone(session.process.poll())
                self.assertTrue((session.directory / 'segment_000000.ts').is_file())
            finally:
                roku_movie.stop(session.token)
            self.assertFalse(session.feeder.is_alive())
            self.assertFalse(session.producer.reader.is_alive())
            self.assertFalse(session.directory.exists())

    def test_replayed_provider_window_is_not_encoded_as_new_footage(self):
        # The same source packets arrive again after reconnect. Monotonic output
        # alone is insufficient: retiming that window makes it visibly replay.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            clip=root/'clip.ts'
            subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                '-f','lavfi','-i',"color=c=black:s=160x90:r=20:d=4,drawbox=color=white:t=fill:enable='between(t,2,2.3)'",
                '-f','lavfi','-i',r'aevalsrc=if(between(t\,2\,2.3)\,sin(2*PI*440*t)\,0):s=48000:d=4',
                '-c:v','libx264','-pix_fmt','yuv420p','-bf','0','-g','40',
                '-c:a','aac','-f','mpegts',str(clip)],check=True,timeout=15)
            source=root/'reconnected.ts'
            source.write_bytes(clip.read_bytes()*2)
            output=root/'relay'
            output.mkdir()
            with patch('media.ffmpeg.media_pipeline.active_encoder',return_value='libx264'):
                subprocess.run(hls._hls_command(str(source),output),check=True,timeout=20)
            combined=root/'output.ts'
            combined.write_bytes(b''.join(p.read_bytes() for p in sorted(output.glob('segment_*.ts'))))
            packets=json.loads(subprocess.run(['ffprobe','-v','error','-show_packets','-show_entries',
                'packet=stream_index,pts_time','-of','json',str(combined)],
                capture_output=True,check=True,timeout=10).stdout)['packets']
            tracks={}
            for packet in packets:
                if 'pts_time' in packet:
                    tracks.setdefault(packet['stream_index'],[]).append(float(packet['pts_time']))
            for values in tracks.values():
                self.assertTrue(all(b>=a-.002 for a,b in zip(values,values[1:])),
                    'A provider reset must not send Roku an audio or video clock that goes backward.')
            audio=array.array('h',subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                '-i',str(combined),'-map','0:a:0','-ac','1','-ar','48000','-f','s16le','pipe:1'],
                capture_output=True,check=True,timeout=10).stdout)
            self.assertAlmostEqual(len(audio)/48000,4,delta=.15,
                msg='A replayed source window must be discarded instead of retimed forward.')
            video=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                '-i',str(combined),'-map','0:v:0','-pix_fmt','gray','-fps_mode','passthrough',
                '-f','rawvideo','pipe:1'],capture_output=True,check=True,timeout=10).stdout
            flashes=[tracks[0][0]+i/20 for i in range(len(video)//(160*90))
                if sum(video[i*160*90:(i+1)*160*90])/(160*90)>128]
            tones=[tracks[1][0]+i/48000 for i in range(0,len(audio)-960,960)
                if sum(abs(s) for s in audio[i:i+960])/960>500]
            for lower,upper in ((3,4),):
                flash=next(t for t in flashes if lower<t<upper)
                tone=next(t for t in tones if lower<t<upper)
                self.assertAlmostEqual(flash,tone,delta=.1)

    def test_long_reconnect_overlap_does_not_replay_old_video(self):
        # Cross the default ten-second timestamp-correction threshold, with
        # an unambiguous increasing visual cue rather than only packet clocks.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', "nullsrc=s=64x36:r=10:d=36,geq=lum='16+N*0.45':cb=128:cr=128",
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=36',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-bf', '0', '-g', '20',
                '-c:a', 'aac', '-f', 'segment', '-segment_time', '2', '-segment_format', 'mpegts',
                '-reset_timestamps', '0', str(root / 'source_%03d.ts')],
                capture_output=True, check=True, timeout=15)
            segments = sorted(root.glob('source_*.ts'))
            source = root / 'overlap.ts'
            source.write_bytes(b''.join(p.read_bytes() for p in segments[:13] + segments[6:]))
            output = root / 'relay'
            output.mkdir()
            with patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'):
                subprocess.run(hls._hls_command(str(source), output),
                    capture_output=True, check=True, timeout=55)
            combined = root / 'output.ts'
            combined.write_bytes(b''.join(p.read_bytes() for p in sorted(output.glob('segment_*.ts'))))
            video = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-i', str(combined), '-map', '0:v:0', '-pix_fmt', 'gray', '-fps_mode', 'passthrough',
                '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True, timeout=10).stdout
            means = [sum(video[i:i+64*36])/(64*36) for i in range(0, len(video), 64*36)]
            self.assertAlmostEqual(len(means)/10, 36, delta=.3,
                msg='The fourteen-second old window must not extend the unique footage.')
            self.assertFalse(any(b < a-5 for a, b in zip(means, means[1:])),
                'An increasing source visual cue must not jump backward after reconnect.')
            audio = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-i', str(combined), '-map', '0:a:0', '-ac', '1', '-ar', '48000',
                '-f', 's16le', 'pipe:1'], capture_output=True, check=True, timeout=10).stdout
            self.assertAlmostEqual(len(audio)/2/48000, 36, delta=.3)

    def test_missing_audio_samples_preserve_timeline_and_live_pacing(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'audio-gap.mkv'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=30:duration=6',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=6',
                '-filter_complex', "[1:a]aselect='not(between(t,2,2.7))'[a]",
                '-map', '0:v', '-map', '[a]', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', str(source)], check=True, timeout=15)
            output = directory / 'relay'
            output.mkdir()
            with patch('media.ffmpeg.media_pipeline.active_encoder', return_value='libx264'):
                command = hls._hls_command(str(source), output)
            started = time.monotonic()
            subprocess.run(command, check=True, timeout=15)
            self.assertGreaterEqual(time.monotonic() - started, 5,
                msg='A buffered source must not publish six seconds of live media in a burst.')
            combined = directory / 'relay.ts'
            combined.write_bytes(b''.join(path.read_bytes() for path in sorted(output.glob('segment_*.ts'))))
            decoded = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-i', str(combined), '-map', '0:a:0', '-ac', '1', '-ar', '48000',
                '-f', 's16le', 'pipe:1'], capture_output=True, check=True, timeout=15)
            samples = len(decoded.stdout) // 2
            self.assertAlmostEqual(samples / 48000, 6, delta=.15,
                msg='Missing source samples must remain silence rather than shorten the soundtrack.')
            # The inserted region is quiet; subsequent commentary is preserved.
            import array
            audio = array.array('h', decoded.stdout)
            gap = audio[int(2.2 * 48000):int(2.5 * 48000)]
            later = audio[int(3 * 48000):int(3.3 * 48000)]
            self.assertLess(sum(abs(value) for value in gap) / len(gap), 50)
            self.assertGreater(sum(abs(value) for value in later) / len(later), 500)


if __name__ == '__main__':
    unittest.main()
