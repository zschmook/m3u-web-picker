"""Retain the exact Python and PyInstaller notices inside frozen installers."""
import importlib.metadata as metadata
from pathlib import Path
import sys


def write_notices(destination):
    dist = metadata.distribution('pyinstaller')
    parts = [f'M3U Web Picker installer third-party notices\nPyInstaller {dist.version}\nPython {sys.version}\n']
    found = False
    for path in dist.files or []:
        if path.name.lower().startswith(('copying', 'license', 'notice')):
            source = Path(dist.locate_file(path))
            if source.is_file():
                parts.append(source.read_text(encoding='utf-8'))
                found = True
    if not found:
        raise RuntimeError('The installed PyInstaller license could not be found')
    candidates = [Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt']
    python_license = next((p for p in candidates if p.is_file()), None)
    if python_license is None:
        raise RuntimeError('The installed Python license could not be found')
    parts.append(python_license.read_text(encoding='utf-8'))
    Path(destination).write_text('\n\n'.join(parts), encoding='utf-8')
