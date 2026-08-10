#!/usr/bin/env python3
import argparse
import threading

from flask import Flask, render_template, send_from_directory

import sports
from sports import scan as sports_scan
from api import register_routes
from core import DEV_PORT, PORT
from settings import SETTINGS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = SETTINGS.max_upload_bytes


# Experimental live match progress. Keep the expensive sports pipeline intact;
# only publish lightweight progress while rule matching walks the logical events.
_sports_match_progress = threading.local()
_original_matching_rules = sports._matching_rules
_original_select_events = sports_scan._select_events


def _matching_rules_with_progress(event, rule_index):
    matched = _original_matching_rules(event, rule_index)
    progress = getattr(_sports_match_progress, "value", None)
    if progress is not None:
        progress["processed"] += 1
        if matched:
            progress["matched"] += 1
        processed = progress["processed"]
        total = progress["total"]
        if processed == total or processed % 10 == 0:
            sports.update_scan_stage(
                progress["db_path"],
                f"{progress['matched']} of {total} sports matched",
            )
    return matched


def _select_events_with_progress(ctx):
    total = len(ctx.events)
    _sports_match_progress.value = {
        "db_path": ctx.db_path,
        "processed": 0,
        "matched": 0,
        "total": total,
    }
    sports.update_scan_stage(ctx.db_path, f"0 of {total} sports matched")
    try:
        _original_select_events(ctx)
        sports.update_scan_stage(
            ctx.db_path,
            f"{len(ctx.selected_events)} of {total} sports matched",
        )
    finally:
        if hasattr(_sports_match_progress, "value"):
            del _sports_match_progress.value


sports._matching_rules = _matching_rules_with_progress
sports_scan._select_events = _select_events_with_progress


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

    // While an update runs, the header is intentionally one line only:
    // "XX of YY sports matched". Timing remains in Master Update on the right.
    const originalRenderSportsScanStatus = renderSportsScanStatus;
    renderSportsScanStatus = function() {
      originalRenderSportsScanStatus();
      const scan = sportsState.scan || {running: false};
      const running = Boolean(masterUpdateBusy || masterUpdateState.running || scan.running);
      if (!running) return;

      const heading = status.querySelector(".sports-scan-status-heading");
      const title = document.getElementById("sportsScanStatusTitle");
      const details = document.getElementById("sportsScanStatusDetails");
      const spinner = document.getElementById("sportsScanStatusSpinner");
      if (heading) heading.style.marginBottom = "0";
      if (spinner) spinner.classList.add("d-none");
      if (title) title.textContent = scan.stage || "0 of 0 sports matched";
      if (details) details.classList.add("d-none");
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
