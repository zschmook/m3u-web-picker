from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ModernUiContractTests(unittest.TestCase):
    def test_app_loads_sidebar_assets_after_existing_ui_layers(self):
        app_text = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        self.assertIn('/static/css/ui_sidebar.css?v=', app_text)
        self.assertIn('/static/js/ui_sidebar.js?v=', app_text)
        self.assertIn('/static/js/ui_jellyfin_settings.js?v=jellyfin-settings-1', app_text)
        self.assertIn('/static/js/ui_network_settings.js?v=network-settings-1', app_text)
        self.assertGreater(
            app_text.index('/static/js/ui_sidebar.js?v='),
            app_text.index('/static/js/ui_schedule_cleanup.js?v=schedule-cleanup-1'),
        )

    def test_modern_shell_is_server_rendered_instead_of_injected(self):
        app_text = (ROOT / "app.py").read_text(encoding="utf-8")
        template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        self.assertNotIn('render_template("index.html")\n    html = html.replace', app_text)
        self.assertIn('id="uiAppShell"', template)
        self.assertIn('id="uiModernRoot"', template)
        self.assertIn("{% include '_modern_sidebar.html' %}", template)

    def test_sidebar_contains_required_navigation_and_status_metrics(self):
        script = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        sidebar_template = (ROOT / "templates/_modern_sidebar.html").read_text(encoding="utf-8")
        source = sidebar_template + script
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
            self.assertIn(label, source)

    def test_documentation_links_live_on_overview_not_sidebar(self):
        sidebar = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        sidebar_template = (ROOT / "templates/_modern_sidebar.html").read_text(encoding="utf-8")
        brand_links = (ROOT / "static/js/ui_sidebar_brand_links.js").read_text(encoding="utf-8")
        sidebar_links = sidebar_template[sidebar_template.index('<div class="ui-side-links">'):sidebar_template.index("</div>", sidebar_template.index('<div class="ui-side-links">'))]
        self.assertNotIn("User Guide", sidebar_links)
        self.assertNotIn("GitHub", sidebar_links)
        self.assertIn("ui-overview-resource-links", sidebar)
        self.assertIn('href="/user-guide"', sidebar)
        self.assertIn("https://github.com/zschmook/m3u-web-picker", sidebar)
        self.assertNotIn("uiGithubLink", brand_links)

    def test_jellyfin_cache_can_be_managed_after_onboarding(self):
        sidebar = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        sidebar += (ROOT / "templates/_modern_sidebar.html").read_text(encoding="utf-8")
        settings = (ROOT / "static/js/ui_jellyfin_settings.js").read_text(encoding="utf-8")
        onboarding = (ROOT / "static/js/onboarding.js").read_text(encoding="utf-8")

        self.assertIn("Jellyfin Cache Cleanup", sidebar)
        self.assertIn("uiJellyfinCleanupEnabled", sidebar)
        self.assertIn('api("/api/jellyfin-cache"', settings)
        self.assertIn('api("/api/jellyfin-cache/validate"', settings)
        self.assertIn('window.addEventListener("pageshow", load)', settings)
        self.assertIn('data-settings-panel="jellyfin"', settings)
        self.assertIn('? "Unavailable" : "Disabled"', settings)
        self.assertIn('el("uiJellyfinUsing").disabled = !mountConfigured', settings)
        self.assertIn('cleanup.disabled = busy || !mountConfigured || !usingJellyfin || !acknowledged', settings)
        self.assertIn('saveButton.disabled = busy || !mountConfigured || !acknowledged', settings)
        self.assertIn('el("uiJellyfinAcknowledge")?.addEventListener("change", syncDependencies)', settings)
        self.assertIn('if (cleanup && (!usingJellyfin || !acknowledged)) cleanup.checked = false;', settings)
        self.assertIn('id="uiJellyfinUsing" type="checkbox" role="switch" autocomplete="off"', sidebar)
        self.assertIn("Jellyfin Cache Directory", onboarding)

    def test_public_url_port_can_be_managed_in_settings(self):
        sidebar = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        sidebar += (ROOT / "templates/_modern_sidebar.html").read_text(encoding="utf-8")
        settings = (ROOT / "static/js/ui_network_settings.js").read_text(encoding="utf-8")
        self.assertIn("Public URL port", sidebar)
        self.assertIn("uiNetworkPort", sidebar)
        self.assertIn('/api/network-config', settings)

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
        self.assertIn('"active_streams": active_ffmpeg_streams', source)
        self.assertIn('playback = media_pipeline.status()', source)

    def test_ui_status_routes_are_registered(self):
        routes = (ROOT / "api/routes.py").read_text(encoding="utf-8")
        self.assertIn("register_ui_status_routes", routes)
        self.assertIn("register_ui_status_routes(app)", routes)

    def test_ui_status_returns_cached_json_during_database_publish_lock(self):
        source = (ROOT / "api/ui_status.py").read_text(encoding="utf-8")
        self.assertIn("except sqlite3.OperationalError as exc:", source)
        self.assertIn('if "locked" not in str(exc).lower():', source)
        self.assertIn('label="Update in progress"', source)
        self.assertIn('stale=True', source)

    def test_saved_channels_remain_visible_when_sd_catalog_filter_is_enabled(self):
        script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        self.assertIn("const isSaved = selected.has(Number(channel.id));", script)
        self.assertIn("excludeSd && !(selectedOnly && isSaved)", script)
        self.assertIn("clearButton.disabled = !canRemove;", script)
        self.assertIn('clearButton.classList.toggle("btn-outline-danger", canRemove);', script)
        self.assertIn("const canRemove = removableVisible.length > 0;", script)
        self.assertIn("const removable = removableVisibleChannels();", script)

    def test_commercial_preview_uses_browser_playback_not_debug_routes(self):
        script = (ROOT / "static/js/ui_commercial_test.js").read_text(encoding="utf-8")
        template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        self.assertIn("/guide/play/manual/", script)
        self.assertIn("/guide/play/sports/", script)
        self.assertNotIn("/guide/debug/ts/", script)
        self.assertIn('addEventListener("error"', script)
        sidebar = (ROOT / "static/js/ui_sidebar.js").read_text(encoding="utf-8")
        self.assertIn('id="uiCommercialTestVideo" controls autoplay playsinline', sidebar)
        self.assertNotIn('id="uiCommercialTestVideo" muted', sidebar)
        self.assertIn("video.muted = false;", script)
        self.assertIn("video.muted = true;", script)
        self.assertIn("ui_commercial_test.js?v=commercial-test-10", template)
        self.assertIn("ui_sidebar.js?v=sidebar-8", template)


if __name__ == "__main__":
    unittest.main()
