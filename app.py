#!/usr/bin/env python3
import argparse
import os

from flask import Flask, render_template, send_from_directory

from api import register_routes
from core import DEV_PORT, PORT

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("M3U_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/guide")
def guide():
    return render_template("guide.html")


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
    app.run(
        host="0.0.0.0",
        port=run_port,
        debug=args.dev,
        use_reloader=args.dev,
    )
