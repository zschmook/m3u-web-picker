from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UserGuideContractTests(unittest.TestCase):
    def test_user_guide_is_rendered_as_html(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "user_guide.html").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("import markdown", app_source)
        self.assertIn('markdown.markdown(', app_source)
        self.assertIn('render_template("user_guide.html"', app_source)
        self.assertIn('"/user-guide"', app_source)
        self.assertIn("{{ guide_content }}", template)
        self.assertNotIn("cdn.jsdelivr.net", template)
        self.assertIn("Markdown>=3.6,<4", requirements)

    def test_user_guide_tracks_current_runtime_documentation(self):
        guide = (ROOT / "docs" / "USER-GUIDE.md").read_text(encoding="utf-8")

        for current_detail in (
            "current v31 application",
            "scripts/docker-setup.sh",
            "built-in free public M3U demo",
            "Hide SD / Low Bandwidth Channels",
            "/playlist/channels.direct.m3u",
            "GPU passthrough is not supported for Docker installs on macOS",
            "M3U_JELLYFIN_CACHE_DIR",
        ):
            self.assertIn(current_detail, guide)
        self.assertNotIn("current v30 application", guide)


if __name__ == "__main__":
    unittest.main()
