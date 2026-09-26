"""Create the sideload ZIP using the existing world/play app icon."""
import argparse
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--server', default='')
parser.add_argument('--output', default=str(root / 'roku-receiver/dist/m3u-tv-roku.zip'))
args = parser.parse_args()
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
source = root / 'roku-receiver'
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.write(source / 'manifest', 'manifest')
    for folder in ('source', 'components'):
        for path in sorted((source / folder).rglob('*')):
            if path.is_file() and path.suffix in ('.brs', '.xml'):
                archive.write(path, path.relative_to(source).as_posix())
    archive.write(root / 'static/icons/guide-512.png', 'images/icon.png')
    archive.writestr('server.json', json.dumps(dict(server=args.server.rstrip('/'))))
print(output)
