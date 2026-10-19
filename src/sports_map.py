"""Generated-channel sports geography shared by native clients.

Picker owns membership and playback. Public metadata may locate or describe an
existing game, but can never add a channel or supply a playback URL.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DATA = Path(__file__).resolve().parents[1] / 'static' / 'sports-map'
REGIONS = json.loads((DATA / 'regions.json').read_text(encoding='utf-8'))
TEAMS = json.loads((DATA / 'teams.json').read_text(encoding='utf-8'))['teams']
REGION_IDS = {s['id'] for s in REGIONS}
FEEDS = {
    'NFL': ('football/nfl', ''), 'NCAA football': ('football/college-football', '&groups=80'),
    'NBA': ('basketball/nba', ''), 'WNBA': ('basketball/wnba', ''),
    'NCAA men': ('basketball/mens-college-basketball', ''),
    'NCAA women': ('basketball/womens-college-basketball', ''),
    'NHL': ('hockey/nhl', ''), 'MLB': ('baseball/mlb', ''),
    'MLS': ('soccer/usa.1', ''), 'NWSL': ('soccer/usa.nwsl', ''),
}
ET = ZoneInfo('America/New_York')
ID_RE = re.compile(r'm3u-picker-sports-[a-f0-9]{1,64}')


def normal(value):
    text = unicodedata.normalize('NFKD', str(value or '').replace('ᴸᶦᵛᵉ', '').replace('ᴺᵉʷ', ''))
    return re.sub(r'[^a-z0-9]+', ' ', text.encode('ascii', 'ignore').decode().lower()).strip()


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0


def generated_channel(channel):
    try:
        number = int(channel.get('number', 0))
        return (channel.get('generated') is True and number > 0
                and str(channel.get('number')) == str(number)
                and channel.get('play_url') == f'/guide/play/sports/{number}'
                and ID_RE.fullmatch(str(channel.get('tvg_id', ''))) is not None)
    except (TypeError, ValueError):
        return False


def split_name(channel):
    league, _, body = str(channel.get('name', '')).partition('•')
    title, sep, feed = body.rpartition('—')
    return league.strip(), (title if sep else body).strip(), (feed if sep else channel.get('subtitle', '')).strip()


def sport_for(league):
    for pattern, sport in [(r'nfl|football', 'Football'), (r'\b(nhl|ahl|echl|sphl|fphl)\b|hockey', 'Hockey'),
                           (r'mlb|baseball', 'Baseball'), (r'nba|wnba|basketball', 'Basketball'),
                           (r'volleyball', 'Volleyball'), (r'pga|golf', 'Golf'),
                           (r'mls|nwsl|soccer', 'Soccer'), (r'track|athletics', 'Athletics')]:
        if re.search(pattern, normal(league)):
            return sport
    return 'Other'


def region_for(venue):
    address = venue.get('address') or {}
    country = normal(address.get('country'))
    canada = country in ('canada', 'ca', 'can')
    if country and not canada and country not in ('us', 'usa', 'united states', 'united states of america'):
        return 'INTL'
    for value in (address.get('state'), ','.join(str(address.get('city', '')).split(',')[1:]).strip()):
        for region in REGIONS:
            if region.get('auxiliary') or (country and (region.get('country') == 'CA') != canada):
                continue
            if value and normal(value) in (normal(region['id']), normal(region['name'])):
                return region['id']
    return 'IL' if str(venue.get('id')) == '10469' else '??'


ABBREVIATIONS = dict(zip(
    'N.C.|S.C.|N.J.|N.Y.|N.M.|N.D.|S.D.|W.Va.|Calif.|Fla.|Ore.|Pa.|Mass.|Mich.|Minn.|Ill.|Ind.|Tenn.|Tex.|Va.|Wash.|Wis.|Colo.|Ariz.|Nev.|Mo.|Ala.|Ark.|Conn.|Del.|Ga.|Kan.|Ky.|La.|Md.|Miss.|Okla.|Vt.'.split('|'),
    'NC SC NJ NY NM ND SD WV CA FL OR PA MA MI MN IL IN TN TX VA WA WI CO AZ NV MO AL AR CT DE GA KS KY LA MD MS OK VT'.split()))


def description_location(description):
    clause = re.search(r'(?:^|[.\n•])\s*From\s+([^\n•]+)', str(description or ''), re.I)
    if not clause:
        return None
    text = clause[1].strip()
    if re.search(r'\b(China|England|United Kingdom|Australia|Japan|France|Germany|Spain|Mexico|Scotland|Ireland)\b', text, re.I):
        return dict(region='INTL', venue=text, city='', locationSource='Picker programme venue')
    places = re.split(r'\bin\s+', text, flags=re.I)[-1]
    ascii_places = unicodedata.normalize('NFKD', places).encode('ascii', 'ignore').decode()
    canada = bool(re.search(r'\bCanada\b', text, re.I))
    for region in sorted(REGIONS, key=lambda s: -len(s['name'])):
        if region.get('auxiliary') or (canada and region.get('country') != 'CA'):
            continue
        if (re.search(r'(?:^|,|\bin)\s*' + re.escape(normal(region['name'])) + r'(?:[.,\s]|$)', ascii_places, re.I)
                or re.search(r',\s*' + region['id'] + r'(?:[.\s,]|$)', places)):
            return dict(region=region['id'], venue=text, city=places.split(',')[0].strip(), locationSource='Picker programme venue')
    if not canada:
        for abbreviation, region in ABBREVIATIONS.items():
            if ', ' + abbreviation in places:
                return dict(region=region, venue=text, city=places.split(',')[0].strip(), locationSource='Picker programme venue')
    return None


def home_candidate(league, title, description='', record=None, programme=None, meta=None):
    league_key = normal(league)
    scope = 'ncaa' if re.search(r'\b(ncaa|college)\b', league_key) else next(
        (s for s in ('nhl', 'ahl', 'echl', 'sphl', 'fphl') if re.search(r'\b' + s + r'\b', league_key)), '')
    if not scope:
        return None
    record, programme = record or {}, programme or {}
    explicit = (meta or {}).get('home', {}).get('name') or record.get('home_team_name') or programme.get('home_team_name')
    pair = re.search(r'^(.+?)\s+(?:at|@|vs\.?|versus|v\.)\s+(.+?)$', title.split(':')[-1].strip(), re.I)
    selection = 'explicit home team'
    if not explicit and pair:
        if re.search(r'\((home|h)\)', pair[1], re.I):
            explicit = pair[1]
        elif re.search(r'\((home|h)\)', pair[2], re.I):
            explicit = pair[2]
    if not explicit:
        marked = re.search(r'\bHome broadcast\s*[•:—-]\s*([^•\n]+)', description, re.I)
        if marked:
            explicit = marked[1].strip().rstrip('.')
    if not explicit and pair:
        explicit, selection = pair[2], 'second listed team'
    if not explicit:
        return None
    name = re.sub(r'\s*\((home|away|h|a)\)', '', str(explicit), flags=re.I).strip().rstrip('.')
    return dict(scope=scope, name=name, selection=selection)


def home_location(candidate, teams):
    if not candidate:
        return None
    matches = [t for t in teams if t['scope'] == candidate['scope'] and
               normal(candidate['name']) in {normal(v) for v in [t['name'], *t.get('aliases', [])]}]
    if not matches or len({t['region'] for t in matches}) != 1:
        return None
    team = matches[0]
    return dict(region=team['region'], venue=team['name'] + ' home region (fallback)', city=team.get('city', ''),
                locationSource='Home-team fallback · ' + candidate['selection'], locationInferred=True,
                locationHomeTeam=team['name'])


def compact_events(league, data):
    result = []
    for event in data.get('events', []):
        competition = (event.get('competitions') or [{}])[0]
        competitors = competition.get('competitors', [])
        def team(side):
            item = next((c for c in competitors if c.get('homeAway') == side), {})
            t = item.get('team') or {}
            return dict(name=t.get('displayName', ''), score=str(item.get('score', '')),
                        aliases=[t.get(k) for k in ('displayName', 'shortDisplayName', 'name') if t.get(k)])
        home, away = team('home'), team('away')
        if not home['name'] or not away['name']:
            continue
        result.append(dict(id=league + ':' + str(event.get('id')), league=league, date=event.get('date'),
                           name=event.get('shortName', ''), venue=competition.get('venue') or {}, home=home, away=away,
                           state=event.get('status', {}).get('type', {}).get('state'),
                           clock=event.get('status', {}).get('type', {}).get('shortDetail', '')))
    return result


def match_event(league, title, kickoff, events):
    text = ' ' + normal(title) + ' '
    def contains(team):
        return any(len(normal(a)) >= 4 and ' ' + normal(a) + ' ' in text for a in team.get('aliases', []))
    choices = [e for e in events if normal(e.get('league')) == normal(league) and contains(e['home'])
               and contains(e['away']) and (not kickoff or abs(stamp(e['date']) - kickoff) <= 14400)]
    return min(choices, key=lambda e: abs(stamp(e['date']) - kickoff), default=None)


def make_map(channels, generated=(), events=(), teams=TEAMS, now=None):
    now = datetime.now(timezone.utc) if now is None else now
    records = {int(r['assigned_number']): r for r in generated}
    regions = [dict(r, games=[]) for r in REGIONS]
    by_region = {r['id']: r for r in regions}
    groups = {}
    rows = [c for c in channels if generated_channel(c)]
    for channel in rows:
        league, title, feed_name = split_name(channel)
        stored = records.get(int(channel['number']), {})
        record = stored if stored.get('display_name') == channel.get('name') else {}
        kickoff = stamp(record.get('event_start'))
        candidates = [p for p in [channel.get('now'), *channel.get('upcoming', []), channel.get('next')]
                      if p and p.get('title') and not p['title'].startswith('Upcoming:') and not p['title'].endswith('— Event window')]
        if kickoff:
            candidates.sort(key=lambda p: abs(stamp(p.get('start')) - kickoff))
        programme = next(iter(candidates), channel.get('now') or {})
        kickoff = kickoff or stamp(programme.get('start'))
        description = str(programme.get('description') or '')
        first = description.split('\n')[0]
        if not re.search(r'\s(at|vs\.?|@)\s', title, re.I) and re.fullmatch(r'[^.:]{2,60}\s(at|vs\.?)\s[^.:]{2,60}', first):
            title = first
        meta = match_event(league, title, kickoff, events)
        located = None
        if meta and region_for(meta['venue']) != '??':
            located = dict(region=region_for(meta['venue']), venue=meta['venue'].get('fullName', ''),
                           city=meta['venue'].get('address', {}).get('city', ''), locationSource='Matched game venue')
        located = located or description_location(description) or home_location(
            home_candidate(league, title, description, record, programme, meta), teams) or {}
        region = located.get('region', '??')
        if region not in by_region:
            region = '??'
        identity = normal(title.split(':')[-1])
        if sport_for(league) == 'Golf' and located:
            identity = normal(located['venue'])
        key = meta['id'] if meta else f'{normal(league)}|{identity}|{kickoff}'
        if key not in groups:
            ended = str((channel.get('now') or {}).get('title', '')).endswith('— Event window') or (meta or {}).get('state') == 'post'
            phase = 'live' if (meta or {}).get('state') == 'in' else 'ended' if ended else 'on-air' if kickoff <= now.timestamp() < stamp(programme.get('stop')) else 'upcoming'
            clock = datetime.fromtimestamp(kickoff, ET).strftime('%I:%M %p %Z').lstrip('0') if kickoff else 'Time not listed'
            if phase == 'live':
                clock = meta['clock']
            elif phase == 'on-air':
                clock = 'On air'
            elif phase == 'ended':
                clock = 'Event ended'
            groups[key] = dict(id='picker-' + hashlib.sha256(key.encode()).hexdigest()[:24], title=title,
                name=(meta or {}).get('name') or title, league=league, sport=sport_for(league), region=region,
                phase=phase, live=phase == 'live', clock=clock, kickoff=kickoff,
                statusLabel={'live': 'LIVE', 'on-air': 'ON AIR', 'ended': 'EVENT WINDOW', 'upcoming': 'UPCOMING'}[phase],
                score=(meta['away']['score'] + '–' + meta['home']['score']) if meta and meta.get('state') in ('in', 'post') else '',
                venue=located.get('venue', 'Location not provided'), locationSource=located.get('locationSource', 'Needs venue metadata'),
                locationInferred=located.get('locationInferred', False), feeds=[])
        start, stop = stamp(programme.get('start')), stamp(programme.get('stop'))
        groups[key]['feeds'].append(dict(id=channel['tvg_id'], number=int(channel['number']), name=feed_name or 'Generated feed',
            available=channel.get('available'), play_url=channel['play_url'], generated_id=channel['tvg_id'],
            is_sports=True, programmes=[dict(title=programme.get('title', title), description=description[:2000],
                start=int(start), stop=int(stop), restart_url='', subtitle='')]))
    for game in groups.values():
        game['feeds'].sort(key=lambda f: f['number'])
        by_region[game['region']]['games'].append(game)
    for region in regions:
        region['games'].sort(key=lambda g: (g['phase'] not in ('live', 'on-air'), g['kickoff'], g['title']))
    return dict(source='picker-generated', states=regions, totalGames=len(groups), totalChannels=len(rows),
                updatedAt=now.isoformat(), refreshSeconds=30, metadataRefreshSeconds=1800)


def fetch_json(url):
    with urlopen(Request(url, headers={'Accept': 'application/json', 'User-Agent': 'M3U-Picker-SportsMap/1'}), timeout=8) as response:
        data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('Sports metadata too large')
        return json.loads(data)


class DailyMapCache:
    """Persist the prepared daily map; revalidate live data off request threads.

    A scan is visible on the next 30-second refresh. Daily rollover also forces
    revalidation, while the last good map remains usable during a slow rebuild.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.loading = False
        self.worker = None
        self.cached = None
        self.at = 0
        self.day = ''
        self.last_attempt = 0
        self.failed = False
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            payload = saved['payload']
            if (saved['version'] == 1 and payload['source'] == 'picker-generated'
                    and {r['id'] for r in payload['states']} == REGION_IDS):
                self.cached, self.at, self.day = payload, float(saved['at']), saved['day']
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def snapshot(self, build):
        now = time.time()
        day = datetime.fromtimestamp(now, ET).date().isoformat()
        with self.lock:
            stale = self.cached is None or day != self.day or now - self.at >= 30
            if stale and not self.loading and now - self.last_attempt >= 5:
                self.loading, self.last_attempt = True, now
                self.worker = threading.Thread(target=self.refresh, args=(build,), daemon=True,
                                               name='sports-map-daily-cache')
                self.worker.start()
            payload = dict(self.cached) if self.cached is not None else dict(
                source='picker-generated', states=[dict(r, games=[]) for r in REGIONS],
                totalGames=0, totalChannels=0, updatedAt='', refreshSeconds=30,
                metadataRefreshSeconds=1800, metadataLoading=False, metadataStale=False)
            payload.update(cacheLoading=self.loading, cacheStale=stale,
                           cacheRefreshFailed=self.failed, cacheDay=self.day)
            return payload

    def refresh(self, build):
        try:
            payload = build()
            now = time.time()
            day = datetime.fromtimestamp(now, ET).date().isoformat()
            with self.lock:
                self.cached, self.at, self.day, self.failed = payload, now, day, False
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(dict(version=1, at=now, day=day, payload=payload)), encoding='utf-8')
                temporary.replace(self.path)
            except OSError:
                logging.getLogger(__name__).warning('Could not persist the prepared sports map')
        except Exception:
            with self.lock:
                self.failed = True
            logging.getLogger(__name__).warning('Sports map refresh failed; retaining the last good map')
        finally:
            with self.lock:
                self.loading = False


class MetadataCache:
    """No request-thread network waits; failed enrichment retains last good data."""
    def __init__(self, path, fetch=fetch_json):
        self.path, self.fetch = Path(path), fetch
        self.lock, self.loading = threading.Lock(), False
        self.cached = dict(at=0, leagues=[], events=[], teams=[], lookups={})
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(saved.get('events'), list) and isinstance(saved.get('teams'), list):
                self.cached.update(saved)
        except (OSError, ValueError, TypeError):
            pass

    def snapshot(self, channels):
        needed = {split_name(c)[0] for c in channels if generated_channel(c)} & FEEDS.keys()
        candidates = []
        for c in channels:
            if generated_channel(c):
                league, title, _ = split_name(c)
                p = c.get('now') or {}
                candidate = home_candidate(league, title, p.get('description', ''))
                if candidate:
                    candidates.append(candidate)
        with self.lock:
            result = dict(self.cached)
            due = time.time() - self.cached['at'] >= 1800 or not needed <= set(self.cached['leagues'])
            missing = any(not home_location(c, TEAMS + self.cached['teams']) and
                          time.time() - self.cached['lookups'].get(c['scope'] + ':' + normal(c['name']), 0) >= 3600 for c in candidates)
            if not self.loading and (due or missing):
                self.loading = True
                threading.Thread(target=self.refresh, args=(needed, candidates), daemon=True, name='sports-map-metadata').start()
            result['loading'] = self.loading
        return result

    def refresh(self, needed, candidates):
        try:
            with self.lock:
                old = dict(self.cached)
            local = datetime.now(ET)
            dates = [(local - timedelta(days=1)).strftime('%Y%m%d'), local.strftime('%Y%m%d')]
            def board(job):
                league, day = job
                route, query = FEEDS[league]
                try:
                    data = self.fetch(f'https://site.api.espn.com/apis/site/v2/sports/{route}/scoreboard?dates={day}&limit=1000{query}')
                    if not isinstance(data.get('events'), list):
                        return league, None
                    return league, compact_events(league, data)
                except (OSError, ValueError, TypeError, KeyError):
                    return league, None
            with ThreadPoolExecutor(max_workers=3) as pool:
                replies = list(pool.map(board, [(league, day) for league in sorted(needed) for day in dates]))
            # A failed date must not erase yesterday's still-running games.
            succeeded = {league for league in needed if all(events is not None for name, events in replies if name == league)}
            events = {e['id']: e for e in old['events'] if e['league'] not in succeeded}
            for league, rows in replies:
                if league in succeeded:
                    for event in rows or []:
                        events[event['id']] = event
            teams = list(old['teams'])
            lookups = dict(old['lookups'])
            catalogues = {}
            for candidate in candidates:
                key = candidate['scope'] + ':' + normal(candidate['name'])
                if home_location(candidate, TEAMS + teams) or time.time() - lookups.get(key, 0) < 3600:
                    continue
                routes = ['football/college-football', 'basketball/mens-college-basketball', 'hockey/mens-college-hockey'] if candidate['scope'] == 'ncaa' else ['hockey/nhl'] if candidate['scope'] == 'nhl' else []
                for route in routes:
                    try:
                        if route not in catalogues:
                            catalogues[route] = self.fetch(f'https://site.api.espn.com/apis/site/v2/sports/{route}/teams?limit=1000')
                        rows = catalogues[route]['sports'][0]['leagues'][0]['teams']
                        def aliases(t):
                            return [t.get(k, '') for k in ('displayName', 'location', 'shortDisplayName', 'abbreviation')] + ({'Ole Miss Rebels': ['Mississippi'], 'Pittsburgh Panthers': ['Pitt']}.get(t.get('displayName'), []))
                        matches = [r['team'] for r in rows if normal(candidate['name']) in {normal(a) for a in aliases(r['team'])}]
                        if len(matches) != 1 or not re.fullmatch(r'\d+', str(matches[0]['id'])):
                            continue
                        t = matches[0]
                        family, league = route.split('/')
                        season = local.year - (local.month < 3) if family == 'football' else local.year + (local.month >= 8)
                        url = f'https://sports.core.api.espn.com/v2/sports/{family}/leagues/{league}/seasons/{season}/teams/{t["id"]}?lang=en&region=us'
                        detail = self.fetch(url)
                        region = region_for(detail.get('venue') or {})
                        if region == '??':
                            continue
                        teams.append(dict(scope=candidate['scope'], name=t['displayName'], aliases=aliases(t), region=region, source=url))
                        break
                    except (OSError, ValueError, TypeError, KeyError, IndexError):
                        continue
                lookups[key] = time.time()
            cached = dict(at=time.time(), leagues=sorted(needed), events=list(events.values()), teams=teams, lookups=lookups,
                          stale=not needed <= succeeded)
            with self.lock:
                self.cached = cached
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(cached), encoding='utf-8')
                temporary.replace(self.path)
            except OSError:
                pass
        finally:
            with self.lock:
                self.loading = False
