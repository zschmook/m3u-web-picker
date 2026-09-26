"""Guide, export and live transport adapters for Plex movie channels."""
from datetime import datetime, timezone
import time
from xml.etree import ElementTree as ET

from flask import Response, jsonify, request, stream_with_context
import custom_channels as plex
import movie_channels as movies
from media import browser
from media.scheduled_channel import segments_at
from settings import load_settings
from .episode_test import stream_plan

PLAY_PREFIX = '/guide/play/movies/'


def local_url(identity):
    movies.channel(identity)
    return f'http://127.0.0.1:{load_settings().port}/stream/movies/{identity}.ts'


def programme(row, servers):
    result = dict(title=row['title'], subtitle=str(row.get('year', '')), description=row.get('description', ''),
        categories=row.get('genres', []), media_type='movie', release_date=row.get('release_date', ''),
        start=datetime.fromtimestamp(row['start'], timezone.utc).isoformat(),
        stop=datetime.fromtimestamp(row['stop'], timezone.utc).isoformat())
    server = servers.get(row.get('server_id'))
    if server and row.get('part'):
        import movie_restart
        result['restart_url'] = movie_restart.issue_plex(server['url'], server['token'], row['part'], title=row['title'])
    return result


def guide_items():
    if not movies.enabled():
        return []
    now = time.time()
    servers = {s['id']: s for s in plex.read('servers.json', [])}
    items = []
    for channel in movies.read('channels.json', []):
        if not channel.get('enabled'):
            continue
        try:
            state = movies.schedule(channel['id'], now)
        except (ValueError, OSError, KeyError):
            continue
        entries = [programme(p, servers) for p in state['programmes'] if p['stop'] > now and p['start'] < now + 5 * 86400]
        current = next((p for p in entries if datetime.fromisoformat(p['start']).timestamp() <= now), None)
        upcoming = [p for p in entries if p is not current]
        items.append(dict(number=channel['number'], name=channel['name'], group='Plex Movies',
            logo='/static/icons/movies/' + channel['id'] + '.svg', tvg_id='movie-' + channel['id'],
            generated=True, available=True, play_url=PLAY_PREFIX + channel['id'], now=current,
            next=upcoming[0] if upcoming else None, upcoming=upcoming))
    return items


def playlist_lines(base):
    lines = []
    for channel in guide_items():
        lines.extend([f'#EXTINF:-1 tvg-id="{channel["tvg_id"]}" tvg-chno="{channel["number"]}" tvg-logo="{base}{channel["logo"]}" group-title="Plex Movies",{channel["name"]}',
            base + '/stream/movies/' + channel['tvg_id'].removeprefix('movie-') + '.ts'])
    return lines


def merge_epg(root, items=None):
    items = guide_items() if items is None else items
    ids = {c['tvg_id'] for c in items}
    for element in list(root):
        if element.get('id') in ids or element.get('channel') in ids:
            root.remove(element)
    for channel in items:
        c = ET.Element('channel', id=channel['tvg_id'])
        ET.SubElement(c, 'display-name').text = channel['name']
        ET.SubElement(c, 'icon', src=request.url_root.rstrip('/') + channel['logo'])
        position = next((i for i, e in enumerate(root) if e.tag == 'programme'), len(root))
        root.insert(position, c)
        for row in ([channel['now']] if channel['now'] else []) + channel['upcoming']:
            stamp = lambda key: datetime.fromisoformat(row[key]).strftime('%Y%m%d%H%M%S +0000')
            p = ET.SubElement(root, 'programme', channel=channel['tvg_id'], start=stamp('start'), stop=stamp('stop'))
            ET.SubElement(p, 'title').text = row['title']
            ET.SubElement(p, 'desc').text = row.get('description', '')
            if row.get('release_date'):
                ET.SubElement(p, 'date').text = row['release_date'].replace('-', '')
            for genre in row.get('categories', []):
                ET.SubElement(p, 'category').text = genre
    return root


def playout(identity, now):
    while True:
        state = movies.schedule(identity, now)
        yield from segments_at(state, now)
        now = state['cursor']


def register_movie_channel_routes(app):
    @app.get('/api/movie-channels')
    def movie_payload():
        return jsonify(movies.payload())

    @app.patch('/api/movie-channels/<identity>')
    def movie_switch(identity):
        try:
            movies.toggle(identity, (request.get_json(silent=True) or {}).get('enabled'))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(movies.payload())

    @app.get('/guide/play/movies/<identity>')
    def movie_play(identity):
        try:
            target = local_url(identity)
        except ValueError:
            return Response('Movie channel unavailable', status=404)
        return browser.response_for(target, remux_only=True)

    @app.route('/stream/movies/<identity>.ts', methods=['GET', 'HEAD'])
    def movie_stream(identity):
        try:
            row = movies.channel(identity)
            movies.schedule(identity)
            servers = {s['id']: dict(server=s['url'], token=s['token']) for s in plex.read('servers.json', [])}
        except (ValueError, OSError):
            return Response('Movie channel unavailable', status=404)
        if request.method == 'HEAD':
            return Response(content_type='video/mp2t')
        now = time.time()
        cfg = dict(wall_clock_anchor=now, servers=servers)
        if request.args.get('roku_buffer') == '1':
            cfg['client_buffer_seconds'] = 30
        return Response(stream_with_context(stream_plan(playout(identity, now), cfg,
            request.environ.get('waitress.client_disconnected'), channel=row['number'])),
            content_type='video/mp2t', direct_passthrough=True, headers={'Cache-Control': 'no-store'})
