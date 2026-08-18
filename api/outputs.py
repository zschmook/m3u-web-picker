from flask import Response, redirect, request, send_file

import core
import public_epg_logos
import sports
from sports import alert_stream
from sports import channel_one_alerts
from sports import channel_three_alerts
from sports import game_alert_demo
from sports import live_stats
from sports import mlb_fake_stats
from sports import mlb_stats_pip
from sports import nfl_demo_stats


def register_output_routes(app):
    def serve_named_epg(source_id: str):
        source = core.find_epg_source(source_id)
        if not source:
            return Response(
                "EPG source not found.\n",
                content_type="text/plain; charset=utf-8",
                status=404,
            )
        path = core.epg_cache_path(source_id)
        if not path.exists():
            ok, message = core.refresh_epg_source(source_id)
            if not ok or not path.exists():
                return Response(
                    f"EPG cache could not be generated: {message}\n",
                    content_type="text/plain; charset=utf-8",
                    status=502,
                )
        response = send_file(
            path,
            mimetype="application/xml",
            as_attachment=False,
            download_name=f"{core.normalize_epg_id(source_id)}.xml",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/sports/stream/<int:assigned_number>")
    def generated_sports_stream(assigned_number: int):
        target = sports.generated_stream_target(core.DB_PATH, assigned_number)
        if not target:
            return Response("Sports stream not found.\n", status=404, content_type="text/plain; charset=utf-8")

        # For the current scoring-alert test, generated sports channels stay on
        # their direct provider streams. Manual channels 1 and 3 carry the real
        # MLB notifications so the experiment has predictable watched streams.
        response = redirect(target, code=307)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.route(
        "/sports/alert-stream/<int:assigned_number>/<filename>",
        methods=["GET", "HEAD", "OPTIONS"],
    )
    def generated_sports_alert_media(assigned_number: int, filename: str):
        if request.method == "OPTIONS":
            response = Response(status=204)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Origin, Accept, Accept-Encoding, Content-Type, Range"
            )
            return response

        try:
            path = alert_stream.safe_media_file(
                core.DB_PATH,
                assigned_number,
                filename,
            )
        except RuntimeError as exc:
            return Response(
                f"{exc}\n",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        except Exception as exc:
            return Response(
                f"Could not start sports alert stream: {exc}\n",
                status=502,
                content_type="text/plain; charset=utf-8",
            )

        if path is None:
            return Response(
                "Sports alert stream not found.\n",
                status=404,
                content_type="text/plain; charset=utf-8",
            )

        if filename == "stream.m3u8":
            response = send_file(
                path,
                mimetype="application/x-mpegurl",
                conditional=True,
            )
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        else:
            response = send_file(path, mimetype="video/mp2t", conditional=True)
            response.headers["Cache-Control"] = "public, max-age=30"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Origin, Accept, Accept-Encoding, Content-Type, Range"
        )
        response.headers["Access-Control-Expose-Headers"] = (
            "Content-Length, Content-Range, Accept-Ranges, Content-Type"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.get("/epg/<source_id>.xml")
    def named_epg(source_id: str):
        if source_id in {"sports", "combined", "epg"}:
            return Response("Not found.\n", status=404)
        return serve_named_epg(source_id)

    @app.get("/epg/sports.xml")
    def sports_epg():
        core.ensure_epg_exports_current()
        response = send_file(
            core.SPORTS_EPG_PATH,
            mimetype="application/xml",
            as_attachment=False,
            download_name="sports.xml",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-M3U-Picker-Guide-Revision"] = str(int(core.SPORTS_EPG_PATH.stat().st_mtime))
        return response

    @app.get("/epg/epg.xml")
    @app.get("/epg/combined.xml")
    def combined_epg():
        core.ensure_epg_exports_current()
        response = send_file(
            core.COMBINED_EPG_PATH,
            mimetype="application/xml",
            as_attachment=False,
            download_name="epg.xml",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-M3U-Picker-Guide-Revision"] = str(int(core.COMBINED_EPG_PATH.stat().st_mtime))
        return response

    def with_manual_epg_logos(text: str) -> str:
        try:
            return public_epg_logos.rewrite_manual_playlist_logos(
                text,
                core.active_public_epg_paths(),
            )
        except Exception:
            # Logo enrichment is cosmetic and must never make a playlist fail.
            return text

    @app.get("/playlist/channels.m3u")
    @app.get("/playlist/custom.m3u")
    def playlist():
        guide_url = request.url_root.rstrip("/") + "/epg/epg.xml"
        base_url = request.url_root.rstrip("/")
        if not core.PLAYLIST_PATH.exists():
            text = f'#EXTM3U url-tvg="{guide_url}" x-tvg-url="{guide_url}"\n'
        else:
            text = core.PLAYLIST_PATH.read_text(encoding="utf-8-sig", errors="replace")
            lines = text.splitlines()
            header = f'#EXTM3U url-tvg="{guide_url}" x-tvg-url="{guide_url}"'
            if lines and lines[0].startswith("#EXTM3U"):
                lines[0] = header
            else:
                lines.insert(0, header)
            lines = [
                f"{base_url}{line}" if line.startswith("/sports/stream/") else line
                for line in lines
            ]
            text = "\n".join(lines) + "\n"

        # Permanent lab channels for this experiment branch. 1.1 proves the
        # ESPN -> renderer -> HLS path using a completed NFL game; 1.2 proves
        # that a single synthetic channel can keep changing game state without
        # any external sports feed at all. 0.10 remains the deterministic fake
        # alert demo while real MLB scoring alerts are temporarily routed onto
        # the actual saved channels 1 and 3 below.
        text = nfl_demo_stats.inject_demo_channel(text, base_url)
        text = mlb_fake_stats.inject_demo_channel(text, base_url)
        text = game_alert_demo.inject_demo_channel(text, base_url)

        # Add N.2 first so the existing N.1 injector inserts between the parent
        # game and PiP channel. Final order is N, N.1, N.2. PiP rows are exposed
        # only for MLB games the carousel currently believes are live.
        text = mlb_stats_pip.inject_pip_channels(text, core.DB_PATH, base_url)

        # Experimental second-screen companion channels. Every logical MLB game
        # gets one N.1 HLS stats channel backed by live game data. The synthetic
        # stream starts only when a client tunes it.
        text = live_stats.inject_stats_channels(text, core.DB_PATH, base_url)

        # Temporary live test: keep channels 1 and 3 identity/guide rows but
        # replace only their served media URLs with the MLB score-alert wrappers.
        text = channel_one_alerts.route_channel_one(text, base_url)
        text = channel_three_alerts.route_channel_three(text, base_url)
        text = with_manual_epg_logos(text)
        response = Response(text, mimetype="audio/x-mpegurl")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/playlist/all.m3u")
    def playlist_all():
        return Response(
            with_manual_epg_logos(core.m3u_from_channels(core.all_grouped_channels())),
            mimetype="audio/x-mpegurl",
        )

    @app.get("/playlist/group/<slug>.m3u")
    def playlist_group(slug: str):
        _, items = core.group_channels_for_slug(slug)
        return Response(
            with_manual_epg_logos(core.m3u_from_channels(items)),
            mimetype="audio/x-mpegurl",
        )
