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

from media import hls


@unittest.skipUnless(shutil.which('ffmpeg'), 'Requires FFmpeg')
class HlsAudioClockTests(unittest.TestCase):
    def test_audio_visual_cue_keeps_source_alignment_with_delayed_track(self):
        for video_offset, audio_offset in ((0, .7), (.7, 0)):
            with self.subTest(video_offset=video_offset, audio_offset=audio_offset), tempfile.TemporaryDirectory() as temp:
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
