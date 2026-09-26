from pathlib import Path
import io
import unittest
from unittest.mock import patch

from flask import Flask

import custom_channels
import movie_restart
from api import guide
from media import ffmpeg
from media import browser


ROOT = Path(__file__).resolve().parents[1]


class MovieRestartTests(unittest.TestCase):
    def setUp(self):
        movie_restart.reset_for_tests()

    def tearDown(self):
        movie_restart.reset_for_tests()

    def test_ticket_is_opaque_reused_and_expires(self):
        url = movie_restart.issue_plex(
            "http://plex.local:32400", "private-token", "/library/parts/123/file.mkv",
            title="The Movie", now=100,
        )
        self.assertRegex(url, r"^/guide/play/restart/[A-Za-z0-9_-]{24,64}$")
        self.assertNotIn("private-token", url)
        self.assertNotIn("library", url)
        self.assertEqual(
            movie_restart.issue_plex(
                "http://plex.local:32400", "private-token", "/library/parts/123/file.mkv", now=200,
            ),
            url,
        )
        ticket = url.rsplit("/", 1)[-1]
        source = movie_restart.resolve(ticket, now=201)
        self.assertEqual(source["target"], "http://plex.local:32400/library/parts/123/file.mkv")
        self.assertEqual(source["input_headers"], {"X-Plex-Token": "private-token"})
        with self.assertRaises(ValueError):
            movie_restart.resolve(ticket, now=200 + movie_restart.TICKET_TTL_SECONDS + 1)

    def test_ticket_rejects_external_parts_and_header_injection(self):
        invalid = [
            "https://other.invalid/movie.mp4",
            "//other.invalid/movie.mp4",
            "/library/parts/../secret",
            "/library/metadata/123",
        ]
        for part in invalid:
            with self.subTest(part=part), self.assertRaises(ValueError):
                movie_restart.issue_plex("http://plex.local:32400", "token", part)
        with self.assertRaises(ValueError):
            movie_restart.issue_plex(
                "http://plex.local:32400", "token\r\nX-Bad: yes", "/library/parts/123/file.mkv"
            )

    def test_custom_channel_bridge_uses_saved_server_without_exposing_it(self):
        servers = [{"id": "plex-one", "url": "http://plex.local:32400", "token": "secret"}]
        with patch.object(custom_channels, "read", return_value=servers):
            url = custom_channels.movie_restart_url("plex-one", "/library/parts/7/movie.mp4", "Movie")
        self.assertTrue(url.startswith("/guide/play/restart/"))
        self.assertNotIn("plex.local", url)
        self.assertNotIn("secret", url)

    def test_restart_route_streams_finite_media_with_private_plex_header(self):
        app = Flask(__name__)
        guide.register_guide_routes(app)
        source = {
            "target": "http://plex.local:32400/library/parts/7/movie.mp4",
            "input_headers": {"X-Plex-Token": "secret"},
            "title": "Movie",
        }
        with patch.object(movie_restart, "resolve", return_value=source), \
                patch("api.guide.browser.response_for", return_value="movie response") as response_for:
            response = app.test_client().get("/guide/play/restart/abcdefghijklmnopqrstuvwxyz123456")
        self.assertEqual(response.get_data(as_text=True), "movie response")
        response_for.assert_called_once_with(
            source["target"], input_headers=source["input_headers"], finite=True
        )

    def test_ffmpeg_places_private_headers_before_the_input(self):
        with patch("media.ffmpeg.executable", return_value="ffmpeg"), \
                patch("media.ffmpeg.media_pipeline.active_encoder", return_value="libx264"):
            args = ffmpeg.normalized_live_input_args(
                "http://plex.local/movie", input_headers={"X-Plex-Token": "secret"}
            )
        self.assertLess(args.index("-headers"), args.index("-i"))
        self.assertEqual(args[args.index("-headers") + 1], "X-Plex-Token: secret\r\n")

    def test_normal_movie_completion_is_not_reported_as_a_live_stream_failure(self):
        class Process:
            stdout = io.BytesIO(b"movie")
            stderr = None

        app = Flask(__name__)
        with app.test_request_context("/guide/play/restart/ticket"), \
                patch.object(browser.media_pipeline, "acquire_session", return_value="session"), \
                patch.object(browser.media_pipeline, "release_session"), \
                patch.object(browser.media_pipeline, "record_output_error") as record_error, \
                patch.object(browser.subprocess, "Popen", return_value=Process()), \
                patch.object(browser, "normalized_live_input_args", return_value=["ffmpeg"]), \
                patch.object(browser, "terminate"):
            response = browser.response_for("http://plex.local/movie", finite=True)
            self.assertEqual(b"".join(response.response), b"movie")
            self.assertEqual(response.headers["Content-Disposition"], 'inline; filename="movie.mp4"')
            response.close()
        record_error.assert_not_called()

    def test_built_in_guide_exposes_restart_and_return_to_live_controls(self):
        template = (ROOT / "templates" / "guide.html").read_text(encoding="utf-8")
        guide_js = (ROOT / "static" / "js" / "guide.js").read_text(encoding="utf-8")
        programmes = (ROOT / "static" / "js" / "guide_programmes.js").read_text(encoding="utf-8")
        dvr = (ROOT / "static" / "js" / "guide_dvr.js").read_text(encoding="utf-8")
        self.assertIn('id="guideRestartMovieBtn"', template)
        self.assertIn('id="guidePauseMovieBtn"', template)
        self.assertIn('id="guideBackToLiveBtn"', template)
        self.assertIn('id="guideProgrammeRestart"', template)
        self.assertIn("function playProgrammeFromBeginning", guide_js)
        self.assertIn("guideState.video.active = false", guide_js)
        self.assertIn("function backToLiveChannel", guide_js)
        self.assertIn("function toggleRestartMoviePause", guide_js)
        self.assertIn('guideEls.player.addEventListener("pause"', guide_js)
        self.assertIn("The live channel is still moving.", guide_js)
        self.assertIn("guideState.restart?.active", programmes)
        self.assertIn('el("guideProgrammeRestart").classList.toggle("d-none", !canRestart)', dvr)


if __name__ == "__main__":
    unittest.main()
