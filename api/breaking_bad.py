"""Channel 0.03: a shared, clock-driven local Breaking Bad broadcast."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
from xml.etree import ElementTree as ET

from flask import Response, request, stream_with_context
import core
from media import browser
from media.scheduled_channel import new_schedule, extend_schedule, trim_schedule, save_schedule, segments_at, programme_payload
from settings import load_settings
from .episode_test import commercial_assets, stream_plan

PLAY_URL = '/guide/play/local/breaking-bad'
STREAM_PATH = '/stream/local/breaking-bad.ts'
CHANNEL_ID = 'local-breaking-bad'
LOCK = threading.RLock()


def catalog():
    data = json.loads((core.DATA_DIR/'breaking-bad-channel.json').read_text(encoding='utf-8-sig'))
    root = Path(os.environ.get('M3U_BREAKING_BAD_DIR','/local-media/breaking-bad')).resolve()
    episodes = []
    for row in data['episodes']:
        path = (root/row['path']).resolve()
        duration = float(row['duration'])
        if not path.is_relative_to(root) or not path.is_file() or not math.isfinite(duration) or duration <= 0:
            raise ValueError('Breaking Bad episode unavailable')
        episodes.append(dict(row,path=str(path),duration=duration))
    if not episodes:
        raise ValueError('No Breaking Bad episodes configured')
    return episodes


def schedule(now=None, horizon=None):
    now = time.time() if now is None else now
    horizon = now+7*86400 if horizon is None else horizon
    path = core.DATA_DIR/'breaking-bad-schedule.json'
    with LOCK:
        changed = False
        if path.exists():
            state = json.loads(path.read_text(encoding='utf-8'))
            if state.get('version') != 1:
                raise ValueError('Unsupported channel schedule')
        else:
            paths,durations = commercial_assets()
            ads = [dict(path=str(p),filename=p.name,duration=durations[p.name]) for p in paths]
            state = new_schedule(catalog(),ads,now,secrets.token_hex(16))
            changed = True
        # Extend in daily batches, retaining the published past and future.
        if state['cursor'] < horizon:
            extend_schedule(state,horizon+86400)
            trim_schedule(state,now-2*86400)
            changed = True
        if changed:
            save_schedule(path,state)
        return state


def playout(state, now):
    while True:
        for slot in segments_at(state,now):
            yield slot
        now = state['cursor']
        state = schedule(now=now)


def local_stream_url():
    return f'http://127.0.0.1:{load_settings().port}{STREAM_PATH}'


def guide_item(now=None):
    now = time.time() if now is None else now
    try:
        state = schedule(now)
    except (OSError,ValueError,KeyError,TypeError):
        return None
    entries = [p for p in state['programmes'] if p['stop']>now and p['start']<now+5*86400]
    current = next((p for p in entries if p['start']<=now<p['stop']),None)
    upcoming = [programme_payload(p) for p in entries if p['start']>now]
    return dict(number='0.03',name='Breaking Bad Shuffle',group='Local Channels',logo='',
                tvg_id=CHANNEL_ID,generated=True,available=True,play_url=PLAY_URL,
                subtitle='Live schedule · vintage commercial breaks',
                now=programme_payload(current) if current else None,
                next=upcoming[0] if upcoming else None,upcoming=upcoming)


def xmltv(state, now):
    root = ET.Element('tv',{'generator-info-name':'M3U Web Picker'})
    channel = ET.SubElement(root,'channel',{'id':CHANNEL_ID})
    ET.SubElement(channel,'display-name').text = 'Breaking Bad Shuffle'
    def stamp(value):
        return datetime.fromtimestamp(value,timezone.utc).strftime('%Y%m%d%H%M%S +0000')
    for entry in state['programmes']:
        if entry['stop']<=now or entry['start']>=now+5*86400:
            continue
        p = ET.SubElement(root,'programme',channel=CHANNEL_ID,start=stamp(entry['start']),stop=stamp(entry['stop']))
        ET.SubElement(p,'title').text = entry['title']
        ET.SubElement(p,'desc').text = programme_payload(entry)['description']
        ET.SubElement(p,'category').text = 'Drama'
        ET.SubElement(p,'episode-num',system='xmltv_ns').text = f"{entry['season']-1}.{entry['episode']-1}."
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def register_breaking_bad_routes(app):
    # Establish the clock at startup, even before the first viewer tunes in.
    if (core.DATA_DIR/'breaking-bad-channel.json').exists():
        try:
            schedule()
        except (OSError,ValueError,KeyError,TypeError) as exc:
            app.logger.warning('Breaking Bad schedule unavailable: %s',type(exc).__name__)

    @app.get(PLAY_URL)
    def breaking_bad_play():
        return browser.response_for(local_stream_url())

    @app.get('/playlist/breaking-bad.m3u')
    def breaking_bad_playlist():
        origin = request.url_root.rstrip('/')
        text = f'#EXTM3U url-tvg="{origin}/epg/breaking-bad.xml"\n'
        text += '#EXTINF:-1 tvg-id="local-breaking-bad" tvg-chno="0.03" group-title="Local Channels",Breaking Bad Shuffle\n'
        return Response(text+origin+STREAM_PATH+'\n',mimetype='audio/x-mpegurl',headers={'Cache-Control':'no-store'})

    @app.get('/epg/breaking-bad.xml')
    def breaking_bad_epg():
        now = time.time()
        try:
            data = xmltv(schedule(now),now)
        except (OSError,ValueError,KeyError,TypeError):
            return Response('Breaking Bad channel unavailable.\n',status=503)
        return Response(data,mimetype='application/xml',headers={'Cache-Control':'no-store'})

    @app.route(STREAM_PATH,methods=['GET','HEAD'])
    def breaking_bad_stream():
        now = time.time()
        try:
            state = schedule(now)
        except (OSError,ValueError,KeyError,TypeError):
            return Response('Breaking Bad channel unavailable.\n',status=503)
        if request.method == 'HEAD':
            return Response(content_type='video/mp2t')
        return Response(stream_with_context(stream_plan(
            playout(state,now),{'wall_clock_anchor':now},
            request.environ.get('waitress.client_disconnected'),channel='0.03')),
            content_type='video/mp2t',direct_passthrough=True,
            headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
