#!/usr/bin/env python3
import argparse

from flask import Flask, render_template

from core import PORT
from api import register_routes

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


register_routes(app)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run M3U Web Picker")
    parser.add_argument(
        "-d",
        "--dev",
        action="store_true",
        help="Run on developer port 9998 instead of 9999",
    )
    args = parser.parse_args()

    run_port = 9998 if args.dev else PORT
    app.run(host="0.0.0.0", port=run_port, debug=False)
