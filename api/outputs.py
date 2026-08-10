from flask import Response, redirect, request, send_file

import core
import sports


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

    @app.get("/playlist/channels.m3u")
    @app.get("/playlist/custom.m3u")
    def playlist():
        guide_url = request.url_root.rstrip("/") + "/epg/epg.xml"
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
            base_url = request.url_root.rstrip("/")
            lines = [
                f"{base_url}{line}" if line.startswith("/sports/stream/") else line
                for line in lines
            ]
            text = "\n".join(lines) + "\n"
        response = Response(text, mimetype="audio/x-mpegurl")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/playlist/all.m3u")
    def playlist_all():
        return Response(
            core.m3u_from_channels(core.all_grouped_channels()),
            mimetype="audio/x-mpegurl",
        )

    @app.get("/playlist/group/<slug>.m3u")
    def playlist_group(slug: str):
        _, items = core.group_channels_for_slug(slug)
        return Response(core.m3u_from_channels(items), mimetype="audio/x-mpegurl")
