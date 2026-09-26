"""Private movie HLS sessions whose segments survive pause/resume."""
import atexit
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time

import media_pipeline
from .ffmpeg import normalized_live_input_args, terminate
from .hls import HLS_ROOT, buffered_seconds

ROOT = HLS_ROOT / 'roku-movies'
LOCK = threading.RLock()
SESSIONS = {}
IDLE_SECONDS = 180
MAX_BYTES = 12 * 1024 ** 3


@dataclass
class MovieSession:
    token: str
    directory: Path
    process: object
    pipeline_token: str
    last_access: float
    stderr: object


def command(source, directory, *, live=False):
    args = normalized_live_input_args(source['target'], input_headers=source['input_headers'],
        video_extra=('-force_key_frames', 'expr:gte(t,n_forced*2)'), preserve_av_timing=live)
    # Bound encoder speed/disk growth while the app retains the whole timeline.
    args[args.index('-i'):args.index('-i')] = ['-re']
    audio_filter = 'aresample=async=1000:first_pts=0' if live else 'aresample=async=1:first_pts=0'
    args += ['-af', audio_filter, '-sn', '-dn', '-f', 'hls',
        '-hls_time', '2', '-hls_playlist_type', 'event', '-hls_list_size', '0',
        '-hls_flags', 'independent_segments+temp_file', '-hls_segment_filename',
        str(directory / 'segment_%06d.ts'), str(directory / 'stream.m3u8')]
    return args


def stop(token):
    with LOCK:
        session = SESSIONS.pop(token, None)
    if not session:
        return False
    terminate(session.process)
    session.stderr.close()
    media_pipeline.release_session(session.pipeline_token)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def touch(token):
    with LOCK:
        session = SESSIONS.get(token)
        if session:
            session.last_access = time.monotonic()
        return session


def media_file(token, filename):
    if not re.fullmatch(r'[A-Za-z0-9_-]{24}', str(token)):
        return None
    if filename != 'stream.m3u8' and not re.fullmatch(r'segment_\d{6,}\.ts', filename):
        return None
    session = touch(token)
    if not session:
        return None
    path = session.directory / filename
    return path if path.is_file() else None


def start(source, timeout=20, *, buffer_seconds=2, live=False):
    pipeline = media_pipeline.acquire_session('roku-movie')
    token = secrets.token_urlsafe(18)
    directory = ROOT / token
    try:
        directory.mkdir(parents=True, exist_ok=False)
        stderr = (directory / 'encoder.log').open('wb')
        try:
            process = subprocess.Popen(command(source, directory, live=live), stdout=subprocess.DEVNULL,
                stderr=stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception:
            stderr.close()
            raise
    except Exception:
        media_pipeline.release_session(pipeline)
        shutil.rmtree(directory, ignore_errors=True)
        raise
    session = MovieSession(token, directory, process, pipeline, time.monotonic(), stderr)
    with LOCK:
        SESSIONS[token] = session
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if buffered_seconds(directory) >= buffer_seconds:
            return session
        if process.poll() is not None:
            break
        time.sleep(.1)
    stop(token)
    raise RuntimeError('Movie could not start. Try again or return to live.')


def maintenance():
    while True:
        time.sleep(30)
        with LOCK:
            sessions = list(SESSIONS.values())
        for session in sessions:
            if time.monotonic() - session.last_access > IDLE_SECONDS:
                stop(session.token)
                continue
            try:
                size = sum(p.stat().st_size for p in session.directory.glob('*.ts'))
            except OSError:
                # A concurrent stop can remove files while cleanup counts them.
                continue
            if size > MAX_BYTES:
                stop(session.token)


threading.Thread(target=maintenance, name='roku-movie-cleanup', daemon=True).start()
atexit.register(lambda: [stop(token) for token in list(SESSIONS)])
