"""Bounded, in-memory diagnostics for the temporary commercials channel."""
from collections import deque
from datetime import datetime, timezone, timedelta
import json
import threading
import uuid

LOCK = threading.RLock()
SESSIONS = deque(maxlen=100)
PLAYS = deque(maxlen=2000)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def snapshot():
    with LOCK:
        return {'sessions': [dict(s) for s in reversed(SESSIONS)],
                'plays': [dict(p) for p in reversed(PLAYS)],
                'note': 'Temporary server-side playout log; resets on app restart. Player buffering may lag behind. Clip boundaries are inferred from measured file durations.'}


class DebugSession:
    def __init__(self, paths, durations=None):
        if durations is None:
            root = paths[0].parent
            manifest = json.loads((root/'manifest.json').read_text())
            durations = {r['clip']:float(r['duration']) for r in manifest['clips']}
            verification = root/'verification.json'
            if verification.exists():
                durations.update({r['clip']:float(r['actual']) for r in json.loads(verification.read_text())})
        self.names = [p.name for p in paths]
        self.durations = [durations[n] for n in self.names]
        if not self.durations or any(d <= 0 for d in self.durations):
            raise ValueError('Clip durations must be positive')
        self.started = datetime.now(timezone.utc)
        self.elapsed = 0.0
        self.position = 0
        self.offset = 0.0
        self.cycle = 1
        self.play = None
        self.closed = False
        self.record = {'id':uuid.uuid4().hex[:12], 'started_at':self.started.isoformat(),
                       'stopped_at':None, 'status':'playing', 'streamed_seconds':0,
                       'output_bytes':0, 'clips_completed':0, 'order':self.names,
                       'current_file':None, 'errors':[]}
        with LOCK:
            SESSIONS.append(self.record)

    def advance(self, seconds, size=0):
        with LOCK:
            if self.closed:
                return
            remaining = max(0,float(seconds)-self.elapsed)
            while remaining > .000001:
                if self.play is None:
                    self.play = {'session_id':self.record['id'], 'filename':self.names[self.position],
                                 'queue_position':self.position+1, 'cycle':self.cycle,
                                 'expected_seconds':self.durations[self.position],
                                 'started_at':(self.started+timedelta(seconds=self.elapsed)).isoformat(),
                                 'stopped_at':None, 'streamed_seconds':0, 'status':'playing'}
                    PLAYS.append(self.play)
                    self.record['current_file'] = self.play['filename']
                part = min(remaining,self.durations[self.position]-self.offset)
                self.offset += part
                self.elapsed += part
                remaining -= part
                self.play['streamed_seconds'] = round(self.offset,3)
                if self.offset >= self.durations[self.position]-.000001:
                    self.play.update(status='completed', stopped_at=(self.started+timedelta(seconds=self.elapsed)).isoformat())
                    self.record['clips_completed'] += 1
                    self.position = (self.position+1) % len(self.names)
                    if self.position == 0:
                        self.cycle += 1
                    self.offset = 0.0
                    self.play = None
                    self.record['current_file'] = None
            self.record.update(streamed_seconds=round(self.elapsed,3),output_bytes=max(size,self.record['output_bytes']))

    def error(self, line):
        with LOCK:
            self.record['errors'] = (self.record['errors']+[line])[-10:]

    def close(self, returncode=None):
        with LOCK:
            if self.closed:
                return
            self.closed = True
            self.record.update(status='stopped', stopped_at=timestamp(), ffmpeg_exit_code=returncode)
            if self.play:
                self.play.update(status='interrupted', stopped_at=self.record['stopped_at'])
            self.record['current_file'] = None


class PlayoutDebugSession(DebugSession):
    """Record a rolling schedule without retaining an endless channel plan."""
    def __init__(self, channel):
        self.closed = False
        self.play = None
        self.offset = 0.0
        self.record = {'id':uuid.uuid4().hex[:12], 'channel':channel,
                       'started_at':timestamp(), 'stopped_at':None, 'status':'playing',
                       'streamed_seconds':0, 'output_bytes':0, 'clips_completed':0,
                       'order':[], 'plan':[], 'current_file':None, 'errors':[], 'transitions':[],
                       'lookahead':'Prepare the next segment as soon as the current segment starts'}
        with LOCK:
            SESSIONS.append(self.record)

    def begin(self, slot, index, offset):
        with LOCK:
            self.offset = offset
            self.play = {'session_id':self.record['id'], 'filename':slot['label'],
                         'source_file':slot['filename'], 'source_start':slot['start'],
                         'source_stop':slot['start']+slot['duration'], 'kind':slot['kind'],
                         'queue_position':index+1, 'cycle':1, 'expected_seconds':slot['duration'],
                         'started_at':timestamp(), 'stopped_at':None, 'streamed_seconds':0,
                         'status':'playing'}
            PLAYS.append(self.play)
            self.record['current_file'] = slot['label']
            self.record['order'] = (self.record['order']+[slot['label']])[-50:]
            self.record['plan'] = (self.record['plan']+[
                {k:v for k,v in slot.items() if k != 'path'}])[-50:]

    def advance(self, seconds, size=0):
        with LOCK:
            if self.play:
                self.play['streamed_seconds'] = round(min(self.play['expected_seconds'], max(0, seconds-self.offset)),3)
            self.record.update(streamed_seconds=round(seconds,3), output_bytes=size)

    def complete(self, seconds, size):
        with LOCK:
            self.advance(seconds,size)
            if self.play:
                self.play.update(status='completed', stopped_at=timestamp())
            self.record['clips_completed'] += 1
            self.record['current_file'] = None
            self.play = None

    def transition(self, item):
        with LOCK:
            self.record['transitions'] = (self.record['transitions']+[item])[-100:]
