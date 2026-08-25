from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UpdateLifecycleContractTests(unittest.TestCase):
    def test_changed_python_sources_parse(self):
        for relative in (
            "app.py",
            "api/epg.py",
            "api/onboarding.py",
            "api/ui_status.py",
            "master_update_worker.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            ast.parse(source, filename=relative)

    def test_main_ui_loads_live_update_lifecycle_after_sidebar(self):
        template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("update_lifecycle.css?v=update-lifecycle-1", template)
        self.assertIn("update_lifecycle.js?v=update-lifecycle-1", template)
        self.assertLess(
            template.index("ui_sidebar.js?v="),
            template.index("update_lifecycle.js?v=update-lifecycle-1"),
        )
        for uncached_path in ('"/"', '"/guide"', '"/user-guide"', '"/api/ui/status"', '"/api/master-update"'):
            self.assertIn(uncached_path, app_source)
        self.assertIn('response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"', app_source)

    def test_browser_uses_server_state_for_update_and_guide_lock(self):
        source = (ROOT / "static" / "js" / "update_lifecycle.js").read_text(encoding="utf-8")
        self.assertIn('/api/master-update?_=${Date.now()}', source)
        self.assertIn('cache: "no-store"', source)
        self.assertIn("#masterUpdateNowBtn, #uiUpdateNowBtn", source)
        self.assertIn('pathname === "/guide"', source)
        self.assertIn('setAttribute("aria-disabled", "true")', source)
        self.assertIn("renderSidebarRunning", source)
        self.assertIn("fetchFinalUiStatus", source)
        self.assertIn("Update in progress", source)

    def test_status_api_and_request_server_are_nonblocking_ready(self):
        status_source = (ROOT / "api" / "ui_status.py").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("master_update_worker.payload()", status_source)
        self.assertIn('response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"', status_source)
        self.assertIn('"--threads=8"', dockerfile)


if __name__ == "__main__":
    unittest.main()
