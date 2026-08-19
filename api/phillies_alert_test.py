from __future__ import annotations

from flask import jsonify, request

import core
from sports import phillies_alert_control
from .http import no_cache


SCRIPT_TAG = '<script src="/static/js/ui_phillies_alert_test.js?v=phanatic-score-1"></script>'


def register_phillies_alert_test_routes(app):
    @app.post("/api/sports/mlb-score-alerts/3/phillies-test")
    def phillies_score_alert_test():
        try:
            return no_cache(jsonify(phillies_alert_control.trigger_current_score(core.DB_PATH)))
        except RuntimeError as exc:
            return no_cache(jsonify(error=str(exc))), 409
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not trigger Phillies score alert: {exc}")), 502

    @app.after_request
    def inject_phillies_alert_test_ui(response):
        if (
            request.path == "/"
            and response.status_code == 200
            and response.mimetype == "text/html"
        ):
            text = response.get_data(as_text=True)
            if SCRIPT_TAG not in text and "</body>" in text:
                response.set_data(text.replace("</body>", f"{SCRIPT_TAG}\n</body>", 1))
                response.headers.pop("Content-Length", None)
        return response
