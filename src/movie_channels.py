"""Plex movie catalogs and persistent, commercial-free broadcast schedules."""
from datetime import date
import hashlib
import math
from pathlib import PureWindowsPath, PurePosixPath
import re
import secrets
import threading
import time

import custom_channels as plex
from media.scheduled_channel import new_schedule, extend_schedule, trim_schedule, save_schedule

LOCK = threading.RLock()
CATEGORIES = (
    ('action-adventure', 'Action & Adventure', ('Action', 'Adventure')),
    ('drama', 'Drama', ('Drama',)), ('comedy', 'Comedy', ('Comedy',)),
    ('crime', 'Crime', ('Crime',)), ('documentary', 'Documentary', ('Documentary', 'News')),
    ('thriller', 'Thriller', ('Thriller',)), ('horror', 'Horror', ('Horror',)),
    ('biography', 'Biography', ('Biography',)), ('scifi', 'Sci-Fi', ('Sci-Fi', 'Science Fiction')),
    ('romance', 'Romance', ('Romance',)), ('mystery', 'Mystery', ('Mystery',)),
    ('fantasy', 'Fantasy', ('Fantasy',)), ('animation', 'Animation', ('Animation',)),
    ('family', 'Family', ('Family',)), ('history', 'History', ('History',)),
    ('film-noir', 'Film Noir', ('Film-Noir', 'Film Noir')),
    ('sports', 'Sports', ('Sport', 'Sports')), ('music-musicals', 'Music & Musicals', ('Music', 'Musical')),
    ('war', 'War', ('War',)), ('western', 'Western', ('Western',)),
    ('hallmark', 'Hallmark', ()), ('just-released', 'Just Released', ()),
)


def root():
    return plex.root() / 'movies'


def read(name, default):
    import json
    path = root() / name
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def enabled():
    return bool(plex.settings().get('movies_enabled', False))


def name_key(title, year):
    return re.sub(r'[^a-z0-9]+', '', str(title).casefold()) + ':' + str(year or '')


def scan_server(server, metadata=None):
    """Scan movie libraries only and confirm accessible, single-part media."""
    metadata = metadata or {}
    movies = []
    for library in plex.plex_xml(server['url'], '/library/sections', server['token']).findall('Directory'):
        if library.get('type') != 'movie':
            continue
        offset = 0
        while True:
            path = f"/library/sections/{library.get('key')}/all?type=1&includeGuids=1&X-Plex-Container-Start={offset}&X-Plex-Container-Size=500"
            page = plex.plex_xml(server['url'], path, server['token'])
            rows = page.findall('Video')
            for batch_start in range(0, len(rows), 100):
                batch = rows[batch_start:batch_start + 100]
                keys = [r.get('ratingKey') for r in batch if str(r.get('ratingKey', '')).isdigit()]
                if keys:
                    checked = plex.plex_xml(server['url'], '/library/metadata/' + ','.join(keys) + '?checkFiles=1&includeGuids=1', server['token']).findall('Video')
                else:
                    checked = batch
                for video in checked:
                    if video.get('type', 'movie') != 'movie':
                        continue
                    title, year = video.get('title', ''), video.get('year', '')
                    ids = [v.get('id', '') for v in video.findall('Guid')]
                    imdb = next((v[7:] for v in ids if v.startswith('imdb://')), '')
                    known = metadata.get('imdb', {}).get(imdb) or metadata.get('titles', {}).get(name_key(title, year)) or {}
                    if known.get('type') and known['type'] not in ('movie', 'tvMovie'):
                        continue
                    if imdb == 'tt0196712':  # Lookwell is a TV pilot in a movie library.
                        continue
                    part = next((m.find('Part') for m in video.findall('Media')
                                 if len(m.findall('Part')) == 1 and m.find('Part').get('exists') == '1'
                                 and m.find('Part').get('accessible') == '1'), None)
                    if part is None or not part.get('key', '').startswith('/library/parts/'):
                        continue
                    try:
                        duration = float(video.get('duration', '0')) / 1000
                    except ValueError:
                        continue
                    if not math.isfinite(duration) or duration <= 0:
                        continue
                    release = video.get('originallyAvailableAt', '')
                    try:
                        release = date.fromisoformat(release).isoformat()
                    except ValueError:
                        release = ''  # Unknown dates do not compete for Just Released.
                    file_path = part.get('file', '')
                    path_type = PureWindowsPath if '\\' in file_path else PurePosixPath
                    genres = known.get('genres') or [g.get('tag') for g in video.findall('Genre') if g.get('tag')]
                    identity = imdb or (video.get('guid', '') if video.get('guid', '').startswith('plex://') else name_key(title, year))
                    movies.append(dict(id=hashlib.sha256(identity.encode()).hexdigest()[:20],
                        title=title, year=year, release_date=release, genres=genres,
                        description=video.get('summary', ''), server_id=server['id'],
                        part=part.get('key'), duration=duration, filename=path_type(file_path).name or 'movie',
                        hallmark='hallmark' in file_path.casefold(), season=1, episode=1))
            offset += len(rows)
            if not rows or offset >= int(page.get('totalSize', str(offset))):
                break
            if offset > 100000:
                raise ValueError('Movie catalog exceeds scan limit')
    return movies


def select_channels(movies):
    unique = {}
    for movie in movies:
        if movie['id'] in unique:
            unique[movie['id']]['hallmark'] |= movie.get('hallmark', False)
        else:
            unique[movie['id']] = dict(movie)
    films = list(unique.values())
    output = []
    for index, (identity, name, genres) in enumerate(CATEGORIES):
        if identity == 'hallmark':
            selected = [m for m in films if m.get('hallmark')]
        elif identity == 'just-released':
            selected = sorted([m for m in films if m.get('release_date')],
                key=lambda m: (m['release_date'], m['title'].casefold(), m['id']), reverse=True)[:5]
        else:
            tags = {g.casefold() for g in genres}
            selected = [m for m in films if tags.intersection(str(g).casefold() for g in m.get('genres', []))]
        if selected:
            output.append((dict(id=identity, name=name, number=str(2500 + index), count=len(selected)), selected))
    return output


def refresh(servers, now=None):
    if not enabled():
        return dict(status='disabled', count=0)
    now = time.time() if now is None else now
    if not servers:
        return dict(status='warning', count=len(read('channels.json', [])), warnings=['No Plex servers available for movie scanning.'])
    metadata = read('metadata.json', {})
    movies, warnings = [], []
    for server in servers:
        try:
            plex.JOB['message'] = 'Scanning movies on ' + server['name'] + '…'
            movies.extend(scan_server(server, metadata))
        except Exception as exc:
            warnings.append(server['name'] + ': movie scan unavailable (' + type(exc).__name__ + ').')
    # An incomplete refresh must not replace running channels with partial pools.
    if warnings:
        return dict(status='warning', count=len(read('channels.json', [])), warnings=warnings)
    with LOCK:
        old = {r['id']: r for r in read('channels.json', [])}
        channels = []
        for row, films in select_channels(movies):
            for index, film in enumerate(films):
                film['episode'] = index + 1
            state = new_schedule(films, [], now, secrets.token_hex(16), ads={'enabled': False})
            extend_schedule(state, now + 6 * 86400)
            save_schedule(root() / (row['id'] + '.json'), state)
            channels.append({**row, 'enabled': old.get(row['id'], {}).get('enabled', True)})
        save_schedule(root() / 'catalog.json', dict(count=len({m['id'] for m in movies}), updated_at=now))
        save_schedule(root() / 'channels.json', channels)
    return dict(status='success', count=len(channels), movies=len({m['id'] for m in movies}), warnings=[])


def channel(identity):
    if not enabled():
        raise ValueError('Movie channels are disabled.')
    row = next((r for r in read('channels.json', []) if r['id'] == identity and r.get('enabled')), None)
    if not row:
        raise ValueError('Movie channel unavailable.')
    return row


def schedule(identity, now=None):
    channel(identity)
    now = time.time() if now is None else now
    with LOCK:
        state = read(identity + '.json', None)
        if not state:
            raise ValueError('Movie schedule unavailable.')
        if state['cursor'] < now + 5 * 86400:
            extend_schedule(state, now + 6 * 86400)
            trim_schedule(state, now - 86400)
            save_schedule(root() / (identity + '.json'), state)
        return state


def payload():
    channels=[]
    for row in read('channels.json', []):
        state=read(row['id']+'.json',{}) if row['id']=='just-released' else {}
        films=[dict(title=m['title'],year=m.get('year',''),release_date=m.get('release_date','')) for m in state.get('episodes',[])]
        channels.append({**row,'movies':films})
    return dict(enabled=enabled(), catalog=read('catalog.json', {}), channels=channels)


def toggle(identity, value):
    if not isinstance(value, bool):
        raise ValueError('Invalid movie channel switch.')
    with LOCK:
        rows = read('channels.json', [])
        row = next((r for r in rows if r['id'] == identity), None)
        if not row:
            raise ValueError('Movie channel not found.')
        row['enabled'] = value
        save_schedule(root() / 'channels.json', rows)
