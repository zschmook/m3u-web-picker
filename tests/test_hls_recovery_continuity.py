"""Synthetic FFmpeg checks for the identity of media across HLS recovery."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from media import hls
from media.hls_manifest import HlsManifestNormalizer


def epochs(text):
    sequence, epoch = 0, 0
    result = {}
    for line in text.splitlines():
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            sequence = int(line.partition(':')[2])
        elif line.startswith('#EXT-X-DISCONTINUITY-SEQUENCE:'):
            epoch = int(line.partition(':')[2])
        elif line == '#EXT-X-DISCONTINUITY':
            epoch += 1
        elif line and not line.startswith('#'):
            result[(sequence, line)] = epoch
            sequence += 1
    return result


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires FFmpeg and FFprobe")
class HlsRecoveryContinuityTests(unittest.TestCase):
    def run_media_tool(self, arguments):
        result = subprocess.run(arguments, capture_output=True, timeout=20)
        # Keep tool arguments and source details out of failure output.
        self.assertEqual(result.returncode, 0, "Synthetic media operation failed.")
        return result.stdout

    def make_source(self, path, color, seconds=6):
        self.run_media_tool([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=64x36:r=20:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-bf", "0", "-g", "40", "-c:a", "aac", "-f", "mpegts", str(path),
        ])

    def publish(self, source, directory, *, retain_history=False):
        with patch("media.ffmpeg.media_pipeline.active_encoder", return_value="libx264"):
            arguments = hls._hls_command(str(source), directory, retain_history=retain_history)
        arguments.remove("-re")
        self.run_media_tool(arguments)

    def manifest(self, directory):
        sequence = 0
        discontinuity = False
        entries = []
        for line in (directory / "stream.m3u8").read_text().splitlines():
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                sequence = int(line.partition(":")[2])
            elif line == "#EXT-X-DISCONTINUITY":
                discontinuity = True
            elif line and not line.startswith("#"):
                entries.append((line, sequence, discontinuity))
                sequence += 1
                discontinuity = False
        return entries

    def first_video_timestamp(self, path):
        raw = self.run_media_tool([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-read_intervals", "%+#1", "-show_entries", "packet=pts_time",
            "-of", "json", str(path),
        ])
        return float(json.loads(raw)["packets"][0]["pts_time"])

    def test_recovery_preserves_old_uri_identity_and_appends_fresh_clock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "relay"
            output.mkdir()
            old_source, fresh_source = root / "old.ts", root / "fresh.ts"
            self.make_source(old_source, "black")
            self.make_source(fresh_source, "white")
            self.publish(old_source, output)
            normalizer = HlsManifestNormalizer()
            before_epochs = epochs(normalizer.normalize((output/'stream.m3u8').read_text()))
            before = self.manifest(output)
            self.assertGreaterEqual(len(before), 3)
            old_sequences = {uri: sequence for uri, sequence, _ in before}
            old_bytes = {uri: (output / uri).read_bytes() for uri in old_sequences}
            last_old_timestamp = self.first_video_timestamp(output / before[-1][0])

            # A replacement encoder starts with an independent zero-based clock.
            self.publish(fresh_source, output)
            raw_after = (output/'stream.m3u8').read_text()
            after_epochs = epochs(normalizer.normalize(raw_after, generation=1))
            self.assertEqual((output/'stream.m3u8').read_text(), raw_after)
            for identity, epoch in before_epochs.items():
                self.assertEqual(after_epochs[identity], epoch,
                    'Recovery changed the clock epoch of retained footage.')
            after = self.manifest(output)
            recovered_sequences = {uri: sequence for uri, sequence, _ in after}
            fresh_entries = [entry for entry in after if entry[0] not in old_sequences]
            self.assertGreaterEqual(len(fresh_entries), 3)
            for uri, contents in old_bytes.items():
                self.assertIn(uri, recovered_sequences, "Recovery removed playable history.")
                self.assertEqual((output / uri).read_bytes(), contents)
            self.assertTrue(fresh_entries[0][2], "A fresh media clock needs an explicit discontinuity.")
            self.assertLess(self.first_video_timestamp(output / fresh_entries[0][0]), last_old_timestamp)
            for uri, old_sequence in old_sequences.items():
                self.assertEqual(recovered_sequences[uri], old_sequence,
                    "Recovery changed the sequence identity of already-published media.")
            self.assertEqual(len(after), len(recovered_sequences), "Recovery reused a segment URI.")
            self.assertEqual(fresh_entries[0][1], max(old_sequences.values()) + 1)
            self.assertEqual(after_epochs[(fresh_entries[0][1], fresh_entries[0][0])],
                max(before_epochs.values())+1)
            names = [uri for uri, _, _ in after]
            numbers = [int(Path(uri).stem.partition("_")[2]) for uri in names]
            self.assertEqual(numbers, list(range(numbers[0], numbers[0] + len(numbers))),
                "Recovery skipped or reused a segment number.")
            # A second restart must preserve both previously published epochs.
            self.publish(old_source, output)
            final_epochs = epochs(normalizer.normalize((output/'stream.m3u8').read_text(), generation=2))
            for identity, epoch in after_epochs.items():
                self.assertEqual(final_epochs[identity], epoch)
            self.assertEqual(max(final_epochs.values()), max(after_epochs.values())+1)

    def test_movie_pause_history_survives_rolling_window_and_encoder_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root/'movie'
            output.mkdir()
            old_source, fresh_source = root/'old.ts', root/'fresh.ts'
            self.make_source(old_source, 'black', seconds=(hls.PLAYLIST_SEGMENTS + 8)*hls.SEGMENT_SECONDS)
            self.make_source(fresh_source, 'white')
            self.publish(old_source, output, retain_history=True)
            before = self.manifest(output)
            self.assertGreater(len(before), hls.PLAYLIST_SEGMENTS)
            self.assertEqual(before[0][1], 0)
            old_first = output/before[0][0]
            old_bytes = old_first.read_bytes()
            normalizer = HlsManifestNormalizer(retain_window=False)
            before_epochs = epochs(normalizer.normalize((output/'stream.m3u8').read_text()))
            self.assertLessEqual(normalizer.tracked_segments, hls.PLAYLIST_SEGMENTS)

            # The viewer can remain paused at the first segment while the
            # source advances beyond the rolling window and reconnects.
            self.publish(fresh_source, output, retain_history=True)
            raw = (output/'stream.m3u8').read_text()
            after_epochs = epochs(normalizer.normalize(raw, generation=1))
            after = self.manifest(output)
            self.assertIn('#EXT-X-PLAYLIST-TYPE:EVENT', raw)
            self.assertNotIn('#EXT-X-ENDLIST', raw)
            self.assertEqual(old_first.read_bytes(), old_bytes)
            self.assertGreater(len(after), len(before))
            for identity, epoch in before_epochs.items():
                self.assertEqual(after_epochs[identity], epoch)
                self.assertTrue((output/identity[1]).is_file())
            self.assertLessEqual(normalizer.tracked_segments, hls.PLAYLIST_SEGMENTS)

            first_fresh = output/after[len(before)][0]
            for segment, expected in ((old_first, 0), (first_fresh, 255)):
                frame = self.run_media_tool(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-i', str(segment), '-map', '0:v:0', '-frames:v', '1', '-pix_fmt', 'gray',
                    '-f', 'rawvideo', 'pipe:1'])
                self.assertEqual(len(frame), 64*36)
                self.assertAlmostEqual(sum(frame)/len(frame), expected, delta=2,
                    msg='Paused footage must remain decodable after source recovery.')
            self.assertEqual(after_epochs[(after[len(before)][1], after[len(before)][0])],
                max(before_epochs.values())+1)
            # Serving the unchanged EVENT playlist repeatedly must not shift
            # old or new clock epochs when only forty identities are tracked.
            self.assertEqual(epochs(normalizer.normalize(raw, generation=1)), after_epochs)


if __name__ == "__main__":
    unittest.main()
