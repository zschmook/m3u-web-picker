import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from flask import Flask
import custom_channels as channels
import plex_auth as auth
from api import custom_channels as routes


class PlexAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root_patch = patch.object(channels, 'root', return_value=Path(self.temp.name))
        root_patch.start(); self.addCleanup(root_patch.stop)
        auth.FLOWS.clear(); self.addCleanup(auth.FLOWS.clear)

    def sign_in(self):
        with patch.object(auth, '_request', return_value={'id': 123, 'code': 'pin-code', 'expiresIn': 600}):
            result = auth.start_sign_in()
        return result

    def account(self):
        auth._save(dict(client_id='picker-client', token='private-account-token', name='Zack'))

    def resources(self):
        return [dict(provides='server', clientIdentifier='server-one', name='My Plex', accessToken='private-server-token',
                     connections=[dict(uri='https://remote.plex.direct:32400', local=False, protocol='https'),
                                  dict(uri='http://10.0.0.18:32400', local=True, protocol='http')]),
                dict(provides='player', clientIdentifier='player', accessToken='player-secret', connections=[])]

    def test_pin_flow_and_client_identifier_persist_without_returning_credentials(self):
        first = self.sign_in()
        client = auth._account()['client_id']
        second = self.sign_in()
        self.assertEqual(auth._account()['client_id'], client)
        self.assertNotEqual(first['flow_id'], second['flow_id'])
        self.assertTrue(first['auth_url'].startswith('https://app.plex.tv/auth#?'))
        self.assertFalse(auth.public_account()['signed_in'])
        self.assertNotIn('private', json.dumps(first))
        self.assertFalse((Path(self.temp.name)/'servers.json').exists())

    def test_claimed_pin_stores_account_backend_only_without_connecting_or_scanning(self):
        flow = self.sign_in()
        with patch.object(auth, '_request', side_effect=[{'authToken': 'private-account-token'}, {'title': 'Zack'}]), \
             patch.object(channels, 'connect_server') as connect, patch.object(channels, 'start_discovery') as scan:
            result = auth.check_sign_in(flow['flow_id'])
        self.assertEqual(result, dict(status='complete', account=dict(signed_in=True, name='Zack')))
        self.assertEqual(auth._account()['token'], 'private-account-token')
        self.assertNotIn('private-account-token', json.dumps(result))
        connect.assert_not_called(); scan.assert_not_called()
        with patch.object(auth, '_request') as request:
            self.assertEqual(auth.check_sign_in(flow['flow_id'])['status'], 'complete')
            request.assert_not_called()

    def test_pending_expired_and_canceled_flows_are_bounded_and_keep_existing_account(self):
        self.account(); flow = self.sign_in()
        with patch.object(auth, '_request', return_value={'authToken': None}) as request:
            self.assertEqual(auth.check_sign_in(flow['flow_id'])['status'], 'pending')
            self.assertEqual(auth.check_sign_in(flow['flow_id'])['status'], 'pending')
            self.assertEqual(request.call_count, 1)
        auth.cancel_sign_in(flow['flow_id'])
        self.assertEqual(auth.check_sign_in(flow['flow_id'])['status'], 'expired')
        self.assertEqual(auth._account()['token'], 'private-account-token')
        with patch.object(auth, '_request') as request:
            self.assertEqual(auth.check_sign_in('untrusted-pin-id')['status'], 'expired')
            request.assert_not_called()

    def test_cancel_during_remote_auth_does_not_save_late_credentials(self):
        flow = self.sign_in()
        def response(path, *args):
            if path.startswith('pins/'):
                return {'authToken': 'late-token'}
            auth.cancel_sign_in(flow['flow_id'])
            return {'title': 'Late user'}
        with patch.object(auth, '_request', side_effect=response):
            self.assertEqual(auth.check_sign_in(flow['flow_id'])['status'], 'expired')
        self.assertNotIn('token', auth._account())

    def test_resources_are_sanitized_and_server_tokens_stay_private(self):
        self.account()
        with patch.object(auth, '_request', return_value=self.resources()):
            servers = auth.account_servers()
        self.assertEqual(servers, [dict(id='server-one', name='My Plex')])
        self.assertNotIn('token', json.dumps(servers))

    def test_selecting_server_uses_its_token_prefers_lan_and_checks_identity(self):
        self.account()
        with patch.object(auth, '_request', return_value=self.resources()), patch.object(channels, 'connect_server') as connect:
            auth.connect_server('server-one')
        connect.assert_called_once_with('http://10.0.0.18:32400', 'private-server-token', expected_identity='server-one')

    def test_connection_fallback_and_failure_do_not_overwrite_previous_connection(self):
        self.account()
        channels.save_schedule(channels.root()/'servers.json', [dict(id='existing', name='Existing', url='http://10.0.0.22:32400', token='old')])
        with patch.object(auth, '_request', return_value=self.resources()), patch.object(channels, 'connect_server', side_effect=ValueError('offline')) as connect:
            with self.assertRaisesRegex(auth.PlexAuthError, 'Could not reach'):
                auth.connect_server('server-one')
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(channels.read('servers.json', [])[0]['id'], 'existing')
        with patch.object(auth, '_request', return_value=self.resources()):
            with self.assertRaisesRegex(auth.PlexAuthError, 'no longer authorized'):
                auth.connect_server('not-in-account')

    def test_wrong_server_identity_is_not_saved(self):
        with patch.object(channels, 'plex_xml', return_value=ET.fromstring('<MediaContainer machineIdentifier="other"/>')):
            with self.assertRaises(ValueError):
                channels.connect_server('http://10.0.0.18:32400', 'private-server-token', expected_identity='server-one')
        self.assertEqual(channels.read('servers.json', []), [])

    def test_resources_reject_credentials_query_loopback_and_relay_urls(self):
        self.account()
        resources = self.resources()
        resources[0]['connections'] = [dict(uri=url) for url in ['file:///etc/passwd', 'http://user:pass@10.0.0.18',
            'http://10.0.0.18/?token=secret', 'http://127.0.0.1:32400', 'http://169.254.169.254', 'http://[::1]:32400']]
        resources[0]['connections'].append(dict(uri='https://relay.example', relay=True))
        with patch.object(auth, '_request', return_value=resources):
            self.assertEqual(auth.account_servers(), [])

    def test_network_errors_do_not_leak_credentials_or_invalidate_account(self):
        self.account()
        with patch.object(auth.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('private-account-token')
            with self.assertRaisesRegex(auth.PlexAuthError, '^Could not reach Plex') as error:
                auth.account_servers()
        self.assertNotIn('private-account-token', str(error.exception))
        self.assertEqual(auth._account()['token'], 'private-account-token')

    def test_invalid_account_requires_sign_in_but_preserves_client_identifier(self):
        self.account()
        with patch.object(auth.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError('https://plex.tv', 401, 'private-secret', {}, None)
            with self.assertRaisesRegex(auth.PlexAuthError, 'Sign in with Plex again'):
                auth.account_servers()
        self.assertFalse(auth.public_account()['signed_in'])
        self.assertEqual(auth._account()['client_id'], 'picker-client')

    def test_api_requires_json_and_returns_only_public_account_and_server_metadata(self):
        self.account()
        app = Flask(__name__); routes.register_custom_channel_routes(app); client = app.test_client()
        self.assertEqual(client.post('/api/custom-channels/plex/sign-in', data='').status_code, 415)
        self.assertEqual(client.post('/api/custom-channels/plex/sign-in', json=[]).status_code, 400)
        with patch.object(auth, '_request', return_value=self.resources()):
            response = client.post('/api/custom-channels/plex/servers', json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn('private-', response.get_data(as_text=True))
        self.assertEqual(response.json['account'], dict(signed_in=True, name='Zack'))


if __name__ == '__main__':
    unittest.main()
