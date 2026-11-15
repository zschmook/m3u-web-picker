"""Real FFmpeg regressions for live transport timing; synthetic media only."""
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from media import mpegts
from media.ffmpeg import normalized_live_input_args


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg tools required')
class MpegtsClockTests(unittest.TestCase):
    encoder = 'libx264'
    @staticmethod
    def run_media(command, data=None):
        result = subprocess.run(command, input=data, capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stderr.decode(errors='replace')[-1200:])
        return result.stdout

    @classmethod
    def setUpClass(cls):
        cls.source = cls.run_media(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=60000/1001', '-itsoffset', '0.2', '-f', 'lavfi', '-i',
            'sine=frequency=500:sample_rate=48000', '-t', '3', '-c:v', 'libx264',
            '-preset', 'veryfast', '-bf', '3', '-g', '60', '-c:a', 'aac', '-ac', '2',
            '-f', 'mpegts', 'pipe:1'])

    @classmethod
    def timestamps(cls, data):
        raw = cls.run_media(['ffprobe', '-v', 'error', '-show_packets', '-show_entries',
            'packet=stream_index,pts_time', '-of', 'json', '-i', 'pipe:0'], data)
        tracks = {}
        for packet in json.loads(raw)['packets']:
            if 'pts_time' in packet:
                tracks.setdefault(packet['stream_index'], []).append(float(packet['pts_time']))
        return tracks

    @staticmethod
    def rewrite(data, *, offset=0, glitches=False):
        data = bytearray(data)
        video_count = 0
        def change(index, seconds):
            x = data[index:index+5]
            value = ((x[0]&14)<<29)|(x[1]<<22)|((x[2]&254)<<14)|(x[3]<<7)|(x[4]>>1)
            value = (value + round(seconds*90000)) % (1<<33)
            data[index:index+5] = bytes([(x[0]&0xf1)|((value>>29)&14), (value>>22)&255,
                ((value>>14)&254)|1, (value>>7)&255, ((value<<1)&254)|1])
        for start in range(0, len(data)-187, 188):
            packet = data[start:start+188]
            if not packet[1]&64 or not packet[3]&16:
                continue
            begin = start+4+(1+packet[4] if packet[3]&32 else 0)
            if data[begin:begin+3] != b'\0\0\1':
                continue
            flags = data[begin+7]&192
            if flags&128:
                change(begin+9, offset)
            if flags == 192:
                video_count += 1
                change(begin+14, offset - (.15 if glitches and video_count%10 == 0 else 0))
        return bytes(data)

    def encode(self, data, *, old=False):
        with patch('media_pipeline.active_encoder', return_value=self.encoder):
            command = normalized_live_input_args('pipe:0') + [
                '-f', 'mpegts', '-mpegts_flags', '+resend_headers',
                '-muxdelay', '0', '-muxpreload', '0', 'pipe:1'] if old else mpegts._command('pipe:0')
        return self.timestamps(self.run_media(command, data))

    def assert_continuous(self, tracks, seconds):
        self.assertEqual(set(tracks), {0, 1})
        for index, values in tracks.items():
            expected = seconds[index] if isinstance(seconds, dict) else seconds
            self.assertAlmostEqual(max(values)-min(values), expected, delta=.2)
            self.assertLess(max(b-a for a,b in zip(values,values[1:])), .05)
            self.assertGreaterEqual(min(b-a for a,b in zip(values,values[1:])), -.001)

    def test_bad_decoding_timestamps_do_not_inflate_output_clock(self):
        source = self.rewrite(self.source, glitches=True)
        broken = self.encode(source, old=True)
        self.assertGreater(max(broken[0])-min(broken[0]), 4)
        self.assertGreater(max(b-a for a,b in zip(broken[0],broken[0][1:])), .1)
        self.assert_continuous(self.encode(source), 3)

    def test_normal_broadcast_keeps_initial_audio_video_offset(self):
        source = self.timestamps(self.source)
        output = self.encode(self.source)
        self.assert_continuous(output, 3)
        original_offset = min(source[1])-min(source[0])
        self.assertGreater(original_offset, .1)
        self.assertAlmostEqual(min(output[1])-min(output[0]), original_offset, delta=.04)

    def test_source_clock_reset_does_not_create_huge_output_gap(self):
        reset = self.rewrite(self.source, offset=1800) + self.source
        # Audio begins 0.2 s after video in each clip. Expect the actual sample
        # duration, rather than pretending both clips contain six seconds of it.
        normal = self.encode(self.source)
        expected = {index: 2*(max(values)-min(values)) for index,values in normal.items()}
        self.assert_continuous(self.encode(reset), expected)

    def test_missing_audio_packets_do_not_advance_audio_content(self):
        source = self.run_media(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=60000/1001', '-f', 'lavfi', '-i',
            'sine=frequency=500:sample_rate=48000', '-t', '3',
            '-af', "aselect='not(between(t,0.7,1.7))'", '-c:v', 'libx264',
            '-preset', 'veryfast', '-bf', '3', '-c:a', 'aac', '-ac', '2',
            '-f', 'mpegts', 'pipe:1'])
        original = self.timestamps(source)
        self.assertGreater(max(b-a for a,b in zip(original[1], original[1][1:])), .8)
        output = self.encode(source)
        self.assert_continuous(output, 3)
