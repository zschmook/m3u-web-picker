"""Plex PIN sign-in. Account and server credentials never leave the backend."""
import ipaddress
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from media.scheduled_channel import save_schedule

LOCK = threading.RLock()
FLOWS = {}
PRODUCT = 'M3U Web Picker'
LIFETIME = 900


class PlexAuthError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _account():
    import custom_channels
    return custom_channels.read('plex-account.json', {})


def _save(account):
    import custom_channels
    path = custom_channels.root() / 'plex-account.json'
    save_schedule(path, account)
    path.chmod(0o600)


def public_account():
    account = _account()
    return dict(signed_in=bool(account.get('token')), name=account.get('name', ''))


def _request(path, client_id, token='', method='GET'):
    url = 'https://plex.tv/api/v2/' + path
    if path.startswith('resources?'):
        url = 'https://clients.plex.tv/api/v2/' + path
    headers = {'Accept': 'application/json', 'X-Plex-Product': PRODUCT,
               'X-Plex-Client-Identifier': client_id}
    if token:
        headers['X-Plex-Token'] = token
    req = urllib.request.Request(url, headers=headers, method=method,
                                 data=b'' if method == 'POST' else None)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('Oversized response')
        return json.loads(data)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PlexAuthError('Plex authorization expired. Sign in with Plex again.') from None
        raise PlexAuthError('Plex is unavailable. Try again in a moment.') from None
    except (OSError, ValueError):
        raise PlexAuthError('Could not reach Plex. Check your connection and try again.') from None


def start_sign_in():
    with LOCK:
        now = time.monotonic()
        for key in list(FLOWS):
            if FLOWS[key]['expires'] <= now:
                del FLOWS[key]
        if len(FLOWS) >= 8:
            raise PlexAuthError('Too many Plex sign-ins are pending. Cancel one or try again shortly.')
        account = _account()
        if not account.get('client_id'):
            account['client_id'] = str(uuid.uuid4())
            _save(account)
        client_id = account['client_id']
        pin = _request('pins?strong=true', client_id, method='POST')
        if not isinstance(pin, dict) or not str(pin.get('id', '')).isdigit() or not pin.get('code'):
            raise PlexAuthError('Plex could not start sign-in. Try again.')
        handle = secrets.token_urlsafe(32)
        try:
            lifetime = min(LIFETIME, max(1, int(pin.get('expiresIn', LIFETIME))))
        except (ValueError, TypeError):
            raise PlexAuthError('Plex could not start sign-in. Try again.') from None
        FLOWS[handle] = dict(pin=str(pin['id']), client_id=client_id,
                             expires=now + lifetime, checked=-10, checking=False)
        query = urllib.parse.urlencode({'clientID': client_id, 'code': str(pin['code']),
                                        'context[device][product]': PRODUCT})
        return dict(flow_id=handle, auth_url='https://app.plex.tv/auth#?' + query,
                    expires_in=lifetime)


def cancel_sign_in(handle):
    with LOCK:
        FLOWS.pop(str(handle), None)


def check_sign_in(handle):
    handle = str(handle)
    with LOCK:
        flow = FLOWS.get(handle)
        if not flow or flow['expires'] <= time.monotonic():
            FLOWS.pop(handle, None)
            return dict(status='expired')
        if flow.get('complete'):
            return dict(status='complete', account=public_account())
        if flow['checking'] or time.monotonic() - flow['checked'] < 2:
            return dict(status='pending')
        flow.update(checked=time.monotonic(), checking=True)
    try:
        pin = _request('pins/' + flow['pin'], flow['client_id'])
        token = pin.get('authToken') if isinstance(pin, dict) else None
        if not isinstance(token, str) or not token:
            return dict(status='pending')
        user = _request('user', flow['client_id'], token)
        if not isinstance(user, dict):
            raise PlexAuthError('Plex could not confirm sign-in. Try again.')
        with LOCK:
            if FLOWS.get(handle) is not flow or flow['expires'] <= time.monotonic():
                return dict(status='expired')
            _save(dict(client_id=flow['client_id'], token=token,
                       name=str(user.get('title') or user.get('username') or 'Plex account')))
            flow['complete'] = True
        return dict(status='complete', account=public_account())
    finally:
        with LOCK:
            flow['checking'] = False


def _connection_url(connection):
    if not isinstance(connection, dict):
        return ''
    url = str(connection.get('uri', '')).rstrip('/')
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.hostname.lower()=='localhost':
            return ''
        if parts.path not in ('', '/') or parts.query or parts.fragment or not 1 <= (parts.port or 80) <= 65535:
            return ''
        try:
            address = ipaddress.ip_address(parts.hostname)
        except ValueError:
            address = None
        if address and (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast):
            return ''
    except ValueError:
        return ''
    return url


def _resources():
    account = _account()
    if not account.get('token'):
        raise PlexAuthError('Sign in with Plex to choose a server.')
    try:
        resources = _request('resources?includeHttps=1&includeRelay=0', account['client_id'], account['token'])
    except PlexAuthError as exc:
        if 'authorization expired' in str(exc):
            with LOCK:
                current = _account()
                if current.get('token') == account['token']:
                    current.pop('token', None)
                    current.pop('name', None)
                    _save(current)
        raise
    if not isinstance(resources, list):
        raise PlexAuthError('Plex could not list servers. Try refreshing the server list.')
    servers = []
    for resource in resources[:100]:
        if not isinstance(resource, dict) or 'server' not in str(resource.get('provides', '')).split(','):
            continue
        identity = resource.get('clientIdentifier')
        token = resource.get('accessToken')
        connections = resource.get('connections') or []
        if not isinstance(connections, list):
            continue
        connections = [item for item in connections if isinstance(item, dict) and not item.get('relay') and _connection_url(item)]
        connections.sort(key=lambda item: (not item.get('local'), item.get('protocol') != 'https'))
        if identity and token and connections:
            servers.append(dict(id=str(identity), name=str(resource.get('name') or 'Plex server'),
                                token=token, connections=connections[:4]))
    return servers


def account_servers():
    return sorted([dict(id=s['id'], name=s['name']) for s in _resources()],
                  key=lambda item: item['name'].casefold())


def connect_server(identity):
    import custom_channels
    if not custom_channels.SCAN_LOCK.acquire(blocking=False):
        raise PlexAuthError('A Plex scan is already running. Try connecting after it finishes.')
    try:
        server = next((s for s in _resources() if s['id'] == str(identity)), None)
        if not server:
            raise PlexAuthError('That server is no longer authorized. Refresh the server list.')
        for connection in server['connections']:
            try:
                custom_channels.connect_server(_connection_url(connection), server['token'], expected_identity=server['id'])
                return
            except ValueError:
                continue
        raise PlexAuthError('Could not reach this Plex server. Check that it is online, or use the manual connection below.')
    finally:
        custom_channels.SCAN_LOCK.release()
