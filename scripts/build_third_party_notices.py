"""Create notices for the packages actually installed in this Docker image."""
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'static/licenses/generated'


def slug(value):
    return re.sub(r'[^a-zA-Z0-9_.-]+', '-', value)


def license_name(dist):
    value = dist.metadata.get('License-Expression') or dist.metadata.get('License')
    if value and len(value) < 160:
        return value
    for classifier in dist.metadata.get_all('Classifier') or []:
        if classifier.startswith('License :: OSI Approved ::'):
            return classifier.split(' :: ')[-1]
    return 'See full license text'


def project_url(dist):
    links = dist.metadata.get_all('Project-URL') or []
    for key in ('source', 'repository', 'homepage', 'documentation'):
        for link in links:
            label, _, url = link.partition(',')
            if key in label.lower() and url.strip().startswith('https://'):
                return url.strip()
    return f'https://pypi.org/project/{quote(dist.metadata["Name"])}/{quote(dist.version)}/'


def build(output=OUTPUT):
    output.mkdir(parents=True, exist_ok=True)
    documents = {}
    entries = []
    missing = []

    def document(name, source):
        data = Path(source).read_bytes()
        documents[name] = data
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return '/static/licenses/generated/' + name

    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        paths = [p for p in dist.files or [] if re.search(r'(?i)(^|/)(license[^/]*|copying[^/]*|notice[^/]*|authors[^/]*)$', str(p))]
        links = []
        for index, path in enumerate(paths):
            source = Path(dist.locate_file(path))
            if source.is_file():
                links.append(document(f'python/{slug(name)}-{index}-{slug(source.name)}.txt', source))
        # This wheel omits its upstream license; retain the verified versioned copy.
        if name.lower().replace('_', '-') == 'http-ece' and dist.version == '1.2.1' and not links:
            links.append(document('python/http-ece-LICENSE.txt', ROOT / 'static/licenses/upstream/http-ece-1.2.1.txt'))
        if not links:
            missing.append(f'{name} {dist.version}')
        entries.append(dict(group='Python libraries', name=name, version=dist.version,
                            license=license_name(dist), website=project_url(dist), notices=links,
                            source=f'https://pypi.org/project/{quote(name)}/{quote(dist.version)}/#files'))
    python_license = Path(sys.base_prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt'
    if python_license.is_file():
        entries.append(dict(group='Runtime', name='Python', version=sys.version.split()[0], license='PSF License and included notices',
                            website='https://www.python.org/', source='https://www.python.org/downloads/source/',
                            notices=[document('runtime/python-LICENSE.txt', python_license)]))

    query = '${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'
    installed = subprocess.check_output(['dpkg-query', '-W', '-f=' + query], text=True)
    for line in installed.splitlines():
        name, version, source, source_version = line.split('\t')
        short = name.split(':')[0]
        copyright_file = Path('/usr/share/doc') / short / 'copyright'
        links = [document(f'debian/{slug(name)}-copyright.txt', copyright_file)] if copyright_file.is_file() else []
        if not links:
            missing.append(f'Debian {name} {version}')
        entries.append(dict(group='Debian packages', name=name, version=version,
                            license='See package copyright and licenses',
                            website=f'https://packages.debian.org/bookworm/{quote(short)}',
                            source=f'https://sources.debian.org/src/{quote(source or short)}/{quote(source_version or version, safe="")}/',
                            notices=links))
    for path in sorted(Path('/usr/share/common-licenses').iterdir()):
        if path.is_file():
            document('common/' + slug(path.name) + '.txt', path)
    # Native libraries embedded in wheels and pip's vendored tools retain their
    # additional license/notice files even when not independent distributions.
    site = Path(sys.base_prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    for path in site.rglob('*'):
        if path.is_file() and re.match(r'(?i)^(license|copying|notice|authors)(\.|$)', path.name):
            document('embedded/' + slug(str(path.relative_to(site))) + '.txt', path)
    for path in sorted((ROOT / 'static/licenses/upstream').glob('*.txt')):
        document('upstream/' + path.name, path)
    for category in ('leagues', 'hockey'):
        document(f'artwork/{category}-sources.json', ROOT / f'static/icons/{category}/sources.json')
        document(f'artwork/{category}-README.txt', ROOT / f'static/icons/{category}/README.md')
    document('build/Dockerfile.txt', ROOT / 'Dockerfile')
    documents['build/ffmpeg-build.txt'] = subprocess.check_output(['ffmpeg', '-version'], stderr=subprocess.STDOUT)
    (output / 'build/ffmpeg-build.txt').write_bytes(documents['build/ffmpeg-build.txt'])
    if missing:
        raise RuntimeError('Missing package notices: ' + ', '.join(missing))
    inventory = dict(entries=entries, common_licenses=['/static/licenses/generated/common/' + slug(p.name) + '.txt' for p in sorted(Path('/usr/share/common-licenses').iterdir()) if p.is_file()])
    payload = (json.dumps(inventory, indent=2) + '\n').encode()
    (output / 'index.json').write_bytes(payload)
    with zipfile.ZipFile(output / 'third-party-notices.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('index.json', payload)
        for name, data in sorted(documents.items()):
            bundle.writestr(name, data)
    print(f'Bundled notices for {len(entries)} installed components ({len(documents)} documents).')


if __name__ == '__main__':
    build()
