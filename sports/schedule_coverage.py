"""Match selected competitions to API-SPORTS' cached coverage catalogues."""
from contextlib import closing
from datetime import datetime, timedelta
import json
import urllib.request
import sports as _s

PRODUCTS = {p: f'https://v1.{p}.api-sports.io' for p in ('basketball', 'hockey', 'volleyball')}
CATALOG_KEY = 'league-catalogue-v1'
NAME_ALIASES = {'ncaab-men': ('NCAA',), 'ncaa-hockey': ('NCAA',), 'nba-g-league': ('NBA - G League',)}


def catalogue(db_path, product):
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute('SELECT raw_json,fetched_at FROM sports_schedule_reference_cache WHERE source=? AND cache_key=? AND season=0',
                           ('api-sports-' + product, CATALOG_KEY)).fetchone()
    return (_s._json_load(row['raw_json'], []), row['fetched_at']) if row else (None, None)


def selected_products(db_path):
    if _s.get_settings(db_path).get('everything_mode'):
        return set(PRODUCTS)
    catalog = {(item['scope_type'], item['id']): item for item in _s.catalog_payload(db_path)}
    result = set()
    for rule in _s.get_rules(db_path):
        if not rule.get('enabled'):
            continue
        item = catalog.get((rule['scope_type'], rule['scope_id']), {})
        sport = rule['scope_id'] if rule['scope_type'] == 'sport' else (
            (item.get('metadata') or {}).get('sport_id') or _s.LEAGUE_SPORTS.get(_s._schedule_api_rule_league_id(rule, catalog), ''))
        if sport in PRODUCTS:
            result.add(sport)
    return result


def refresh_coverage(db_path, *, api_key, now=None, cancel_check=None, force=False):
    now = now or datetime.now().astimezone()
    failures = []
    for product in sorted(selected_products(db_path)):
        _s._raise_if_cancelled(cancel_check)
        _, fetched = catalogue(db_path, product)
        if fetched and not force:
            try:
                if now - datetime.fromisoformat(fetched) < timedelta(days=7):
                    continue
            except ValueError:
                pass
        opener = urllib.request.build_opener()
        opener.addheaders = []
        try:
            request = urllib.request.Request(PRODUCTS[product] + '/leagues', headers={'x-apisports-key': api_key})
            with opener.open(request, timeout=20) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('Coverage response too large')
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('response'), list):
                raise ValueError('Coverage catalogue unavailable')
            _s._raise_if_cancelled(cancel_check)
            with closing(_s._connect(db_path)) as conn:
                conn.execute('INSERT OR REPLACE INTO sports_schedule_reference_cache(source,cache_key,season,fetched_at,raw_json) VALUES (?,?,0,?,?)',
                             ('api-sports-' + product, CATALOG_KEY, now.isoformat(), json.dumps(payload['response'])))
                conn.commit()
        except _s.ScanCancelled:
            raise
        except Exception as exc:
            # Do not retain credentials or upstream error text.
            failures.append(f'{product.title()} league coverage could not be checked ({type(exc).__name__}).')
    return failures


def available_datasets(db_path, now=None):
    datasets = {key: dict(value) for key, value in _s.SCHEDULE_API_DATASETS.items()}
    now = now or datetime.now().astimezone()
    leagues = _s.catalog_payload(db_path, scope_type='league')
    for product in PRODUCTS:
        rows, _ = catalogue(db_path, product)
        if rows is None:
            continue
        for item in leagues:
            sport = (item.get('metadata') or {}).get('sport_id') or _s.LEAGUE_SPORTS.get(item['id'])
            if sport != product:
                continue
            matches = []
            # Prefer current names over historical aliases, e.g. FPHL over FHL.
            for name in (item['name'], item['id'], *NAME_ALIASES.get(item['id'], ()), *(item.get('aliases') or [])):
                matches = [r for r in rows if isinstance(r, dict) and _s._normalize(str(r.get('name') or '')) == _s._normalize(name)]
                if matches:
                    break
            if len(matches) != 1 or not matches[0].get('id'):
                continue
            row = matches[0]
            try:
                remote_id = int(row['id'])
            except (ValueError, TypeError):
                continue
            if remote_id <= 0:
                continue
            cutoff = now.year - (1 if now.month <= 6 else 0)
            years = []
            for season in row.get('seasons') or []:
                try:
                    years.append(int(str(season.get('season', ''))[:4]))
                except (ValueError, TypeError, AttributeError):
                    pass
            if not any(y >= cutoff for y in years):
                # An old-only league must not suppress current provider events.
                datasets.pop(item['id'], None)
                continue
            league = item['id']
            datasets[league] = dict(id=league, label=item['name'], product=product,
                source='api-sports-' + product, base_url=PRODUCTS[product], league_id=league,
                remote_league_id=remote_id, sport_id=product, season_mode='winter',
                request_mode='standard_games')
    return datasets
