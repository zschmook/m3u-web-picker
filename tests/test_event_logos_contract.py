from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EventLogoContractTests(unittest.TestCase):
    def test_event_logo_python_sources_parse(self):
        for relative in ("event_logos.py", "api/event_images.py", "sports/feeds.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            ast.parse(source, filename=relative)

    def test_matchup_logo_never_refreshes_schedule_api(self):
        source = (ROOT / "event_logos.py").read_text(encoding="utf-8")
        self.assertNotIn("refresh_schedule_api", source)
        self.assertNotIn("schedule_api_requests", source)
        self.assertIn("logo_registry.lookup", source)
        self.assertIn("urllib.request.urlopen", source)

    def test_all_generated_matchup_feeds_use_event_logo(self):
        source = (ROOT / "sports" / "feeds.py").read_text(encoding="utf-8")
        self.assertIn("event_logos.register_matchup_logo", source)
        self.assertIn("if away_team_id and home_team_id and event.get(\"event_key\")", source)
        self.assertNotIn('feed_type not in {"home", "away"}', source)
        self.assertIn("away_team_id", source)
        self.assertIn("home_team_id", source)

    def test_event_logo_is_stable_by_logical_event_and_teams(self):
        source = (ROOT / "event_logos.py").read_text(encoding="utf-8")
        self.assertIn("def event_digest", source)
        self.assertIn('str(event_key or "").strip()', source)
        self.assertIn('str(away_team_id or "").strip().casefold()', source)
        self.assertIn('str(home_team_id or "").strip().casefold()', source)
        self.assertNotIn("assigned_number", source)

    def test_compositor_trims_padding_and_preserves_aspect_ratio(self):
        source = (ROOT / "event_logos.py").read_text(encoding="utf-8")
        self.assertIn('alpha = source.getchannel("A")', source)
        self.assertIn("bbox = alpha.getbbox()", source)
        self.assertIn("min(inner / source.width, inner / source.height)", source)
        self.assertIn("Image.Resampling.LANCZOS", source)

    def test_event_logo_route_and_pillow_dependency_exist(self):
        routes = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
        api_source = (ROOT / "api" / "event_images.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("register_event_image_routes", routes)
        self.assertIn('/api/event-logo/<digest>.png', api_source)
        self.assertIn("Pillow", requirements)

    def test_event_logo_urls_bust_old_guide_art_and_are_not_browser_pinned(self):
        logo_source = (ROOT / "event_logos.py").read_text(encoding="utf-8")
        api_source = (ROOT / "api" / "event_images.py").read_text(encoding="utf-8")
        self.assertIn("EVENT_LOGO_URL_VERSION", logo_source)
        self.assertIn(".png?v={EVENT_LOGO_URL_VERSION}", logo_source)
        self.assertIn("no-cache, no-store, must-revalidate", api_source)

    def test_guide_uses_same_sized_logo_boxes(self):
        source = (ROOT / "static" / "css" / "event_logo_normalization.css").read_text(encoding="utf-8")
        self.assertIn(".guide-logo", source)
        self.assertIn(".guide-matchup-logo", source)
        self.assertGreaterEqual(source.count("36px"), 6)


if __name__ == "__main__":
    unittest.main()
