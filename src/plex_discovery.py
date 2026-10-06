"""Bounded, on-demand Plex discovery when Docker cannot receive LAN broadcasts."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import socket
import time
import urllib.parse
import urllib.request
from xml.etree import ElementTree as ET


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _private_address(host):
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return False
    return any(address in ipaddress.ip_network(prefix) for prefix in
               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def _identity(url, name=''):
    try:
        with urllib.request.build_opener(NoRedirect).open(url + '/identity', timeout=2) as response:
            metadata = ET.fromstring(response.read(8192))
        identity = metadata.get('machineIdentifier')
        if metadata.tag != 'MediaContainer' or not identity:
            return None
        host = urllib.parse.urlsplit(url).hostname
        return dict(id=identity, name=metadata.get('friendlyName') or name or f'Plex at {host}', url=url)
    except (OSError, ValueError, ET.ParseError):
        return None


def discover_lan_servers(lan_host):
    try:
        address = ipaddress.IPv4Address(lan_host)
    except ipaddress.AddressValueError:
        return []
    if not _private_address(lan_host):
        return []
    network = ipaddress.ip_network(f'{address}/24', strict=False)

    def probe(host):
        url = f'http://{host}:32400'
        try:
            with socket.create_connection((str(host), 32400), timeout=.35):
                pass
            return _identity(url)
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=32) as pool:
        found = [server for server in pool.map(probe, network.hosts()) if server]
    return found


def discover_plex_servers(lan_host):
    """List identities without authentication, catalog updates or channel toggles."""
    found = {server['id']: server for server in discover_lan_servers(lan_host)}
    candidates = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(.2)
            for host in ('239.0.0.250', '255.255.255.255'):
                sock.sendto(b'M-SEARCH * HTTP/1.0\r\n\r\n', (host, 32414))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and len(candidates) < 60:
                try:
                    payload, remote = sock.recvfrom(8192)
                except socket.timeout:
                    continue
                if not _private_address(remote[0]):
                    continue
                headers = {key.strip().lower(): value.strip() for line in payload.decode(errors='replace').splitlines()
                           if ':' in line for key, value in [line.split(':', 1)]}
                port = headers.get('port', '32400')
                if port.isdigit() and 1 <= int(port) <= 65535:
                    candidates[f'http://{remote[0]}:{port}'] = headers.get('name', '')
    except OSError:
        pass
    if candidates:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for server in pool.map(lambda entry: _identity(*entry), candidates.items()):
                if server:
                    found[server['id']] = server
    return sorted(found.values(), key=lambda server: (server['name'].casefold(), server['url']))
