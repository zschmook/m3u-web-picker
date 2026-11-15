"""Run schema setup once per database file and process, outside read paths."""
import os
from pathlib import Path
import threading

_lock = threading.RLock()
_ready = set()


def ensure_schema(connection, path, namespace, initialize, *, force=False):
    # Each :memory: connection owns a different database.
    if str(path) == ':memory:':
        initialize(connection)
        return
    file = Path(path).resolve()
    stat = file.stat()
    key = (os.getpid(), str(file), stat.st_dev, stat.st_ino, namespace)
    with _lock:
        if force or key not in _ready:
            initialize(connection)
            # Only successful initialization is remembered. Replacing the file
            # changes its identity and therefore runs migrations again.
            _ready.add(key)
