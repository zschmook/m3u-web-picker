"""Bounded live read-ahead that grows while the viewer is already playing."""
import subprocess
import threading
import time

from .episode_buffer import BufferedSegment, END
from .ffmpeg import terminate

STARTUP_SECONDS = 6.0
MAX_AHEAD_SECONDS = 45.0
FILL_RATE = 0.2


def publication_delay(progress, elapsed):
    # At first permit six seconds, then grow by one second every five seconds.
    # Solve progress <= elapsed + min(45, 6 + elapsed * .2) for elapsed.
    required = max(0.0, progress - MAX_AHEAD_SECONDS,
                   (progress - STARTUP_SECONDS) / (1.0 + FILL_RATE))
    return max(0.0, required - elapsed)


class BufferedHlsProcess:
    """One encoder and a copy-only muxer, exposed as one supervised process."""
    def __init__(self, encoder_command, mux_command, stderr):
        self.cancelled = threading.Event()
        self.close_lock = threading.Lock()
        self.producer = None
        self.muxer = subprocess.Popen(mux_command, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=stderr, bufsize=0)
        try:
            def report_error(message):
                if not self.cancelled.is_set():
                    try:
                        stderr.write((message + '\n').encode())
                    except (OSError, ValueError):
                        pass
            self.producer = BufferedSegment(encoder_command, 0, float('inf'), '', report_error)
        except Exception:
            self.cancelled.set()
            terminate(self.muxer)
            self.muxer.stdin.close()
            raise
        self.feeder = threading.Thread(target=self.feed, name='hls-live-read-ahead', daemon=True)
        self.feeder.start()

    def close_source(self):
        with self.close_lock:
            if self.producer is not None:
                self.producer.close()

    def feed(self):
        import queue
        started = None
        try:
            while not self.cancelled.is_set():
                try:
                    item = self.producer.output.get(timeout=.1)
                except queue.Empty:
                    continue
                if item is END:
                    break
                data, progress = item
                if started is None:
                    started = time.monotonic()
                delay = publication_delay(progress, time.monotonic() - started)
                if self.cancelled.wait(delay):
                    break
                self.muxer.stdin.write(data)
                self.muxer.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.muxer.stdin.close()
            except (OSError, ValueError):
                pass
            self.close_source()

    def poll(self):
        code = self.muxer.poll()
        if code is not None:
            self.cancelled.set()
            # poll is also called while serving already-buffered media. The
            # feeder owns cleanup; never join producer threads on that path.
        return code

    def terminate(self):
        self.cancelled.set()
        self.muxer.terminate()
        self.close_source()

    def kill(self):
        self.cancelled.set()
        self.muxer.kill()
        self.close_source()

    def wait(self, timeout=None):
        code = self.muxer.wait(timeout=timeout)
        self.cancelled.set()
        self.close_source()
        self.feeder.join(timeout=3)
        return code
