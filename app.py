#!/usr/bin/env python3
import argparse
import atexit
import os
from flask import Flask, render_template, send_from_directory

import hdhr_config
from api import register_routes
from api.hdhr_discovery import start_hdhr_discovery, stop_hdhr_discovery
from core import DEV_PORT, PORT
from settings import SETTINGS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = SETTINGS.max_upload_bytes


@app.get("/")
def index():
    html = render_template("index.html")
    html = html.replace(
        "</head>",
        '<link rel="stylesheet" href="/static/css/experiments_ui.css?v=ui-refactor-8">\n'
        '<link rel="stylesheet" href="/static/css/ui_refactor.css?v=ui-refactor-8">\n'
        '<link rel="stylesheet" href="/static/css/ui_themes.css?v=ui-refactor-8">\n'
        '<link rel="stylesheet" href="/static/css/ui_sections.css?v=ui-refactor-8">\n'
        '<link rel="stylesheet" href="/static/css/ui_provider_cleanup.css?v=ui-refactor-8">\n'
        '<link rel="stylesheet" href="/static/css/ui_top_controls.css?v=ui-refactor-8">\n</head>',
    )
    return html.replace(
        "</body>",
        '<script src="/static/js/experiments_ui.js?v=ui-refactor-8"></script>\n'
        '<script src="/static/js/ui_refactor.js?v=ui-refactor-8"></script>\n'
        '<script src="/static/js/ui_themes.js?v=ui-refactor-8"></script>\n'
        '<script src="/static/js/ui_sections.js?v=ui-refactor-8"></script>\n'
        '<script src="/static/js/hdhr_ui.js?v=hdhr-support-toggle-3"></script>\n'
        '<script src="/static/js/ui_top_controls.js?v=ui-refactor-8"></script>\n'
        '<script src="/static/js/ui_brand_meta.js?v=ui-refactor-8"></script>\n</body>',
    )


@app.get("/guide")
def guide():
    html = render_template("guide.html")
    html = html.replace(
        "</head>",
        '<link rel="stylesheet" href="/static/css/guide_programmes.css?v=now-next-1">\n</head>',
    )
    return html.replace(
        "</body>",
        '<script src="/static/js/guide_experiments_ui.js?v=port-10000"></script>\n'
        '<script src="/static/js/guide_programmes.js?v=now-next-1"></script>\n</body>',
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

    # In debug mode Werkzeug starts a reloader parent and then the real serving
    # child. Bind UDP 65001 only in the serving process so discovery has one
    # authoritative responder instead of two competing threads. The responder
    # also follows the persisted HDHomeRun support switch at runtime.
    serving_process = (not args.dev) or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if serving_process and hdhr_config.is_enabled() and start_hdhr_discovery():
        atexit.register(stop_hdhr_discovery)

    app.run(
        host="0.0.0.0",
        port=run_port,
        debug=args.dev,
        use_reloader=args.dev,
    )
