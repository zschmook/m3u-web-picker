import io
import unittest
from unittest.mock import patch

from media.audio_source import prefer_live_playlist


class AudioSourceTests(unittest.TestCase):
    def test_valid_live_playlist_preserves_credentials_and_query(self):
        original = "https://provider.test/live/user/password/2050.ts?token=value"
        data = b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:10\n#EXTINF:10,\n10.ts\n"
        with patch("media.audio_source.urlopen", return_value=io.BytesIO(data)) as fetch:
            self.assertEqual(prefer_live_playlist(original), original.replace("2050.ts", "2050.m3u8"))
        self.assertEqual(fetch.call_args.kwargs["timeout"], 3)

    def test_unavailable_finite_or_invalid_playlist_keeps_original(self):
        original = "https://provider.test/live/user/password/2050.ts"
        for data in (b"<html>not found</html>", b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1,\n0.ts\n#EXT-X-ENDLIST",
                     b"x" * 65537):
            with self.subTest(data=data[:20]), patch("media.audio_source.urlopen", return_value=io.BytesIO(data)):
                self.assertEqual(prefer_live_playlist(original), original)
        with patch("media.audio_source.urlopen", side_effect=TimeoutError):
            self.assertEqual(prefer_live_playlist(original), original)

    def test_local_custom_and_unrelated_stream_paths_are_not_probed(self):
        with patch("media.audio_source.urlopen") as fetch:
            for target in ("/tmp/clip.ts", "http://localhost:9999/stream/custom/123.ts",
                           "https://provider.test/movie/u/p/2050.ts", "https://provider.test/live.m3u8"):
                self.assertEqual(prefer_live_playlist(target), target)
        fetch.assert_not_called()
