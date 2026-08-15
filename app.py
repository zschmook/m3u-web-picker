#!/usr/bin/env python3
import argparse
import atexit
import os
from flask import Flask, render_template, send_from_directory

import core
import hdhr_config
import onboarding
from api import register_routes
from api.hdhr_discovery import start_hdhr_discovery, stop_hdhr_discovery
from core import DEV_PORT, PORT
from settings import SETTINGS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = SETTINGS.max_upload_bytes


def _dev_onboarding_required() -> bool:
    try:
        return onboarding.setup_required(
            core.DB_PATH,
            provider_configured=bool(core.primary_provider_source() or core.source_mode == "file"),
        )
    except Exception as exc:
        print(f"Could not determine dev onboarding state: {exc}")
        return False


@app.get("/")
def index():
    html = render_template("index.html")
    if _dev_onboarding_required():
        html = html.replace(
            '<html lang="en">',
            '<html lang="en" class="onboarding-pending">',
            1,
        )
    html = html.replace(
        "</head>",
        '<style>html.onboarding-pending body{visibility:hidden}</style>\n'
        '<link rel="stylesheet" href="/static/css/experiments_ui.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_refactor.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_themes.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_sections.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_provider_cleanup.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_top_controls.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_resilient_images.css?v=ui-refactor-9">\n'
        '<link rel="stylesheet" href="/static/css/ui_schedule_cleanup.css?v=schedule-cleanup-1">\n'
        '<link rel="stylesheet" href="/static/css/ui_sidebar.css?v=sidebar-1">\n'
        '<link rel="stylesheet" href="/static/css/ui_sidebar_tweaks.css?v=sidebar-tweaks-4">\n'
        '<link rel="stylesheet" href="/static/css/onboarding_dev.css?v=dev-onboarding-1">\n</head>',
    )
    return html.replace(
        "</body>",
        '<script src="/static/js/experiments_ui.js?v=ui-refactor-9"></script>\n'
        '<script src="/static/js/ui_refactor.js?v=ui-refactor-9"></script>\n'
        '<script src="/static/js/ui_themes.js?v=themes-overview-1"></script>\n'
        '<script src="/static/js/ui_sections.js?v=ui-refactor-9"></script>\n'
        '<script src="/static/js/hdhr_ui.js?v=hdhr-support-toggle-3"></script>\n'
        '<script src="/static/js/ui_top_controls.js?v=ui-top-controls-10"></script>\n'
        '<script src="/static/js/ui_brand_meta.js?v=ui-refactor-9"></script>\n'
        '<script src="/static/js/ui_resilient_images.js?v=logo-registry-1"></script>\n'
        '<script src="/static/js/ui_schedule_cleanup.js?v=schedule-cleanup-1"></script>\n'
        '<script src="/static/js/ui_sidebar.js?v=sidebar-1"></script>\n'
        '<script src="/static/js/ui_img_cache_status.js?v=espn-logo-cache-1"></script>\n'
        '<script src="/static/js/ui_epg_static.js?v=epg-static-1"></script>\n'
        '<script src="/static/js/ui_overview_update_status.js?v=overview-status-1"></script>\n'
        '<script src="/static/js/order_drag_drop.js?v=drag-order-1"></script>\n'
        '<script src="/static/js/onboarding_dev.js?v=dev-onboarding-1"></script>\n</body>',
    )


@app.get("/guide")
def guide():
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
        "M3U-Web-Picker-v22.1-RC5-User-Guide.pdf",
        mimetype="application/pdf",
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
