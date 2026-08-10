#!/usr/bin/env python3
import argparse
from flask import Flask, render_template, send_from_directory

from api import register_routes
from core import DEV_PORT, PORT
from settings import SETTINGS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = SETTINGS.max_upload_bytes


@app.get("/")
def index():
    html = render_template("index.html")
    return html.replace(
        "</body>",
        """
<script>
  (() => {
    const brand = document.querySelector(".app-brand-block");
    const status = document.getElementById("sportsScanStatus");
    if (!brand || !status) return;
    brand.style.flex = "1 1 620px";
    status.classList.remove("mb-3");
    status.classList.add("mt-4");
    status.style.width = "100%";
    brand.appendChild(status);

    // The sports status panel reports scan/matching progress only. The master
    // update controls on the right already own the elapsed-time counter.
    const originalRenderSportsScanStatus = renderSportsScanStatus;
    renderSportsScanStatus = function() {
      originalRenderSportsScanStatus();
      const scan = sportsState.scan || {running: false};
      const running = Boolean(masterUpdateBusy || masterUpdateState.running || scan.running);
      if (!running) return;
      const details = document.getElementById("sportsScanStatusDetails");
      if (!details) return;
      details.textContent = details.textContent
        .split(" • ")
        .filter(part => !/^Elapsed\b/i.test(part.trim()))
        .join(" • ");
    };
  })();
</script>
</body>""",
    )


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
