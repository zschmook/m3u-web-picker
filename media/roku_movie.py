"""Private movie HLS sessions whose segments survive pause/resume."""
import atexit
from dataclasses import dataclass, field
from pathlib import Path
import re
import queue
import secrets
import shutil
import subprocess
import threading
import time

import media_pipeline
from .episode_buffer import BufferedSegment, END
from .ffmpeg import executable, normalized_live_input_args, terminate
from .hls import HLS_ROOT, buffered_seconds

ROOT = HLS_ROOT / 'roku-movies'
LOCK = threading.RLock()
SESSIONS = {}
IDLE_SECONDS = 180
MAX_BYTES = 12 * 1024 ** 3
READ_AHEAD_SECONDS = 30


@dataclass
class MovieSession:
    token: str
    directory: Path
    process: object
    pipeline_token: str
    last_access: float
    stderr: object
    producer: object = None
    feeder: object = None
    cancelled: object = field(default_factory=threading.Event)
    producer_lock: object = field(default_factory=threading.Lock)


def command(source, *, live=False):
    args = normalized_live_input_args(source['target'], input_headers=source['input_headers'],
        video_extra=('-force_key_frames', 'expr:gte(t,n_forced*2)'), preserve_av_timing=live)
    audio_filter = 'aresample=async=1000:first_pts=0' if live else 'aresample=async=1:first_pts=0'
    return args + ['-af', audio_filter, '-sn', '-dn', '-f', 'mpegts',
        '-muxdelay', '0', '-muxpreload', '0', 'pipe:1']


def hls_command(directory):
    # The producer already supplies H.264/AAC. This second process only muxes
    # paced bytes into retained segments; it does not encode the movie twice.
    return [executable(), '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-probesize', '65536', '-analyzeduration', '200000', '-f', 'mpegts',
        '-copyts', '-start_at_zero', '-i', 'pipe:0', '-map', '0:v:0?',
        '-map', '0:a:0?', '-c', 'copy', '-f', 'hls',
        '-hls_time', '2', '-hls_playlist_type', 'event', '-hls_list_size', '0',
        '-hls_flags', 'independent_segments+temp_file', '-hls_segment_filename',
        str(directory / 'segment_%06d.ts'), str(directory / 'stream.m3u8')]


def paced_chunks(producer, cancelled):
    """Publish first frames immediately, then stay at most 30s ahead of play."""
    clock_start = None
    while not cancelled.is_set():
        try:
            value = producer.output.get(timeout=.1)
        except queue.Empty:
            continue
        if value is END:
            return
        data, progress = value
        if clock_start is None:
            clock_start = time.monotonic()
        delay = clock_start + progress - READ_AHEAD_SECONDS - time.monotonic()
        if cancelled.wait(max(0, delay)):
            return
        yield data


def feed(session):
    try:
        for data in paced_chunks(session.producer, session.cancelled):
            session.process.stdin.write(data)
            session.process.stdin.flush()
    except (OSError, ValueError):
        pass  # A stop or closed muxer cancels the same private encoder.
    finally:
        try:
            session.process.stdin.close()
        except (OSError, ValueError):
            pass
        close_producer(session)


def close_producer(session):
    if session.producer is not None:
        with session.producer_lock:
            session.producer.close()


def stop(token):
    with LOCK:
        session = SESSIONS.pop(token, None)
    if not session:
        return False
    session.cancelled.set()
    terminate(session.process)
    close_producer(session)
    if session.feeder is not None:
        session.feeder.join(timeout=4)
    try:
        session.process.stdin.close()
    except (OSError, ValueError):
        pass
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
            process = subprocess.Popen(hls_command(directory), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
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
    try:
        def report_error(message):
            for private in [source['target'], *source['input_headers'].values()]:
                if private:
                    message = message.replace(private, '[redacted]')
            if not session.cancelled.is_set():
                try:
                    with (directory / 'source.log').open('a', encoding='utf-8') as log:
                        log.write(message + '\n')
                except OSError:
                    pass  # Concurrent cleanup can remove the directory.
        session.producer = BufferedSegment(command(source, live=live), 0, float('inf'), '', report_error)
        session.feeder = threading.Thread(target=feed, args=(session,), name='roku-movie-buffer', daemon=True)
        session.feeder.start()
    except Exception:
        stop(token)
        raise
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
