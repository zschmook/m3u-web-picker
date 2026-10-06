import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from xml.etree import ElementTree as ET
from flask import Flask

import custom_channels as channels
import plex_discovery as discovery
from api import custom_channels as routes


class PlexDiscoveryTests(unittest.TestCase):
    def test_lan_fallback_finds_authenticated_plex_without_broadcasts_or_a_token(self):
        def connect(address, **kwargs):
            self.assertEqual(address[1], 32400)
            self.assertLessEqual(kwargs['timeout'], .35)
            if address[0] != '10.0.0.22':
                raise OSError('unreachable')
            return io.BytesIO()

        with patch.object(discovery.socket, 'create_connection', side_effect=connect), patch.object(
            discovery.urllib.request, 'build_opener'
        ) as opener:
            opener.return_value.open.return_value = io.BytesIO(
                b'<MediaContainer machineIdentifier="server-22" version="1.42"/>')
            found = discovery.discover_lan_servers('10.0.0.18')
        self.assertEqual(found, [dict(id='server-22', name='Plex at 10.0.0.22', url='http://10.0.0.22:32400')])
        opener.return_value.open.assert_called_once_with('http://10.0.0.22:32400/identity', timeout=2)
        self.assertNotIn('token', json.dumps(found))

    def test_lan_fallback_does_not_scan_public_loopback_or_invalid_addresses(self):
        with patch.object(discovery.socket, 'create_connection') as connect:
            for host in ('', '127.0.0.1', '8.8.8.8', 'host.example', '::1', '169.254.1.1', '192.0.2.1'):
                self.assertEqual(discovery.discover_lan_servers(host), [])
            connect.assert_not_called()

    def test_open_port_without_plex_identity_is_not_a_server(self):
        def connect(address, **kwargs):
            if address[0] != '10.0.0.22':
                raise OSError('unreachable')
            return io.BytesIO()
        with patch.object(discovery.socket, 'create_connection', side_effect=connect), patch.object(
            discovery.urllib.request, 'build_opener'
        ) as opener:
            for body in (b'not xml', b'<html/>', b'<Other machineIdentifier="fake"/>'):
                opener.return_value.open.return_value = io.BytesIO(body)
                self.assertEqual(discovery.discover_lan_servers('10.0.0.18'), [])

    def test_locked_servers_are_shown_without_saving_an_unauthorized_connection(self):
        detected = [dict(id='locked', name='Plex at 10.0.0.22', url='http://10.0.0.22:32400')]
        with tempfile.TemporaryDirectory() as temp, patch.object(channels, 'root', return_value=Path(temp)), patch.object(
            channels, 'load_settings', return_value=SimpleNamespace(lan_host='10.0.0.18', data_dir=Path(temp))
        ), patch.object(channels, 'discover_lan_servers', return_value=detected), patch.object(
            channels.socket, 'socket', side_effect=OSError('broadcast unavailable')
        ), patch.object(channels, 'plex_xml', side_effect=HTTPError('http://local', 401, 'Unauthorized', {}, None)):
            self.assertEqual(channels.discover_servers(), [])
            self.assertEqual(channels.read('discovered-servers.json', []), detected)
            self.assertEqual(channels.read('servers.json', []), [])

    def test_discovery_preserves_saved_credentials_and_excludes_connected_identity_from_candidates(self):
        server = dict(id='connected', name='Saved', url='http://10.0.0.22:32400', token='private-value')
        with tempfile.TemporaryDirectory() as temp, patch.object(channels, 'root', return_value=Path(temp)), patch.object(
            channels, 'load_settings', return_value=SimpleNamespace(lan_host='10.0.0.18', data_dir=Path(temp))
        ), patch.object(channels, 'discover_lan_servers', return_value=[{k:v for k,v in server.items() if k != 'token'}]), patch.object(
            channels.socket, 'socket', side_effect=OSError('broadcast unavailable')
        ), patch.object(channels, 'plex_xml') as get:
            channels.save_schedule(Path(temp)/'servers.json', [server])
            get.return_value = ET.fromstring('<MediaContainer machineIdentifier="connected" friendlyName="Saved"/>')
            self.assertEqual(channels.discover_servers()[0]['token'], 'private-value')
            self.assertEqual(channels.read('discovered-servers.json', []), [])

    def test_missing_or_expired_token_returns_actionable_error_and_keeps_previous_connection(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(channels, 'root', return_value=Path(temp)), patch.object(
            channels, 'plex_xml', side_effect=HTTPError('http://private-value', 401, 'Unauthorized', {}, None)
        ):
            saved=[dict(id='saved', name='Saved', url='http://10.0.0.22:32400', token='original')]
            channels.save_schedule(Path(temp)/'servers.json', saved)
            with self.assertRaisesRegex(ValueError, 'Plex requires a valid token'):
                channels.connect_server('http://10.0.0.22:32400', 'expired-value')
            self.assertEqual(channels.read('servers.json', []), saved)

    def test_public_identity_does_not_count_as_authorized_library_access(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(channels, 'root', return_value=Path(temp)), patch.object(
            channels, 'plex_xml', side_effect=[ET.fromstring('<MediaContainer machineIdentifier="locked"/>'),
                HTTPError('http://local', 401, 'Unauthorized', {}, None)]
        ):
            with self.assertRaisesRegex(ValueError, 'Plex requires a valid token'):
                channels.connect_server('http://10.0.0.22:32400', '')
            self.assertEqual(channels.read('servers.json', []), [])

    def test_connection_errors_never_echo_private_values(self):
        with patch.object(channels, 'plex_xml', side_effect=ValueError('private-value')):
            with self.assertRaises(ValueError) as result:
                channels.connect_server('http://10.0.0.22:32400', 'private-value')
            self.assertNotIn('private-value', str(result.exception))

    def test_explicit_discovery_works_with_both_channel_options_disabled_and_preserves_catalog(self):
        found=[dict(id='locked',name='Plex at 10.0.0.22',url='http://10.0.0.22:32400')]
        app=Flask(__name__);routes.register_custom_channel_routes(app)
        with tempfile.TemporaryDirectory() as temp, patch.object(channels,'root',return_value=Path(temp)), patch.object(
            channels,'load_settings',return_value=SimpleNamespace(lan_host='10.0.0.18')
        ),patch.object(channels,'discover_plex_servers',return_value=found),patch.object(channels,'scan_server') as scan:
            original_catalog=dict(shows=[],updated_at=1,warnings=[])
            channels.save_schedule(Path(temp)/'catalog.json',original_catalog)
            response=app.test_client().post('/api/custom-channels/servers/discover',json={})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['discovery_count'],1)
            self.assertEqual(response.json['discovered_servers'],found)
            self.assertFalse(response.json['settings']['enabled'])
            self.assertFalse(response.json['settings']['movies_enabled'])
            self.assertEqual(channels.read('catalog.json',{}),original_catalog)
            self.assertEqual(channels.read('servers.json',[]),[])
            scan.assert_not_called()

    def test_discovery_does_not_compete_with_an_active_library_scan(self):
        app=Flask(__name__);routes.register_custom_channel_routes(app)
        with channels.SCAN_LOCK,patch.object(channels,'discover_plex_servers') as discover:
            response=app.test_client().post('/api/custom-channels/servers/discover',json={})
            self.assertEqual(response.status_code,409)
            discover.assert_not_called()

    def test_broadcast_identity_discovery_retains_server_names_and_works_without_lan_setting(self):
        with patch.object(discovery,'discover_lan_servers',return_value=[]),patch.object(
            discovery.socket,'socket'
        ) as create,patch.object(discovery.time,'monotonic',side_effect=[0,0,2.1]),patch.object(
            discovery,'_identity',return_value=dict(id='one',name='Living room Plex',url='http://10.0.0.22:32400')
        ) as identify:
            create.return_value.__enter__.return_value.recvfrom.return_value=(b'HTTP/1.0 200 OK\r\nName: Living room Plex\r\nPort: 32400\r\n',('10.0.0.22',32414))
            self.assertEqual(discovery.discover_plex_servers('')[0]['name'],'Living room Plex')
            identify.assert_called_once_with('http://10.0.0.22:32400','Living room Plex')


if __name__ == '__main__':
    unittest.main()
