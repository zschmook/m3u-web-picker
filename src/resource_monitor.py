"""Read local resource counters without probing playback or changing devices."""
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

LOCK = threading.Lock()
_previous = None
_gpu = None
_gpu_checked = float('-inf')
_gpu_pending = False


def read(path):
    return Path(path).read_text().strip()


def percent(used, capacity):
    return round(max(0, min(100, used * 100 / capacity)), 1) if capacity > 0 else None


def counters():
    mem = {line.split(':')[0]: int(line.split()[1]) * 1024
           for line in read('/proc/meminfo').splitlines() if ':' in line}
    capacity = mem['MemTotal']
    ram_scope = 'Docker host / VM'
    used = capacity - mem.get('MemAvailable', mem.get('MemFree', 0))
    try:
        current = int(read('/sys/fs/cgroup/memory.current'))
        limit = read('/sys/fs/cgroup/memory.max')
        stats = dict(line.split() for line in read('/sys/fs/cgroup/memory.stat').splitlines())
        used = max(0, current - int(stats.get('inactive_file', 0)))
        capacity = min(capacity, int(limit)) if limit != 'max' else capacity
        ram_scope = 'Picker container / memory limit' if limit != 'max' else 'Picker container / Docker host memory'
    except (OSError, ValueError):
        pass
    try:
        cpu = dict(line.split() for line in read('/sys/fs/cgroup/cpu.stat').splitlines())
        quota, period = read('/sys/fs/cgroup/cpu.max').split()
        cores = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
        cores = min(cores, int(quota) / int(period)) if quota != 'max' else cores
        return float(cpu['usage_usec']) / 1e6, time.monotonic() * cores, 'Picker container / allocated CPU', used, capacity, ram_scope
    except (OSError, ValueError, KeyError):
        ticks = [int(n) for n in read('/proc/stat').splitlines()[0].split()[1:]]
        # guest ticks are already included in user/nice.
        total = sum(ticks[:8])
        busy = total - ticks[3] - (ticks[4] if len(ticks) > 4 else 0)
        return busy, total, 'Docker host / VM', used, capacity, ram_scope


def read_gpu():
    binary = shutil.which('nvidia-smi')
    if binary:
        try:
            result = subprocess.run([binary, '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                                    capture_output=True, text=True, timeout=0.75, check=True)
            values = [float(value.strip()) for value in result.stdout.splitlines()]
            # A shared GPU reports whole-device activity, not just Picker.
            if values and all(0 <= value <= 100 for value in values):
                return round(max(values), 1)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    values = []
    for path in Path('/sys/class/drm').glob('card[0-9]*/device/gpu_busy_percent'):
        try:
            value = float(read(path))
            if 0 <= value <= 100:
                values.append(value)
        except (OSError, ValueError):
            pass
    return round(max(values), 1) if values else None


def _sample_gpu():
    global _gpu, _gpu_pending
    try:
        value = read_gpu()
    except Exception:
        value = None
    with LOCK:
        _gpu = value
        _gpu_pending = False


def snapshot():
    global _previous, _gpu_checked, _gpu_pending
    result = dict(cpu_percent=None, ram_percent=None, gpu_percent=None,
                  cpu_scope='Unavailable', ram_scope='Unavailable',
                  gpu_scope='Whole device; busiest exposed GPU')
    try:
        busy, total, cpu_scope, used, capacity, ram_scope = counters()
        with LOCK:
            if _previous and _previous[2] == cpu_scope:
                result['cpu_percent'] = percent(busy - _previous[0], total - _previous[1])
            _previous = busy, total, cpu_scope
        result.update(cpu_scope=cpu_scope, ram_scope=ram_scope, ram_percent=percent(used, capacity))
    except (OSError, ValueError, KeyError, IndexError):
        pass
    with LOCK:
        result['gpu_percent'] = _gpu
        now = time.monotonic()
        if not _gpu_pending and now - _gpu_checked >= 10:
            _gpu_checked = now
            _gpu_pending = True
            threading.Thread(target=_sample_gpu, daemon=True, name='gpu-usage').start()
    return result
