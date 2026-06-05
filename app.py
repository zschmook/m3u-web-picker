#!/usr/bin/env python3
from flask import Flask, render_template

from core import PORT
from api import register_routes

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


register_routes(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
