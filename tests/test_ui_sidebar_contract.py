from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ModernUiContractTests(unittest.TestCase):
    def test_app_loads_sidebar_assets_after_existing_ui_layers(self):
        app_text = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('/static/css/ui_sidebar.css?v=sidebar-1', app_text)
        self.assertIn('/static/js/ui_sidebar.js?v=sidebar-1', app_text)
        self.assertIn('/static/js/ui_jellyfin_settings.js?v=jellyfin-settings-1', app_text)
        self.assertGreater(
            app_text.index('/static/js/ui_sidebar.js?v=sidebar-1'),
            app_text.index('/static/js/ui_schedule_cleanup.js?v=schedule-cleanup-1'),
        )

    def test_sidebar_contains_required_navigation_and_status_metrics(self):
        script = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        for label in (
            "Overview",
            "Providers",
            "Channels",
            "EPG",
            "Sports Automation",
            "Devices",
            "Settings",
            "All Channels",
            "Indexed Channels",
            "Sports Channels",
            "Update Now",
            "Outputs",
            "Update status",
        ):
            self.assertIn(label, script)

    def test_jellyfin_cache_can_be_managed_after_onboarding(self):
        sidebar = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        settings = (ROOT / "static/js/ui_jellyfin_settings.js").read_text(encoding="utf-8")
        onboarding = (ROOT / "static/js/onboarding.js").read_text(encoding="utf-8")

        self.assertIn("Jellyfin Cache Cleanup", sidebar)
        self.assertIn("uiJellyfinCleanupEnabled", sidebar)
        self.assertIn('api("/api/jellyfin-cache"', settings)
        self.assertIn('api("/api/jellyfin-cache/validate"', settings)
        self.assertIn("Jellyfin Cache Directory", onboarding)

    def test_ui_status_exposes_sidebar_counts_and_output_health(self):
        source = (ROOT / "api/ui_status.py").read_text(encoding="utf-8")
        for key in (
            '"all_channels"',
            '"indexed_channels"',
            '"sports_channels"',
            '"active_streams"',
            '"master_update"',
            '"update"',
            '"outputs"',
        ):
            self.assertIn(key, source)
        self.assertIn('"M3U Publish"', source)
        self.assertIn('"Combined EPG"', source)

    def test_ui_status_routes_are_registered(self):
        routes = (ROOT / "api/routes.py").read_text(encoding="utf-8")
        self.assertIn("register_ui_status_routes", routes)
        self.assertIn("register_ui_status_routes(app)", routes)

    def test_saved_channels_remain_visible_when_sd_catalog_filter_is_enabled(self):
        script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        self.assertIn("const isSaved = selected.has(Number(channel.id));", script)
        self.assertIn("excludeSd && !(selectedOnly && isSaved)", script)
        self.assertIn("clearButton.disabled = !canRemove;", script)
        self.assertIn('clearButton.classList.toggle("btn-outline-danger", canRemove);', script)
        self.assertIn("const canRemove = removableVisible.length > 0;", script)
        self.assertIn("const removable = removableVisibleChannels();", script)


if __name__ == "__main__":
    unittest.main()
