from flask import Response, redirect, request, send_file

import core
import media_pipeline
import public_epg_logos
import sports
from media import mpegts


def register_output_routes(app):
    def curated_playlist_text(*, encoded: bool) -> str:
        lines = ["#EXTM3U"]
        base_url = request.url_root.rstrip("/")
        for number, channel in enumerate(core.selected_channels_from_selected_ids_in_order(), start=1):
            raw = core.apply_channel_number(channel, number)
            if not raw:
                continue
            key = core.channel_key(channel)
            token = key.split(":", 1)[1] if key.startswith("manual:") else ""
            raw[-1] = (
                f"{base_url}/stream/channel/manual/{token}/mpegts"
                if encoded and token else str(channel.get("url", "") or "")
            )
            lines.extend(raw)
        for row in sports.generated_rows(core.DB_PATH):
            raw = list(row.get("raw", []))
            if not raw:
                continue
            number = int(row.get("assigned_number") or 0)
            raw[-1] = (
                f"{base_url}/stream/channel/sports/{number}/mpegts"
                if encoded else str(row.get("url", "") or "")
            )
            lines.extend(raw)
        return "\n".join(lines) + "\n"

    def resolve_stream(kind: str, identity: str) -> str:
        if kind == "manual":
            return core.manual_stream_target(identity)
        if kind == "sports" and str(identity).isdigit():
            return sports.generated_stream_target(core.DB_PATH, int(identity))
        return ""

    @app.route("/stream/channel/<kind>/<identity>/mpegts", methods=["GET", "HEAD"])
    def encoded_channel_stream(kind: str, identity: str):
        target = resolve_stream(kind, identity)
        if not target:
            return Response("Channel stream not found.\n", status=404, content_type="text/plain; charset=utf-8")
        if request.method == "HEAD":
            return Response(status=200, content_type="video/mp2t")
        return mpegts.response_for(target)

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
        response = redirect(target, code=307)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
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
        text = curated_playlist_text(encoded=media_pipeline.settings()["enabled"])
        lines = text.splitlines()
        lines[0] = f'#EXTM3U url-tvg="{guide_url}" x-tvg-url="{guide_url}"'
        text = "\n".join(lines) + "\n"
        text = with_manual_epg_logos(text)
        response = Response(text, mimetype="audio/x-mpegurl")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/playlist/channels.direct.m3u")
    def playlist_direct():
        guide_url = request.url_root.rstrip("/") + "/epg/epg.xml"
        lines = curated_playlist_text(encoded=False).splitlines()
        lines[0] = f'#EXTM3U url-tvg="{guide_url}" x-tvg-url="{guide_url}"'
        response = Response(with_manual_epg_logos("\n".join(lines) + "\n"), mimetype="audio/x-mpegurl")
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
