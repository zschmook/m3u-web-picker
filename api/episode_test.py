"""On-demand Plex episode with deliberate, fixed-interval test ad breaks."""
import json
from pathlib import Path
import random
import queue
import time

from flask import Response, request, stream_with_context
import core
from media import browser
from media.commercials_debug import PlayoutDebugSession
from media.episode_buffer import BufferedSegment, END, TransportContinuity
from settings import load_settings
from .commercials import clip_paths

PLAY_URL = '/guide/play/temp/episode-test'
STREAM_PATH = '/stream/temp/episode-test.ts'


def configuration():
    return json.loads((core.DATA_DIR/'plex-test-channel.json').read_text(encoding='utf-8-sig'))


def local_stream_url():
    return f'http://127.0.0.1:{load_settings().port}{STREAM_PATH}'


def guide_item():
    try:
        cfg = configuration()
    except (OSError, ValueError):
        return None
    return {'number':'0.002', 'name':cfg['title']+' + Commercials', 'group':'Temporary',
            'logo':'', 'tvg_id':'temp-episode-test', 'generated':True, 'available':True,
            'subtitle':'1 opening ad · 3 ads every 2 episode minutes · on demand', 'play_url':PLAY_URL}


def make_plan(cfg, paths, durations, interval=120):
    pool = list(paths)
    random.SystemRandom().shuffle(pool)
    pointer = 0
    plan = []
    def add_ad():
        nonlocal pointer
        if pointer == len(pool):
            random.SystemRandom().shuffle(pool)
            pointer = 0
        path = pool[pointer]
        pointer += 1
        plan.append({'kind':'commercial','filename':path.name,'path':str(path),
                     'start':0.0,'duration':durations[path.name],'label':f'{len(plan)+1:03d}_{path.name}'})
    add_ad()
    start = 0.0
    while start < cfg['duration']-.001:
        duration = min(interval, cfg['duration']-start)
        plan.append({'kind':'episode','filename':cfg['filename'],'start':start,'duration':duration,
                     'label':f'{len(plan)+1:03d}_Lanterns_S01E04_{start:07.2f}-{start+duration:07.2f}'})
        start += duration
        if start < cfg['duration']-.001:
            for _ in range(3):
                add_ad()
    return plan


def commercial_assets():
    paths = clip_paths()
    if not paths:
        raise ValueError('No commercial clips available')
    root = paths[0].parent
    data = json.loads((root/'manifest.json').read_text())
    durations = {r['clip']:float(r['duration']) for r in data['clips']}
    if (root/'verification.json').exists():
        durations.update({r['clip']:float(r['actual']) for r in json.loads((root/'verification.json').read_text())})
    if any(durations[p.name] <= 0 for p in paths):
        raise ValueError('Invalid clip duration')
    return paths,durations


def load_plan(cfg):
    paths,durations = commercial_assets()
    return make_plan(cfg,paths,durations)


def segment_command(slot, cfg, offset, realtime=True):
    args = ['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-progress','pipe:2','-stats_period','1']
    if realtime:
        args += ['-re']
    if slot['kind'] == 'episode' and not slot.get('path'):
        source=cfg.get('servers',{}).get(slot.get('server_id'),cfg)
        args += ['-rw_timeout','15000000','-headers','X-Plex-Token: '+source['token']+'\r\n']
        target = source['server'].rstrip('/')+(slot.get('part') or source['part'])
    else:
        target = slot['path']
    args += ['-probesize','262144','-analyzeduration','500000',
             '-ss',str(slot['start']),'-i',target,'-t',str(slot['duration']),
             '-map','0:v:0','-map','0:a:0','-sn','-dn',
             '-vf','scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=fps=24:start_time=0',
             '-c:v','libx264','-preset','veryfast','-tune','zerolatency','-crf','21','-pix_fmt','yuv420p',
             '-threads','2','-g','48','-sc_threshold','0','-c:a','aac','-b:a','160k','-ac','2','-ar','48000',
             '-af','aresample=async=1:first_pts=0','-output_ts_offset',str(offset),
             '-f','mpegts','-mpegts_flags','+resend_headers+initial_discontinuity',
             '-muxdelay','0','-muxpreload','0','pipe:1']
    return args


def stream_plan(plan, cfg, disconnected=None, channel='0.002'):
    schedule = iter(plan)
    slot = next(schedule,None)
    if slot is None:
        return
    debug = PlayoutDebugSession(channel)
    offset = 0.0
    total_bytes = 0
    current = upcoming = None
    clock_start = None
    continuity = TransportContinuity()
    secrets = {cfg.get('token',''),*(source.get('token','') for source in cfg.get('servers',{}).values())}-{''}

    def stopped():
        return callable(disconnected) and disconnected()

    def start_segment(segment, index, content_offset):
        transport_offset = content_offset+index*.1
        source=cfg.get('servers',{}).get(segment.get('server_id'),cfg)
        return BufferedSegment(segment_command(segment,cfg,transport_offset,realtime=False),
                               transport_offset,segment['duration'],source.get('token',''),debug.error)

    try:
        index = 0
        previous = None
        while slot is not None:
            if stopped():
                break
            transport_offset = offset+index*.1
            transition = None
            handoff_started = time.monotonic()
            if index:
                transition = {'from':previous['label'],'to':slot['label'],
                              'ready_at_handoff':bool(upcoming and upcoming.ready.is_set()),
                              'warmup_seconds':round(handoff_started-upcoming.started_at,3) if upcoming else 0,
                              'wait_for_data_ms':None}
                debug.transition(transition)
            current = upcoming if upcoming is not None else start_segment(slot,index,offset)
            upcoming = None
            next_slot = None
            lookahead_started = False
            received = False
            while not stopped():
                try:
                    value = current.output.get(timeout=.1)
                except queue.Empty:
                    continue
                if value is END:
                    break
                data, progress = value
                if not received:
                    received = True
                    debug.begin(slot,index,offset)
                    if transition:
                        transition['wait_for_data_ms'] = round((time.monotonic()-handoff_started)*1000,2)
                if clock_start is None:
                    clock_start = time.monotonic()
                    if cfg.get('wall_clock_anchor') is not None:
                        clock_start -= time.time()-cfg['wall_clock_anchor']
                # Keep three seconds of client buffer. Prefetched data is
                # paced on this one clock and is only logged when sent.
                deadline = clock_start+transport_offset+progress-3
                while time.monotonic()<deadline and not stopped():
                    time.sleep(max(0,min(.05,deadline-time.monotonic())))
                if stopped():
                    return
                if not lookahead_started:
                    next_slot = next(schedule,None)
                    if next_slot is not None:
                        upcoming = start_segment(next_slot,index+1,offset+slot['duration'])
                    lookahead_started = True
                total_bytes += len(data)
                debug.advance(offset+progress,total_bytes)
                yield continuity.rewrite(data)
            code = current.returncode
            current.close()
            current = None
            if stopped():
                break
            if code != 0:
                debug.error(f"FFmpeg stopped during {slot['label']} (exit {code})")
                break
            if not lookahead_started:
                # A viewer can tune into the last fraction of a frame.
                # Keep the live channel going even if that tail emits no data.
                next_slot = next(schedule,None)
            offset += slot['duration']
            debug.complete(offset,total_bytes)
            previous,slot = slot,next_slot
            index += 1
    except (OSError,ValueError) as exc:
        message=str(exc)
        for secret in secrets: message=message.replace(secret,'[redacted]')
        debug.error(message)
    finally:
        if current is not None:
            current.close()
        if upcoming is not None:
            upcoming.close()
        debug.close()


def register_episode_test_routes(app):
    @app.get(PLAY_URL)
    def episode_test_play():
        return browser.response_for(local_stream_url())

    @app.get('/playlist/episode-test.m3u')
    def episode_test_playlist():
        return Response('#EXTM3U\n#EXTINF:-1 tvg-id="temp-episode-test" tvg-chno="0.002" group-title="Temporary",Lanterns S01E04 + Commercials\n'+request.url_root.rstrip('/')+STREAM_PATH+'\n',mimetype='audio/x-mpegurl',headers={'Cache-Control':'no-store'})

    @app.route(STREAM_PATH,methods=['GET','HEAD'])
    def episode_test_stream():
        try:
            cfg = configuration()
            if request.method == 'HEAD':
                return Response(content_type='video/mp2t')
            plan = load_plan(cfg)
        except (OSError,ValueError,KeyError):
            return Response('Episode test channel is not configured.\n',status=503)
        return Response(stream_with_context(stream_plan(plan,cfg,request.environ.get('waitress.client_disconnected'))),
                        content_type='video/mp2t',direct_passthrough=True,
                        headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
