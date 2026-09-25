"""Bounded, cancellable in-memory encoder read-ahead for local test playout."""
import queue
import subprocess
import threading
import time

from .ffmpeg import terminate

END = object()
CHUNK_BYTES = 188 * 128
BUFFER_CHUNKS = 256  # At most 6 MiB of transport data per encoder.


def video_clock(data, previous=0.0):
    """Latest video PTS in an aligned MPEG-TS block, in seconds."""
    for i in range(0, len(data)-187, 188):
        packet = data[i:i+188]
        if packet[0] != 0x47 or ((packet[1]&31)<<8 | packet[2]) != 256:
            continue
        if not packet[1]&0x40 or not packet[3]&0x10:
            continue
        start = 4 + (1+packet[4] if packet[3]&0x20 else 0)
        pes = packet[start:]
        if len(pes)<14 or pes[:3]!=b'\x00\x00\x01' or not pes[7]&0x80:
            continue
        a,b,c,d,e = pes[9:14]
        pts = ((a&14)<<29) | (b<<22) | ((c&254)<<14) | (d<<7) | (e>>1)
        seconds = pts/90000
        period = (1 << 33)/90000
        # MPEG-TS timestamps wrap after about 26.5 hours; the channel clock does not.
        seconds += round((previous-seconds)/period)*period
        previous = max(previous,seconds)
    return previous


class TransportContinuity:
    """Keep packet counters continuous across independent encoder outputs."""
    def __init__(self):
        self.counters = {}

    def rewrite(self, data):
        output = bytearray(data)
        for i in range(0,len(output)-187,188):
            if output[i] != 0x47:
                raise ValueError('Unaligned MPEG-TS output')
            pid = ((output[i+1]&31)<<8) | output[i+2]
            payload = bool(output[i+3]&0x10)
            counter = self.counters.get(pid, -1)
            if payload:
                counter = (counter+1)&15
            output[i+3] = (output[i+3]&0xf0) | max(counter,0)
            self.counters[pid] = max(counter,0)
            # This is one continuous programme; don't signal a clock reset
            # merely because a new encoder supplied the next clip.
            if output[i+3]&0x20 and output[i+4]>0:
                output[i+5] &= 0x7f
        return bytes(output)


class BufferedSegment:
    def __init__(self, args, transport_offset, duration, redact, report_error):
        self.output = queue.Queue(maxsize=BUFFER_CHUNKS)
        self.cancelled = threading.Event()
        self.ready = threading.Event()
        self.started_at = time.monotonic()
        self.first_data_at = None
        self.returncode = None
        self.closed = False
        self.process = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=64*1024)

        def put(value):
            while not self.cancelled.is_set():
                try:
                    self.output.put(value,timeout=.1)
                    return
                except queue.Full:
                    pass

        def read_output():
            clock = transport_offset
            try:
                while not self.cancelled.is_set():
                    data = self.process.stdout.read(CHUNK_BYTES)
                    if not data:
                        break
                    if self.first_data_at is None:
                        self.first_data_at = time.monotonic()
                        self.ready.set()
                    clock = video_clock(data,clock)
                    seconds = min(duration,max(0,clock-transport_offset)+1/24)
                    put((data,seconds))
                if not self.cancelled.is_set():
                    self.returncode = self.process.wait(timeout=5)
            except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
                if not self.cancelled.is_set():
                    report_error(str(exc).replace(redact,'[redacted]') if redact else str(exc))
                    self.returncode = -1
            finally:
                put(END)

        def read_errors():
            for raw in iter(self.process.stderr.readline,b''):
                line = raw.decode(errors='replace').strip()
                if '=' not in line and line and not self.cancelled.is_set():
                    report_error(line.replace(redact,'[redacted]') if redact else line)

        self.reader = threading.Thread(target=read_output,daemon=True,name='episode-buffer')
        self.errors = threading.Thread(target=read_errors,daemon=True,name='episode-buffer-errors')
        self.reader.start()
        self.errors.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancelled.set()
        terminate(self.process)
        self.reader.join(timeout=3)
        self.errors.join(timeout=3)
        self.process.stdout.close()
        self.process.stderr.close()
