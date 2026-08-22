#!/usr/bin/env python3
import argparse
import atexit
import os
from flask import Flask, redirect, render_template, request, send_from_directory

import core
import hdhr_config
import onboarding
import public_epg_compat
from api import register_routes
from api.hdhr_discovery import start_hdhr_discovery, stop_hdhr_discovery
from core import DEV_PORT, PORT
from settings import SETTINGS

public_epg_compat.install(core)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = SETTINGS.max_upload_bytes


@app.after_request
def disable_runtime_document_cache(response):
    """Never let navigation/status pages resurrect stale update state."""
    path = request.path
    if (
        path in {"/", "/guide", "/api/ui/status", "/api/master-update"}
        or path.startswith("/api/master-update/")
    ):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _provider_configured() -> bool:
    return bool(core.primary_provider_source() or core.source_mode == "file")


def _onboarding_required() -> bool:
    try:
        return onboarding.setup_required(
            core.DB_PATH,
            provider_configured=_provider_configured(),
        )
    except Exception as exc:
        print(f"Could not determine onboarding state: {exc}")
        return False


def _onboarding_initial_refresh_required() -> bool:
    try:
        state = onboarding.get_state(
            core.DB_PATH,
            provider_configured=_provider_configured(),
        )
        return onboarding.initial_refresh_required(state)
    except Exception as exc:
        print(f"Could not determine onboarding guide gate state: {exc}")
        return False


@app.get("/")
def index():
    html = render_template("index.html")
    if _onboarding_required():
        pending_classes = "onboarding-pending"
        if _onboarding_initial_refresh_required():
            pending_classes += " onboarding-initial-refresh-pending"
        html = html.replace(
            '<html lang="en">',
            f'<html lang="en" class="{pending_classes}">',
            1,
        )
    html = html.replace(
        "</head>",
        '<style>html.onboarding-pending body,html.onboarding-initial-refresh-pending body{visibility:hidden}</style>\n'
        '<link rel="stylesheet" href="/static/css/experiments_ui.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_refactor.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_themes.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_sections.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_provider_cleanup.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_top_controls.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_resilient_images.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_schedule_cleanup.css?v=schedule-cleanup-1">\n'
        '<link rel="stylesheet" href="/static/css/ui_sidebar.css?v=sidebar-1">\n'
        '<link rel="stylesheet" href="/static/css/ui_sidebar_brand_links.css?v=sidebar-brand-links-1">\n'
        '<link rel="stylesheet" href="/static/css/ui_sidebar_tweaks.css?v=sidebar-tweaks-4">\n'
        '<link rel="stylesheet" href="/static/css/update_lifecycle.css?v=update-lifecycle-1">\n'
        '<link rel="stylesheet" href="/static/css/onboarding.css?v=onboarding-1">\n</head>',
    )
    return html.replace(
        "</body>",
        '<script src="/static/js/experiments_ui.js?v=ui-refactor-9"></script>\n'
        '<script src="/static/js/ui_refactor.js?v=ui-refactor-9"></script>\n'
        '<script src="/static/js/ui_themes.js?v=themes-overview-1"></script>\n'
        '<script src="/static/js/ui_sections.js?v=ui-refactor-9"></script>\n'
        '<script src="/static/js/hdhr_ui.js?v=hdhr-support-toggle-3"></script>\n'
        '<script src="/static/js/ui_top_controls.js?v=ui-top-controls-10"></script>\n'
        '<script src="/static/js/ui_brand_meta.js?v=v30-brand-1"></script>\n'
        '<script src="/static/js/ui_resilient_images.js?v=logo-registry-1"></script>\n'
        '<script src="/static/js/ui_schedule_cleanup.js?v=schedule-cleanup-1"></script>\n'
        '<script src="/static/js/ui_sidebar.js?v=sidebar-1"></script>\n'
        '<script src="/static/js/ui_jellyfin_settings.js?v=jellyfin-settings-1"></script>\n'
        '<script src="/static/js/ui_sidebar_brand_links.js?v=sidebar-brand-links-1"></script>\n'
        '<script src="/static/js/update_lifecycle.js?v=update-lifecycle-1"></script>\n'
        '<script src="/static/js/ui_img_cache_status.js?v=espn-logo-cache-1"></script>\n'
        '<script src="/static/js/ui_epg_static.js?v=output-lan-1"></script>\n'
        '<script src="/static/js/ui_overview_update_status.js?v=overview-status-1"></script>\n'
        '<script src="/static/js/order_drag_drop.js?v=drag-order-1"></script>\n'
        '<script src="/static/js/onboarding.js?v=onboarding-1"></script>\n'
        '<script src="/static/js/onboarding_initial_refresh_gate.js?v=onboarding-guide-gate-1"></script>\n'
        '<script src="/static/js/onboarding_enhancements.js?v=onboarding-2"></script>\n'
        '<script src="/static/js/onboarding_provider_validation_v2.js?v=onboarding-4"></script>\n'
        '<script src="/static/js/onboarding_demo_provider.js?v=demo-provider-1"></script>\n'
        '<script src="/static/js/onboarding_manual_channels.js?v=onboarding-5"></script>\n</body>',
    )


@app.get("/guide")
def guide():
    if _onboarding_initial_refresh_required():
        return redirect("/")
    html = render_template("guide.html")
    html = html.replace(
        "</head>",
        '<link rel="stylesheet" href="/static/css/guide_programmes.css?v=matchup-1">\n'
        '<link rel="stylesheet" href="/static/css/event_logo_normalization.css?v=event-logo-2">\n'
        '<link rel="stylesheet" href="/static/css/ui_themes.css?v=theme-pack-2">\n'
        '<link rel="stylesheet" href="/static/css/ui_sidebar_tweaks.css?v=theme-pack-2">\n'
        '<link rel="stylesheet" href="/static/css/guide_theme.css?v=guide-theme-2">\n'
        '<link rel="stylesheet" href="/static/css/ui_resilient_images.css?v=ui-refactor-9">\n</head>',
    )
    return html.replace(
        "</body>",
        '<script>\n'
        '(() => {\n'
        '  const saved = localStorage.getItem("m3u-picker.ui.theme") || "midnight";\n'
        '  const valid = new Set(["midnight","slate","oled-black","carbon","light","ice","terminal-amber","terminal-green","cornfield","ketchup-mustard"]);\n'
        '  document.body.dataset.uiTheme = valid.has(saved) ? saved : "midnight";\n'
        '})();\n'
        '</script>\n'
        '<script src="/static/js/guide_experiments_ui.js?v=cast-flow-2"></script>\n'
        '<script src="/static/js/guide_programmes.js?v=matchup-1"></script>\n'
        '<script src="/static/js/guide_event_logo_bridge.js?v=event-logo-2"></script>\n'
        '<script src="/static/js/guide_roku_button.js?v=roku-button-2"></script>\n'
        '<script src="/static/js/ui_resilient_images.js?v=logo-registry-1"></script>\n'
        '<script src="/static/js/guide_back_button.js?v=guide-back-3"></script>\n</body>',
    )


@app.get("/user-guide")
def user_guide():
    return send_from_directory(
        app.root_path,
        "USER-GUIDE.md",
        mimetype="text/plain",
        as_attachment=False,
    )


register_routes(app)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run M3U Web Picker")
    parser.add_argument(
        "-d",
        "--dev",
        action="store_true",
        help="Run Flask debug mode on the developer port (default 9998).",
    )
    args = parser.parse_args()

    if args.dev:
        os.environ.setdefault("M3U_DEBUG_TOOLS", "true")

    run_port = DEV_PORT if args.dev else PORT

    serving_process = (not args.dev) or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if serving_process and hdhr_config.is_enabled() and start_hdhr_discovery():
        atexit.register(stop_hdhr_discovery)

    app.run(
        host="0.0.0.0",
        port=run_port,
        debug=args.dev,
        use_reloader=args.dev,
    )
