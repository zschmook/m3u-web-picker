"""Passive, bounded, in-memory observations of media served by Picker."""
from collections import OrderedDict, deque
import hashlib
import hmac
import secrets
import threading
import time
import uuid

from flask import request

LOCK = threading.RLock()
CLIENTS = OrderedDict()
EVENTS = deque(maxlen=40)
KEY = secrets.token_bytes(32)
HLS_IDLE_SECONDS = 30
MAX_CLIENTS = 256
RATE_WINDOW = 10


def identity(target):
    return hmac.new(KEY, str(target).encode(), hashlib.sha256).hexdigest()


def attach(response, target, mode, transport, upstream_id='', *, segmented=False):
    # Internal metadata only. Never put a provider URL into response headers.
    response._picker_stream = dict(target=target, mode=mode, transport=transport,
                                   upstream_id=upstream_id, segmented=segmented)
    return response


def client_name(agent):
    for marker, label in [('Roku', 'Roku'), ('VLC', 'VLC'), ('Jellyfin', 'Jellyfin'),
                          ('Edg/', 'Edge'), ('Firefox/', 'Firefox'), ('Chrome/', 'Chrome'),
                          ('Safari/', 'Safari'), ('Lavf/', 'FFmpeg')]:
        if marker.lower() in agent.lower():
            return label
    return 'Media client'


def source_details(target):
    """Use the already loaded catalog, without resolving/probing any channel."""
    import core
    sources = list(getattr(core, 'provider_sources', []))
    primary = next((s for s in sources if s.get('role') == 'primary'), {})
    for channel in list(getattr(core, 'channels', [])):
        if channel.get('url') == target:
            return str(channel.get('name') or 'Channel')[:160], str(primary.get('name') or 'Primary')[:80]
    return 'Media stream', ''


def _event(record, message, now):
    EVENTS.appendleft(dict(channel=record['channel'], client_ip=record['client_ip'],
                           message=message, recorded_at=now))


def _prune(now):
    expired = [key for key, row in CLIENTS.items()
               if not row['open_requests'] and now - row['last_seen'] > HLS_IDLE_SECONDS]
    for key in expired:
        row = CLIENTS.pop(key)
        _event(row, 'No recent HLS requests', now)


def observe(response):
    info = getattr(response, '_picker_stream', None)
    if not info or request.method != 'GET':
        return response
    if not 200 <= response.status_code < 300:
        return response
    now = time.time()
    address = request.remote_addr or 'Unknown'
    # Forwarded headers are not trusted; the connected peer is the destination.
    agent = request.headers.get('User-Agent', '')
    source_id = identity(info['target'])
    key = identity(address + agent + source_id + info['transport'] + info['upstream_id']) if info['segmented'] else uuid.uuid4().hex
    channel, provider = source_details(info['target'])
    with LOCK:
        _prune(now)
        if key not in CLIENTS:
            if len(CLIENTS) >= MAX_CLIENTS:
                idle = next((k for k, v in CLIENTS.items() if not v['open_requests']), None)
                if idle is None:
                    return response  # Monitoring must never hold up playback.
                CLIENTS.pop(idle)
            CLIENTS[key] = dict(id=key, channel=channel, provider=provider,
                client_ip=address, client=client_name(agent), mode=info['mode'],
                transport=info['transport'], upstream_id=info['upstream_id'] or source_id,
                started_at=now, last_seen=now, last_data=0, bytes_sent=0,
                samples=deque(maxlen=1024), open_requests=0, segmented=info['segmented'])
        row = CLIENTS[key]
        row['open_requests'] += 1
        row['last_seen'] = now
        CLIENTS.move_to_end(key)
    original = response.response
    finished = False
    finish_lock = threading.Lock()

    def finish():
        nonlocal finished
        with finish_lock:
            if finished:
                return
            finished = True
        with LOCK:
            current = CLIENTS.get(key)
            if current:
                current['open_requests'] = max(0, current['open_requests'] - 1)
                current['last_seen'] = time.time()
                if not current['segmented'] and not current['open_requests']:
                    CLIENTS.pop(key, None)
                    _event(current, 'Stream response closed', time.time())

    def measured():
        try:
            for chunk in original:
                size = len(chunk.encode('utf-8') if isinstance(chunk, str) else chunk)
                stamp = time.time()
                with LOCK:
                    current = CLIENTS.get(key)
                    if current:
                        current['bytes_sent'] += size
                        current['last_seen'] = current['last_data'] = stamp
                        samples = current['samples']
                        if samples and int(samples[-1][0]) == int(stamp):
                            samples[-1] = (stamp, samples[-1][1] + size)
                        else:
                            samples.append((stamp, size))
                        while samples and samples[0][0] < stamp - RATE_WINDOW:
                            samples.popleft()
                yield chunk
        finally:
            try:
                if hasattr(original, 'close'):
                    original.close()
            finally:
                finish()

    response.response = measured()
    if hasattr(original, 'close'):
        response.call_on_close(original.close)
    response.call_on_close(finish)
    return response


def snapshot(now=None):
    now = time.time() if now is None else now
    with LOCK:
        _prune(now)
        streams = []
        for row in CLIENTS.values():
            public = {key: row[key] for key in ['id', 'channel', 'provider', 'client_ip', 'client',
                      'mode', 'transport', 'upstream_id', 'started_at', 'bytes_sent']}
            public['elapsed_seconds'] = max(0, int(now - row['started_at']))
            amount = sum(size for stamp, size in row['samples'] if stamp >= now - RATE_WINDOW)
            period = min(RATE_WINDOW, max(1, now - row['started_at']))
            public['mbps'] = round(amount * 8 / period / 1_000_000, 2)
            public['bytes_per_second'] = round(amount / period)
            public['state'] = 'receiving' if now - row['last_data'] < 15 else 'waiting'
            streams.append(public)
        clients = {}
        for stream in streams:
            key = (stream['client_ip'], stream['client'])
            client = clients.setdefault(key, dict(client_ip=key[0], client=key[1],
                       connections=0, bytes_per_second=0, bytes_sent=0))
            client['connections'] += 1
            client['bytes_per_second'] += stream['bytes_per_second']
            client['bytes_sent'] += stream['bytes_sent']
        return dict(streams=streams, clients=list(clients.values()), events=list(EVENTS), sampled_at=now,
                    client_count=len({(s['client_ip'], s['client']) for s in streams}),
                    observed_stream_count=len(streams))
