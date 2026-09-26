"""TV-only Roku client API; the web receiver remains supported."""
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import threading
import time
import zipfile

from flask import Response, jsonify, request, send_file
import movie_restart
from media import hls, director, roku_movie
from . import guide
from .http import no_cache

REPO = Path(__file__).resolve().parents[1]
LIVE_LEASES = {}
LEASE_LOCK = threading.Lock()
SPORTS_DELAY_SECONDS = 4
ENTERTAINMENT_DELAY_SECONDS = 30


def is_sports_channel(channel):
    play_url = str(channel.get('play_url') or '')
    if play_url.startswith('/guide/play/sports/'):
        return True
    # A football documentary or sports movie remains an entertainment stream.
    if play_url.startswith(('/guide/play/custom/', '/guide/play/movies/')):
        return False
    current = channel.get('now') or {}
    labels = [channel.get('name', ''), channel.get('group', ''), *(current.get('categories') or [])]
    return bool(re.search(r'\b(sports?|espn\w*|fs[12]|fox sports|nbcsn|mlb|nfl|nba|nhl|tennis|golf|sny|nesn|btn)\b',
        ' '.join(str(label) for label in labels), re.IGNORECASE))


def lease_live(token):
    lease = secrets.token_urlsafe(18)
    with LEASE_LOCK:
        LIVE_LEASES[lease] = (token, time.monotonic())
    return lease


def update_live(data, *, stop=False):
    lease, token = str(data.get('lease', '')), str(data.get('token', ''))
    if not lease:
        # Compatibility with playback received from the existing web remote.
        return hls.stop_session(token) if stop else bool(hls.touch_session(token))
    with LEASE_LOCK:
        existing = LIVE_LEASES.get(lease)
        if existing is None or existing[0] != token:
            return False
        if stop:
            LIVE_LEASES.pop(lease)
        else:
            LIVE_LEASES[lease] = (token, time.monotonic())
    return hls.stop_session(token) if stop else bool(hls.touch_session(token))


def expire_live_leases():
    with LEASE_LOCK:
        stale = [lease for lease, (_, touched) in LIVE_LEASES.items()
            if time.monotonic() - touched > 180]
        tokens = [LIVE_LEASES.pop(lease)[0] for lease in stale]
    for token in tokens:
        hls.stop_session(token)


def maintain_live_leases():
    while True:
        time.sleep(30)
        expire_live_leases()


threading.Thread(target=maintain_live_leases, name='roku-live-cleanup', daemon=True).start()


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, TypeError):
        return 0


def guide_payload(channels, now=None, hours=8):
    now = time.time() if now is None else now
    rows = []
    for channel in channels:
        if channel.get('play_url') == director.PLAY_URL:
            continue
        programmes = []
        seen = set()
        for p in [channel.get('now'), channel.get('next'), *channel.get('upcoming', [])]:
            if not p:
                continue
            start, stop = stamp(p.get('start')), stamp(p.get('stop'))
            key = (start, stop, p.get('title'))
            if stop <= now or start >= now + hours * 3600 or stop <= start or key in seen:
                continue
            seen.add(key)
            programmes.append(dict(title=p.get('title') or 'Program', description=(p.get('description') or '')[:2000],
                start=int(start), stop=int(stop), media_type=p.get('media_type', ''),
                restart_url=p.get('restart_url', ''), subtitle=p.get('subtitle', '')))
        programmes.sort(key=lambda p: p['start'])
        if not programmes:
            programmes = [dict(title='No guide data', description='', start=int(now),
                stop=int(now + hours * 3600), media_type='', restart_url='', subtitle='')]
        logo = channel.get('logo', '')
        if logo.split('?', 1)[0].lower().endswith('.svg'):
            logo = '/static/icons/guide-192.png'
        rows.append(dict(number=str(channel.get('number', '')), name=channel.get('name', ''),
            group=channel.get('group', ''), logo=logo, play_url=channel.get('play_url', ''),
            is_sports=is_sports_channel(channel), programmes=programmes))
    return dict(server_time=int(now), window_hours=hours, channels=rows)


def package(server=''):
    data = BytesIO()
    root = REPO / 'roku-receiver'
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(root / 'manifest', 'manifest')
        for folder in ('source', 'components'):
            for path in sorted((root / folder).rglob('*')):
                if path.is_file() and path.suffix in ('.brs', '.xml'):
                    archive.write(path, path.relative_to(root).as_posix())
        archive.write(REPO / 'static/icons/guide-512.png', 'images/icon.png')
        archive.writestr('server.json', json.dumps(dict(server=server)))
    data.seek(0)
    return data


def register_roku_app_routes(app):
    @app.get('/api/roku/guide')
    def roku_guide():
        response = app.view_functions['api_guide_channels']()
        return no_cache(jsonify(guide_payload(response.get_json()['channels'])))

    @app.get('/roku/app.zip')
    def roku_package():
        server = guide._guide_media_origin()
        return send_file(package(server), mimetype='application/zip', as_attachment=True,
            download_name='m3u-tv-roku.zip', max_age=0)

    @app.post('/api/roku/playback')
    def roku_playback():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Playback request must be an object.'), 400
        play_url = str(data.get('play_url', ''))
        mode = data.get('mode', 'live')
        if mode not in ('live', 'movie'):
            return jsonify(error='Unknown playback mode.'), 400
        if mode == 'movie':
            ticket = re.fullmatch(r'/guide/play/restart/([A-Za-z0-9_-]{24,64})', str(data.get('restart_url', '')))
            if not ticket:
                return jsonify(error='Movie restart link unavailable. Refresh the guide.'), 400
            try:
                source = movie_restart.resolve(ticket.group(1))
                session = roku_movie.start(source, timeout=45, buffer_seconds=32)
            except (ValueError, RuntimeError, OSError):
                return jsonify(error='Movie could not start. Try again or return to live.'), 502
            return no_cache(jsonify(token=session.token, kind='movie', title=source['title'],
                media_url=request.url_root.rstrip('/') + f'/roku/movie/{session.token}/stream.m3u8'))
        targets, callback = guide._resolve_guide_hls_targets(play_url)
        if not targets:
            return jsonify(error='Channel unavailable.'), 404
        low_latency = play_url.startswith('/guide/play/sports/') or data.get('low_latency') is True
        delay = SPORTS_DELAY_SECONDS if low_latency else ENTERTAINMENT_DELAY_SECONDS
        try:
            if play_url == director.PLAY_URL:
                from .remote import _manual_channel_payload
                _manual_channel_payload()
                if director.safe_media_file('stream.m3u8') is None:
                    raise RuntimeError('Channel is warming up.')
                return no_cache(jsonify(token='', kind='live',
                    media_url=request.url_root.rstrip('/') + director.STREAM_PATH))
            session = hls.start_session(targets, on_target=callback)
            if not hls.wait_for_buffer(session.directory, session.process, delay + 2):
                hls.stop_session(session.token)
                raise RuntimeError('Channel did not build a playback buffer.')
        except (ValueError, RuntimeError, OSError):
            return jsonify(error='Channel could not start. Try again.'), 502
        return no_cache(jsonify(token=session.token, lease=lease_live(session.token), kind='live',
            live_delay_seconds=delay,
            media_url=request.url_root.rstrip('/') + f'/guide/roku/{session.token}/stream.m3u8'))

    @app.post('/api/roku/playback/stop')
    def roku_stop():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Playback request must be an object.'), 400
        stopped = roku_movie.stop(str(data.get('token', ''))) if data.get('kind') == 'movie' else update_live(data, stop=True)
        return no_cache(jsonify(stopped=stopped))

    @app.post('/api/roku/playback/heartbeat')
    def roku_heartbeat():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Playback request must be an object.'), 400
        active = bool(roku_movie.touch(str(data.get('token', '')))) if data.get('kind') == 'movie' else update_live(data)
        return no_cache(jsonify(active=active))

    @app.route('/roku/movie/<token>/<filename>', methods=['GET', 'HEAD'])
    def roku_movie_media(token, filename):
        path = roku_movie.media_file(token, filename)
        if path is None:
            return Response('Movie stream unavailable.', status=404)
        response = send_file(path, mimetype='application/x-mpegurl' if filename.endswith('.m3u8') else 'video/mp2t', conditional=True)
        response.headers['Cache-Control'] = 'no-store' if filename.endswith('.m3u8') else 'private, max-age=3600'
        return response
