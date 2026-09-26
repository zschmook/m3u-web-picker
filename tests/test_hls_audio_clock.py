"""Exercise decoded audio timing across a source interruption."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from media import hls


@unittest.skipUnless(shutil.which('ffmpeg'), 'Requires FFmpeg')
class HlsAudioClockTests(unittest.TestCase):
    def test_missing_audio_samples_do_not_shorten_video_timeline(self):
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
            subprocess.run(command, check=True, timeout=15)
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
