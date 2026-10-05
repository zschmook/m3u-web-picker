"""Receiver playlists preserve absolute media epochs as FFmpeg windows change."""
import unittest

from media.hls_manifest import HlsManifestNormalizer


def playlist(start, names, *, before=(), base=None):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2",
             f"#EXT-X-MEDIA-SEQUENCE:{start}"]
    if base is not None:
        lines.append(f"#EXT-X-DISCONTINUITY-SEQUENCE:{base}")
    for index, name in enumerate(names):
        if index in before:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.extend([f"#EXT-X-PROGRAM-DATE-TIME:2026-09-27T12:{index * 2 // 60:02d}:{index * 2 % 60:02d}Z",
                      "#EXTINF:2.000000,", name])
    return "\n".join(lines) + "\n"


def media_epochs(text):
    sequence, epoch = 0, 0
    entries = {}
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.partition(":")[2])
        elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            epoch = int(line.partition(":")[2])
        elif line == "#EXT-X-DISCONTINUITY":
            epoch += 1
        elif line and not line.startswith("#"):
            entries[(sequence, line)] = epoch
            sequence += 1
    return entries


class HlsManifestNormalizerTests(unittest.TestCase):
    def test_initial_leading_marker_becomes_base_and_stays_after_sliding(self):
        normalizer = HlsManifestNormalizer()
        first = normalizer.normalize(playlist(0, ["a.ts", "b.ts", "c.ts"], before=(0,)))
        second = normalizer.normalize(playlist(1, ["b.ts", "c.ts", "d.ts"]))
        self.assertEqual(set(media_epochs(first).values()), {1})
        self.assertEqual(set(media_epochs(second).values()), {1})
        self.assertIn("#EXT-X-DISCONTINUITY-SEQUENCE:1\n", second)
        self.assertNotIn("#EXT-X-DISCONTINUITY\n", first)

    def test_internal_boundary_retains_epoch_after_marker_leaves_window(self):
        normalizer = HlsManifestNormalizer()
        first = normalizer.normalize(playlist(10, ["a.ts", "b.ts", "c.ts"], before=(1,)))
        second = normalizer.normalize(playlist(11, ["b.ts", "c.ts", "d.ts"]))
        self.assertEqual(media_epochs(first), {(10, "a.ts"): 0, (11, "b.ts"): 1, (12, "c.ts"): 1})
        self.assertEqual(media_epochs(second), {(11, "b.ts"): 1, (12, "c.ts"): 1, (13, "d.ts"): 1})

    def test_restart_leading_marker_does_not_renumber_retained_epochs(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(10, ["a.ts", "b.ts", "c.ts"], before=(1,)))
        result = normalizer.normalize(
            playlist(10, ["a.ts", "b.ts", "c.ts", "fresh.ts"], before=(0, 1, 3)), generation=1)
        self.assertEqual(media_epochs(result), {
            (10, "a.ts"): 0, (11, "b.ts"): 1, (12, "c.ts"): 1, (13, "fresh.ts"): 2})
        self.assertEqual(result.count("#EXT-X-DISCONTINUITY\n"), 2)

    def test_new_boundary_after_tail_anchor_is_preserved(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(0, ["a.ts", "b.ts"], before=(0,)))
        result = normalizer.normalize(playlist(1, ["b.ts", "c.ts", "d.ts"], before=(1,)))
        self.assertEqual(media_epochs(result), {(1, "b.ts"): 1, (2, "c.ts"): 2, (3, "d.ts"): 2})

    def test_restart_after_sliding_keeps_nonzero_base_and_fresh_boundary(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(0, ["a.ts", "b.ts", "c.ts"], before=(1,)))
        normalizer.normalize(playlist(1, ["b.ts", "c.ts", "d.ts"]))
        result = normalizer.normalize(
            playlist(2, ["c.ts", "d.ts", "fresh.ts"], before=(0, 2)), generation=1)
        self.assertEqual(media_epochs(result), {(2, "c.ts"): 1, (3, "d.ts"): 1, (4, "fresh.ts"): 2})
        self.assertIn("#EXT-X-DISCONTINUITY-SEQUENCE:1\n", result)
        self.assertEqual(result.count("#EXT-X-DISCONTINUITY\n"), 1)

    def test_redundant_internal_marker_cannot_change_known_segment(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(0, ["a.ts", "b.ts", "c.ts"]))
        result = normalizer.normalize(playlist(0, ["a.ts", "b.ts", "c.ts"], before=(1,)))
        self.assertEqual(set(media_epochs(result).values()), {0})
        self.assertNotIn("#EXT-X-DISCONTINUITY\n", result)

    def test_no_overlap_recovery_advances_epoch_once(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(0, ["a.ts", "b.ts"], before=(0,)))
        raw = playlist(20, ["fresh.ts", "new.ts"], before=(0,))
        result = normalizer.normalize(raw, generation=1)
        self.assertEqual(set(media_epochs(result).values()), {2})
        self.assertEqual(normalizer.normalize(raw, generation=1), result)

    def test_no_overlap_recovery_without_raw_marker_still_has_new_epoch(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(0, ["a.ts"]))
        result = normalizer.normalize(playlist(1, ["fresh.ts"]), generation=1)
        self.assertEqual(media_epochs(result), {(1, "fresh.ts"): 1})

    def test_no_overlap_preserves_multiple_unseen_recovery_generations(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(0, ["a.ts"], before=(0,)), generation=0)
        raw = playlist(20, ["fresh.ts", "new.ts"])
        result = normalizer.normalize(raw, generation=3)
        self.assertEqual(media_epochs(result), {(20, "fresh.ts"): 4, (21, "new.ts"): 4})
        self.assertEqual(normalizer.normalize(raw, generation=3), result)

    def test_initial_explicit_base_and_internal_boundaries_are_respected(self):
        normalizer = HlsManifestNormalizer()
        raw = playlist(10, ["a.ts", "b.ts", "c.ts"], before=(0, 2), base=5)
        result = normalizer.normalize(raw)
        self.assertEqual(media_epochs(result), {(10, "a.ts"): 6, (11, "b.ts"): 6, (12, "c.ts"): 7})
        self.assertEqual(normalizer.normalize(result), result)

    def test_timestamps_durations_uris_and_other_tags_keep_their_order(self):
        normalizer = HlsManifestNormalizer()
        raw = playlist(4, ["segment_4.ts", "segment_5.ts"], before=(0, 1)) + "#EXT-X-ENDLIST\n"
        result = normalizer.normalize(raw)
        unaffected = lambda text: [line for line in text.splitlines()
            if not line.startswith("#EXT-X-DISCONTINUITY")]
        self.assertEqual(unaffected(result), unaffected(raw))
        self.assertLess(result.index("#EXT-X-DISCONTINUITY-SEQUENCE:"), result.index("#EXTINF:"))

    def test_memory_is_bounded_and_keeps_a_larger_current_window(self):
        normalizer = HlsManifestNormalizer(max_entries=40)
        for start in range(200):
            names = [f"segment_{index}.ts" for index in range(start, start + 3)]
            normalizer.normalize(playlist(start, names))
            self.assertLessEqual(normalizer.tracked_segments, 40)
        names = [f"segment_{index}.ts" for index in range(200, 280)]
        normalizer.normalize(playlist(200, names))
        self.assertEqual(normalizer.tracked_segments, 80)
        normalizer.normalize(playlist(278, ["segment_278.ts", "segment_279.ts", "segment_280.ts"]))
        self.assertEqual(normalizer.tracked_segments, 40)

    def test_empty_manifest_preserves_previous_state(self):
        normalizer = HlsManifestNormalizer()
        normalizer.normalize(playlist(0, ["a.ts"], before=(0,)))
        self.assertEqual(normalizer.normalize("#EXTM3U\n", generation=1), "#EXTM3U\n")
        result = normalizer.normalize(playlist(0, ["a.ts", "b.ts"]))
        self.assertEqual(set(media_epochs(result).values()), {1})

    def test_retained_event_reconstructs_old_epochs_from_bounded_recent_anchors(self):
        normalizer = HlsManifestNormalizer(max_entries=40, retain_window=False)
        names = [f"segment_{index}.ts" for index in range(90)]
        raw = playlist(0, names, before=(0, 20, 55), base=7)
        raw = raw.replace("#EXT-X-VERSION:3\n", "#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:EVENT\n")
        first = normalizer.normalize(raw)
        old_epochs = media_epochs(first)
        self.assertEqual(normalizer.tracked_segments, 40)
        self.assertEqual(normalizer.normalize(raw), first)

        names.extend(f"segment_{index}.ts" for index in range(90, 100))
        restarted = playlist(0, names, before=(0, 20, 55, 90), base=7)
        restarted = restarted.replace("#EXT-X-DISCONTINUITY\n",
            "#EXT-X-DISCONTINUITY\n#EXT-X-DISCONTINUITY\n", 1)
        result = normalizer.normalize(restarted, generation=1)
        epochs = media_epochs(result)
        self.assertEqual({key: epochs[key] for key in old_epochs}, old_epochs)
        self.assertEqual({epochs[(index, f"segment_{index}.ts")] for index in range(90, 100)}, {11})
        self.assertEqual(len(epochs), 100)
        self.assertEqual(normalizer.tracked_segments, 40)
        self.assertEqual(normalizer.normalize(restarted, generation=1), result)

    def test_retained_event_growth_and_multiple_restarts_keep_full_history(self):
        normalizer = HlsManifestNormalizer(max_entries=40, retain_window=False)
        previous = {}
        for generation, end in enumerate((80, 130, 180)):
            names = [f"segment_{index}.ts" for index in range(end)]
            boundaries = (0, 20) + tuple(value for value in (80, 130) if value < end)
            raw = playlist(0, names, before=boundaries)
            if generation:
                raw = raw.replace("#EXT-X-DISCONTINUITY\n",
                    "#EXT-X-DISCONTINUITY\n#EXT-X-DISCONTINUITY\n", 1)
            result = normalizer.normalize(raw, generation=generation)
            current = media_epochs(result)
            self.assertEqual({key: current[key] for key in previous}, previous)
            self.assertEqual(current[(end - 1, f"segment_{end - 1}.ts")], generation + 2)
            self.assertEqual(normalizer.tracked_segments, 40)
            previous = current


if __name__ == "__main__":
    unittest.main()
