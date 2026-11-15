"""Team lighting preferences share Sports Automation's catalog identities."""
from contextlib import closing, contextmanager
import colorsys
import json
import re
import sqlite3
import core
import sports


def teams():
    selected = {r['scope_id'] for r in sports.get_rules(core.DB_PATH)
                if r['scope_type'] == 'team' and r['enabled']}
    return sorted(sports.catalog_payload(core.DB_PATH, scope_type='team'),
                  key=lambda t: (t['id'] not in selected, t['name'].casefold()))


@contextmanager
def _connection():
    connection = sqlite3.connect(core.DB_PATH)
    connection.execute('CREATE TABLE IF NOT EXISTS sports_lighting_rooms (room_id TEXT PRIMARY KEY, teams_json TEXT NOT NULL)')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def preferences(room_id):
    with _connection() as connection:
        row = connection.execute('SELECT teams_json FROM sports_lighting_rooms WHERE room_id=?', (room_id,)).fetchone()
    return json.loads(row[0]) if row else []


def save(room_id, values):
    allowed = {t['id'] for t in teams()}
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError('Choose up to 100 teams.')
    result = []
    for value in values:
        if (not isinstance(value, dict) or set(value) != {'id', 'color'}
                or value['id'] not in allowed or any(t['id'] == value['id'] for t in result)
                or not isinstance(value['color'], str) or not re.fullmatch('#[0-9a-fA-F]{6}', value['color'])):
            raise ValueError('Choose unique teams from Sports Automation and valid colors.')
        result.append({'id': value['id'], 'color': value['color'].lower()})
    with _connection() as connection:
        connection.execute('INSERT INTO sports_lighting_rooms VALUES (?,?) ON CONFLICT(room_id) DO UPDATE SET teams_json=excluded.teams_json',
                           (room_id, json.dumps(result)))
    return result


def apply(settings, play_url, is_sports=False, programme=None):
    chosen = preferences(settings.get('id', ''))
    if not chosen or not play_url:
        return {**settings, 'enabled': False} if is_sports and chosen else settings
    match = re.search(r'/(?:guide/play/sports|sports/stream)/(\d+)(?:$|[/?])', play_url)
    if match:
        with closing(sqlite3.connect(core.DB_PATH)) as connection:
            row = connection.execute('SELECT event_title, display_name, league_id FROM sports_generated WHERE assigned_number=?',
                                     (int(match[1]),)).fetchone()
    elif is_sports and programme:
        row = (programme.get('title', ''), programme.get('subtitle', ''), '')
    else:
        return {**settings, 'enabled': False} if is_sports else settings
    catalog = {t['id']: t for t in teams()}
    for selected in chosen:
        team = catalog.get(selected['id'])
        if not row or not team or row[2] and team.get('league_id') != row[2]:
            continue
        names = [team['name'], *team.get('aliases', [])]
        if not any(isinstance(name, str) and len(name) >= 4 and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', ' '.join(str(v or '') for v in row[:2]), re.I) for name in names):
            continue
        color = selected['color'].lstrip('#')
        h, s, _ = colorsys.rgb_to_hsv(*(int(color[i:i+2], 16)/255 for i in (0, 2, 4)))
        return {**settings, 'sports_hsv': {'hue': round(h*360)%360, 'saturation': round(s*100)}, 'sports_team': team['name']}
    return {**settings, 'enabled': False}
