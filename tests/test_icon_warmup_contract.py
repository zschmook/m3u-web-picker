from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IconWarmupContractTests(unittest.TestCase):
    def test_icon_python_sources_parse(self):
        for relative in ("api/images.py", "api/epg.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            ast.parse(source, filename=relative)

    def test_manual_icon_warmup_never_refreshes_schedule_api(self):
        source = (ROOT / "api/images.py").read_text(encoding="utf-8")
        self.assertIn("def warm_known_logos", source)
        self.assertIn("core.sports_provider_channel_sets()", source)
        self.assertIn('sports.catalog_payload(core.DB_PATH, scope_type="team")', source)
        self.assertNotIn("refresh_schedule_api", source)
        self.assertNotIn("schedule_api_requests", source)

    def test_master_update_only_runs_warmup_when_requested(self):
        source = (ROOT / "api/epg.py").read_text(encoding="utf-8")
        self.assertIn('update_logos = bool(data.get("logos"))', source)
        self.assertIn("image_api.prepare_icon_update()", source)
        self.assertIn("icon_update = image_api.warm_known_logos()", source)

    def test_temporary_ui_exposes_logos_checkbox_and_status(self):
        source = (ROOT / "static/js/ui_icon_update.js").read_text(encoding="utf-8")
        self.assertIn('const CHECKBOX_ID = "masterUpdateLogos"', source)
        self.assertIn("Logos?", source)
        self.assertIn("Icon update", source)
        self.assertIn("body.logos", source)
        self.assertIn("data.icon_update", source)

    def test_icon_update_overlay_is_loaded(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('/static/js/ui_icon_update.js?v=icon-update-1', source)


if __name__ == "__main__":
    unittest.main()
