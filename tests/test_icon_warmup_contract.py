from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LogoUpdateContractTests(unittest.TestCase):
    def test_logo_python_sources_parse(self):
        for relative in ("api/images.py", "api/epg.py", "espn_team_logos.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            ast.parse(source, filename=relative)

    def test_master_update_has_no_manual_logo_toggle(self):
        source = (ROOT / "api" / "epg.py").read_text(encoding="utf-8")
        self.assertNotIn('data.get("logos")', source)
        self.assertNotIn("prepare_icon_update", source)
        self.assertNotIn("warm_known_logos", source)
        self.assertNotIn("icon_update", source)

    def test_logos_checkbox_overlay_is_not_loaded(self):
        source = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("ui_icon_update.js", source)
        self.assertIn("ui_img_cache_status.js", source)

    def test_generated_logos_use_automatic_espn_lookup(self):
        source = (ROOT / "sports" / "feeds.py").read_text(encoding="utf-8")
        resolver = (ROOT / "espn_team_logos.py").read_text(encoding="utf-8")
        self.assertIn("espn_team_logos.espn_full_default_url", source)
        self.assertIn('event.get("sport_id") or ""', source)
        self.assertIn("_available_espn_sports", resolver)
        self.assertIn("_league_slugs_for_sport", resolver)
        self.assertIn("_dynamic_candidates", resolver)


if __name__ == "__main__":
    unittest.main()
