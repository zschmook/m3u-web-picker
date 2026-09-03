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

    def test_mobile_guide_collapses_channel_column_to_station_logos(self):
        programme_css = (ROOT / "static/css/guide_programmes.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 620px)", programme_css)
        self.assertIn("--guide-station-width: 72px;", programme_css)
        self.assertIn(".guide-station-number,\n  .guide-station-copy {\n    display: none;", programme_css)
        self.assertIn(".guide-station-cell {\n    justify-content: center;", programme_css)

    def test_local_player_exposes_picture_in_picture_popout(self):
        template = (ROOT / "templates/guide.html").read_text(encoding="utf-8")
        pip = (ROOT / "static/js/guide_pip.js").read_text(encoding="utf-8")

        self.assertIn('id="guidePopoutBtn"', template)
        self.assertIn('leavepictureinpicture', pip)
        self.assertIn('scrollIntoView', pip)
        self.assertIn('const playback = player.play()', pip)
        self.assertIn('playback.catch', pip)
        self.assertIn("player.requestPictureInPicture()", pip)
        self.assertIn("document.exitPictureInPicture()", pip)
        self.assertIn('"enterpictureinpicture"', pip)
        self.assertIn('"leavepictureinpicture"', pip)
        self.assertIn('webkitSupportsPresentationMode("picture-in-picture")', pip)
        self.assertIn('webkitSetPresentationMode("picture-in-picture")', pip)
        self.assertIn('"webkitpresentationmodechanged"', pip)
        self.assertIn('"canplay"', pip)

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

    def test_dvr_library_groups_saved_recordings_for_browser_playback(self):
        template = (ROOT / "templates/guide.html").read_text(encoding="utf-8")
        dvr_ui = (ROOT / "static/js/guide_dvr.js").read_text(encoding="utf-8")
        dvr_api = (ROOT / "api/dvr.py").read_text(encoding="utf-8")
        app = (ROOT / "src/app.py").read_text(encoding="utf-8")

        self.assertIn('data-dvr-view="upcoming">Upcoming &amp; Status</button>', template)
        self.assertIn('data-dvr-view="library">Library</button>', template)
        self.assertNotIn('class="guide-dvr-eyebrow">In-app recorder', template)
        self.assertIn('id="guideDvrStorage" class="small-muted d-none"', template)
        self.assertIn('id="guideDvrMessage" class="guide-dvr-message small-muted d-none"', template)
        self.assertIn('id="guideBrowseControls"', template)
        self.assertIn('id="guideBrowseList"', template)
        self.assertIn('id="guideDvrBtn" class="btn btn-outline-light btn-sm" type="button" aria-pressed="false"', template)
        self.assertIn('selectedDvrView: "upcoming"', dvr_ui)
        self.assertIn('const hidden = !message || state.selectedDvrView === "library"', dvr_ui)
        self.assertIn('setMessage("")', dvr_ui)
        self.assertIn('el("guideBrowseControls")?.classList.toggle("d-none", state.panelOpen)', dvr_ui)
        self.assertIn('el("guideBrowseList")?.classList.toggle("d-none", state.panelOpen)', dvr_ui)
        self.assertIn('el("guideDvrBtn")?.classList.toggle("btn-primary", state.panelOpen)', dvr_ui)
        self.assertIn('el("guideDvrBtn")?.setAttribute("aria-pressed", String(state.panelOpen))', dvr_ui)
        self.assertIn('status.key === "ready" && Boolean(item.playback_url)', dvr_ui)
        self.assertIn('class="guide-dvr-library-group"', dvr_ui)
        self.assertIn("recordingTimestamp(right) - recordingTimestamp(left)", dvr_ui)
        self.assertIn('class="guide-dvr-item guide-dvr-series-item', dvr_ui)
        self.assertIn('summary class="guide-dvr-series-select"', dvr_ui)
        self.assertIn('`Next episode: ${rangeText(next)}`', dvr_ui)
        self.assertNotIn('escape(rule.channel_name || rule.tvg_id)', dvr_ui)
        self.assertIn('data-dvr-play="${item.id}"', dvr_ui)
        self.assertIn("browser.response_for(", dvr_api)
        self.assertIn('guide_dvr.css?v=dvr-library-7', app)
        self.assertIn('guide_dvr.js?v=dvr-library-7', app)

if __name__ == "__main__":
    unittest.main()
