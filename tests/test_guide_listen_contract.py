from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GuideListenContractTests(unittest.TestCase):
    def test_guide_exposes_audio_only_listen_mode(self):
        template = (ROOT / "templates/guide.html").read_text(encoding="utf-8")
        guide = (ROOT / "static/js/guide.js").read_text(encoding="utf-8")
        programmes = (ROOT / "static/js/guide_programmes.js").read_text(encoding="utf-8")
        dvr = (ROOT / "static/js/guide_dvr.js").read_text(encoding="utf-8")
        guide_api = (ROOT / "api/guide.py").read_text(encoding="utf-8")

        self.assertIn('id="guideProgrammeListen"', template)
        self.assertIn('id="guideAudioPlayer"', template)
        self.assertIn("function startListenMode(channel)", guide)
        self.assertNotIn("async function startListenMode(channel)", guide)
        self.assertIn("/guide/listen?play_url=", guide)
        self.assertIn('data-guide-listen="true"', programmes)
        self.assertIn('data-guide-listen-action="${isListening ? "stop" : "start"}"', programmes)
        self.assertIn('${isListening ? "Stop" : "Listen"}', programmes)
        self.assertIn('el("guideProgrammeListen").classList.toggle("d-none", !current)', dvr)
        self.assertIn("browser.response_for(target, audio_only=True)", guide_api)

    def test_mobile_media_card_uses_show_and_channel_metadata(self):
        guide = (ROOT / "static/js/guide.js").read_text(encoding="utf-8")

        self.assertIn("new MediaMetadata({", guide)
        self.assertIn("title: programmeTitle || channelName", guide)
        self.assertIn('artist: programmeTitle ? channelName : "M3U Web Picker"', guide)
        self.assertIn('setListenMediaAction("play"', guide)
        self.assertIn('setListenMediaAction("pause"', guide)
        self.assertIn('setListenMediaAction("stop"', guide)

    def test_listen_and_stop_share_one_fixed_button_footprint(self):
        programmes = (ROOT / "static/js/guide_programmes.js").read_text(encoding="utf-8")
        css = (ROOT / "static/css/guide_programmes.css").read_text(encoding="utf-8")

        self.assertIn("guide-station-listen", programmes)
        self.assertIn(".guide-station-listen {\n  width: 47px;", css)

    def test_documentation_calls_out_ffmpeg_requirement(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        user_guide = (ROOT / "docs/USER-GUIDE.md").read_text(encoding="utf-8")

        self.assertIn("Listen mode requires FFmpeg", readme)
        self.assertIn("Listen** requires FFmpeg", user_guide)


if __name__ == "__main__":
    unittest.main()
