from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GuideRemotePlaybackContractTests(unittest.TestCase):
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
