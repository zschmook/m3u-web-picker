from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GuideRemotePlaybackContractTests(unittest.TestCase):
    def test_guide_exposes_five_day_navigation_without_expanding_one_timeline(self):
        template = (ROOT / "templates/guide.html").read_text(encoding="utf-8")
        programmes = (ROOT / "static/js/guide_programmes.js").read_text(encoding="utf-8")
        guide_css = (ROOT / "static/css/guide.css").read_text(encoding="utf-8")
        programme_css = (ROOT / "static/css/guide_programmes.css").read_text(encoding="utf-8")

        self.assertIn('id="guideDayNav"', template)
        self.assertIn('Search Guide <span id="guideVisibleCount"', template)
        self.assertIn("const GUIDE_COVERAGE_DAYS = 5;", programmes)
        self.assertIn("const GUIDE_WINDOW_HOURS = 8;", programmes)
        self.assertIn("const GUIDE_DAY_WINDOW_HOURS = 24;", programmes)
        self.assertIn("const futureDaySelected = daySelected && selectedGuideDay > 0;", programmes)
        self.assertIn("individualProgrammeSearchText(programme).includes(query)", programmes)
        self.assertIn('" is-search-match"', programmes)
        self.assertIn('["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]', programmes)
        self.assertIn('`(${visible.length.toLocaleString()} Channel', programmes)
        self.assertIn('guideDayNav?.addEventListener("click"', programmes)
        self.assertIn("if (wrap) wrap.scrollLeft = 0;", programmes)
        self.assertIn(".guide-controls {\n  width: 100%;\n}", guide_css)
        self.assertIn(".guide-controls {\n  width: 100%;\n}", programme_css)

    def test_local_player_exposes_picture_in_picture_popout(self):
        template = (ROOT / "templates/guide.html").read_text(encoding="utf-8")
        pip = (ROOT / "static/js/guide_pip.js").read_text(encoding="utf-8")

        self.assertIn('id="guidePopoutBtn"', template)
        self.assertIn('leavepictureinpicture', pip)
        self.assertIn('scrollIntoView', pip)
        self.assertIn("player.requestPictureInPicture()", pip)
        self.assertIn("document.exitPictureInPicture()", pip)
        self.assertIn('"enterpictureinpicture"', pip)
        self.assertIn('"leavepictureinpicture"', pip)

    def test_external_cast_disconnect_resumes_local_without_interrupting_roku(self):
        template = (ROOT / "templates/guide.html").read_text(encoding="utf-8")
        guide = (ROOT / "static/js/guide.js").read_text(encoding="utf-8")

        self.assertIn("resumeLocalAfterExternalCastDisconnect", template)
        self.assertIn("remoteTransitionBusy || guideState.roku.active", template)
        self.assertIn("playLocalChannel(channel)", template)
        self.assertLess(
            template.index("if (currentCastSession()) return;", template.index("function finishCastDisconnectHandoff")),
            template.index("castDisconnectHandoff = null;", template.index("function finishCastDisconnectHandoff")),
        )
        self.assertIn("if (guideState.roku.active)", guide)
        self.assertIn("playback continues", guide)

if __name__ == "__main__":
    unittest.main()
