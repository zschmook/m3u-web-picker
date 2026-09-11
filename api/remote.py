from __future__ import annotations

from flask import Response, jsonify, render_template, request, send_file

import core
import push_alerts
import sports
from guide_epg import enrich_guide_channels
from media import browser, director
from sports.generated import generated_publish_lock
from sports import remote_games
from . import guide as guide_api
from .http import no_cache


def _remote_payload(*, refresh_sports: bool = False) -> tuple[list[dict], list[dict], dict]:
    """Return both remote views and install every selectable server-side target."""
    with generated_publish_lock:
        items = [
            item
            for item in core.curated_channels_for_guide()
            if not bool(item.get("generated"))
            and str(item.get("play_url", "") or "").startswith("/guide/play/manual/")
            and not str(item.get("number", "") or "").startswith("0.")
        ]
        sports_settings = sports.get_settings(core.DB_PATH)
        items, epg_status = enrich_guide_channels(
            items,
            core.COMBINED_EPG_PATH,
            timezone_name=str(sports_settings.get("timezone", "America/New_York")),
        )
        generated_rows = sports.generated_rows(core.DB_PATH)

    catalog = []
    public_items = []
    for item in items:
        play_url = str(item.get("play_url", "") or "").split("?", 1)[0].strip()
        target = guide_api._resolve_guide_play_target(play_url)
        if not target:
            continue
        public = dict(item)
        public["id"] = play_url
        public_items.append(public)
        catalog.append({**public, "target": target})
    sports_rows = remote_games.primary_rows(generated_rows)
    sports_cards = remote_games.game_cards(
        sports_rows,
        timezone_name=str(sports_settings.get("timezone", "America/New_York")),
        refresh=refresh_sports,
    )
    if refresh_sports:
        sports_cards = remote_games.retain_recent_finals(
            core.DB_PATH,
            sports_cards,
            timezone_name=str(sports_settings.get("timezone", "America/New_York")),
        )
    playable_sports = {
        str(card.get("id") or "")
        for card in sports_cards
        if bool(card.get("selectable"))
    }
    sports_catalog = [
        {
            "id": f"/guide/play/sports/{int(row['assigned_number'])}",
            "number": str(int(row["assigned_number"])),
            "name": str(row.get("event_title") or row.get("display_name") or "Sports event"),
            "group": str(row.get("group_title") or "Sports Today"),
            "logo": str(row.get("tvg_logo") or ""),
            # Channel 0.2 is a selector, so use the provider feed itself rather
            # than routing a single game back through another generated stream.
            "target": str(row.get("url") or ""),
        }
        for row in sports_rows
        if f"/guide/play/sports/{int(row['assigned_number'])}" in playable_sports
    ]
    director.set_channel_catalog([*catalog, *sports_catalog])
    return public_items, sports_cards, epg_status


def _manual_channel_payload() -> tuple[list[dict], dict]:
    """Backward-compatible catalog warmup used by the stable 0.2 media route."""
    channels, _sports_cards, epg = _remote_payload(refresh_sports=False)
    return channels, epg


def _media_response(path, filename: str):
    is_playlist = filename == "stream.m3u8"
    response = (
        Response(director.public_manifest(path), mimetype="application/x-mpegurl")
        if is_playlist
        else send_file(path, mimetype="video/mp2t", conditional=True, etag=True)
    )
    response.headers["Cache-Control"] = (
        "no-cache, no-store, must-revalidate" if is_playlist else "public, max-age=30"
    )
    if is_playlist:
        response.headers.pop("ETag", None)
        response.headers.pop("Last-Modified", None)
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def register_remote_routes(app):
    @app.get("/remote")
    def remote_control():
        return render_template("remote.html")

    @app.get("/remote-sw.js")
    def remote_service_worker():
        response = app.send_static_file("js/remote-sw.js")
        response.mimetype = "application/javascript"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/api/remote/alerts")
    def remote_alert_status():
        try:
            return no_cache(jsonify(
                ok=True,
                public_key=push_alerts.public_key(),
                subscriptions=push_alerts.count(core.DB_PATH),
            ))
        except Exception as exc:
            return no_cache(jsonify(ok=False, error=f"Could not prepare phone alerts: {exc}")), 502

    @app.post("/api/remote/alerts/subscribe")
    def remote_alert_subscribe():
        try:
            data = request.get_json(force=True, silent=True) or {}
            subscription = data.get("subscription") if isinstance(data.get("subscription"), dict) else data
            saved = push_alerts.save(
                core.DB_PATH,
                subscription,
                user_agent=str(request.headers.get("User-Agent") or ""),
            )
            return no_cache(jsonify(ok=True, endpoint=saved["endpoint"]))
        except ValueError as exc:
            return no_cache(jsonify(ok=False, error=str(exc))), 400
        except Exception as exc:
            return no_cache(jsonify(ok=False, error=f"Could not enable phone alerts: {exc}")), 502

    @app.delete("/api/remote/alerts/subscribe")
    def remote_alert_unsubscribe():
        data = request.get_json(force=True, silent=True) or {}
        removed = push_alerts.remove(core.DB_PATH, str(data.get("endpoint") or ""))
        return no_cache(jsonify(ok=True, removed=removed))

    @app.post("/api/remote/alerts/test")
    def remote_alert_test():
        try:
            data = request.get_json(force=True, silent=True) or {}
            endpoint = str(data.get("endpoint") or "").strip()
            requested_id = str(data.get("channel_id") or "").strip()
            channels, _sports_cards, _epg = _remote_payload(refresh_sports=False)
            channel = next(
                (item for item in channels if str(item.get("id") or "") == requested_id),
                None,
            )
            if channel is None:
                channel = next(
                    (item for item in channels if "AMC" in str(item.get("name") or "").upper()),
                    channels[0] if channels else None,
                )
            if channel is None:
                raise ValueError("There is no enabled channel for the test alert.")
            push_alerts.send(
                core.DB_PATH,
                endpoint,
                {
                    "title": "Jurassic Park is on",
                    "body": f"{channel.get('name') or 'A movie channel'} · Play it on channel 0.2?",
                    "tag": "m3u-picker-movie-test",
                    "url": "/remote",
                    "channel_id": str(channel.get("id") or ""),
                    "channel_name": str(channel.get("name") or "Movie channel"),
                },
            )
            return no_cache(jsonify(ok=True, channel={
                "id": str(channel.get("id") or ""),
                "name": str(channel.get("name") or "Movie channel"),
            }))
        except ValueError as exc:
            return no_cache(jsonify(ok=False, error=str(exc))), 400
        except Exception as exc:
            return no_cache(jsonify(ok=False, error=f"Could not send the test alert: {exc}")), 502

    @app.route("/api/remote", methods=["GET", "POST", "DELETE"])
    def remote_state():
        try:
            director.expire_idle()
            refresh_sports = str(request.args.get("mode") or "").strip().lower() == "sports"
            channels, sports_cards, epg = _remote_payload(refresh_sports=refresh_sports)
            if request.method == "POST":
                data = request.get_json(force=True, silent=True) or {}
                channel_id = str(data.get("channel_id") or data.get("play_url") or "").strip()
                director.select_channel(channel_id)
                guide_api.schedule_director_roku_relaunch(director.state_payload().get("revision") or 0)
            elif request.method == "DELETE":
                director.stop()
                guide_api.schedule_director_roku_relaunch(director.state_payload().get("revision") or 0)

            state = director.state_payload()
            selected_id = str(state.get("selected_id", "") or "")
            state["selected"] = next(
                (
                    item
                    for item in [*channels, *sports_cards]
                    if str(item.get("id", "")) == selected_id
                ),
                state.get("selected"),
            )
            state.update(
                ok=True,
                count=len(channels),
                channels=channels,
                sports=sports_cards,
                sports_count=len(sports_cards),
                epg=epg,
            )
            return no_cache(jsonify(state))
        except ValueError as exc:
            return no_cache(jsonify(ok=False, error=str(exc))), 400
        except Exception as exc:
            return no_cache(jsonify(ok=False, error=f"Could not switch channel 0.2: {exc}")), 502

    @app.route("/director/<filename>", methods=["GET", "HEAD", "OPTIONS"])
    def remote_media(filename: str):
        if request.method == "OPTIONS":
            response = Response(status=204)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        try:
            if filename == "stream.m3u8" and not director.state_payload().get("ready"):
                _manual_channel_payload()
            path = director.safe_media_file(filename)
        except Exception as exc:
            return no_cache(jsonify(error=f"Could not start channel 0.2: {exc}")), 502
        if path is None:
            return no_cache(jsonify(error="Channel 0.2 media not found.")), 404
        return _media_response(path, filename)

    @app.get("/playlist/remote.m3u")
    def remote_playlist():
        response = Response(
            director.playlist(request.host_url.rstrip("/")),
            mimetype="audio/x-mpegurl",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get(director.PLAY_URL)
    def guide_play_remote():
        if director.safe_media_file("stream.m3u8") is None:
            raise RuntimeError("Remote channel stream is not ready.")
        # Follow the stable public playlist rather than one revision's private
        # directory. That lets an open browser player survive phone-controlled
        # A/B handoffs just like Roku and other HLS clients do.
        target = guide_api._resolve_guide_play_target(director.PLAY_URL)
        return browser.response_for(
            target,
            stream_copy=True,
            on_activity=director.touch_session,
        )
