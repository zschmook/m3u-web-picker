#!/usr/bin/env python3
import argparse
import atexit
import os
from pathlib import Path

import markdown
from flask import Flask, redirect, render_template, request
from markupsafe import Markup

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

_ONBOARDING_DEMO_PROVIDER_SCRIPT = "/static/js/onboarding_demo_provider.js?v=demo-provider-1"


@app.after_request
def disable_runtime_document_cache(response):
    """Never let navigation/status pages resurrect stale update state."""
    path = request.path
    if (
        path in {"/", "/guide", "/user-guide", "/api/ui/status", "/api/master-update"}
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
    page_classes = ""
    if _onboarding_required():
        page_classes = "onboarding-pending"
        if _onboarding_initial_refresh_required():
            page_classes += " onboarding-initial-refresh-pending"
    return render_template("index.html", page_classes=page_classes)


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
        '<script src="/static/js/guide_cast_ui.js?v=cast-flow-3"></script>\n'
        '<script src="/static/js/guide_programmes.js?v=matchup-1"></script>\n'
        '<script src="/static/js/guide_event_logo_bridge.js?v=event-logo-2"></script>\n'
        '<script src="/static/js/guide_roku_button.js?v=roku-button-2"></script>\n'
        '<script src="/static/js/ui_resilient_images.js?v=logo-registry-1"></script>\n'
        '<script src="/static/js/guide_back_button.js?v=guide-back-3"></script>\n</body>',
    )


@app.get("/user-guide")
def user_guide():
    source = Path(app.root_path, "USER-GUIDE.md").read_text(encoding="utf-8")
    content = markdown.markdown(
        source,
        extensions=("fenced_code", "tables", "toc", "sane_lists"),
        output_format="html5",
    )
    return render_template("user_guide.html", guide_content=Markup(content))


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
