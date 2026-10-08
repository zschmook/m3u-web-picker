import copy
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import vpn_deployment
import vpn_runtime


class PersistenceTests(unittest.TestCase):
    def fixture(self):
        app = {'Name': '/m3u-picker-setup', 'Config': {'Labels': {'com.docker.compose.project': 'm3u-picker-setup', 'com.docker.compose.service': 'setup'},
               'Env': ['M3U_PORT=9998', 'M3U_DATA_DIR=/app/setup-data'], 'Entrypoint': None, 'Cmd': ['waitress-serve', 'src.setup_runtime:application']},
               'HostConfig': {}, 'Mounts': [{'Type': 'volume', 'Name': 'existing-data', 'Destination': '/app/setup-data', 'RW': True}]}
        return app

    def configuration(self):
        return vpn_deployment.startup_manifest(self.fixture(), 'm3u-web-picker:new', 'm3u-picker-vpn-'+'a'*32,
                                               vpn_deployment.credential_volume('a'*32), 'qmcgaw/gluetun@sha256:'+'b'*64, ['10.0.0.0/24'])

    def test_recreation_keeps_credentials_and_waits_for_vpn_health(self):
        config = self.configuration()
        app, glue = config['services']['setup'], config['services']['gluetun']
        self.assertEqual(app['network_mode'], 'service:gluetun')
        self.assertEqual(app['depends_on']['gluetun']['condition'], 'service_healthy')
        self.assertEqual(app['environment']['M3U_VPN_REQUIRED'], 'true')
        self.assertEqual(glue['restart'], 'unless-stopped')
        self.assertEqual(glue['environment']['HEALTH_RESTART_VPN'], 'on')
        self.assertTrue(config['volumes']['vpn-config']['external'])
        self.assertEqual(glue['volumes'][0]['target'], '/gluetun')
        self.assertNotIn('vpn-config', [item['source'] for item in app['volumes']])
        self.assertEqual(config['volumes']['app-volume-0']['name'], 'existing-data')

    def test_keys_travel_only_on_stdin_and_failed_write_removes_volume(self):
        secret, auth = 'PRIVATE-CONFIG-CONTENT', 'PRIVATE-CONTROL-CONTENT'
        with patch.object(vpn_deployment, 'run') as run:
            vpn_deployment.create_credentials('a'*32, secret, auth, 'gluetun:test')
        calls = run.call_args_list
        self.assertNotIn(secret, json.dumps([call.args[0] for call in calls]))
        self.assertNotIn(auth, json.dumps([call.args[0] for call in calls]))
        self.assertEqual([call.args[1] for call in calls if len(call.args)>1], [secret, auth])
        self.assertIn('chmod 600', calls[1].args[0][-1])
        with patch.object(vpn_deployment, 'run', side_effect=['volume', RuntimeError('write failed'), 'removed']) as failed:
            with self.assertRaises(RuntimeError):
                vpn_deployment.create_credentials('b'*32, secret, auth, 'gluetun:test')
            self.assertEqual(failed.call_args.args[0][:3], ['docker', 'volume', 'rm'])

    def test_invalid_identity_and_keys_in_manifest_are_rejected(self):
        with self.assertRaises(ValueError): vpn_deployment.credential_volume('../other')
        app = self.fixture()
        app['Config']['Env'].append('WIREGUARD_PRIVATE_KEY=secret')
        with self.assertRaises(ValueError):
            vpn_deployment.startup_manifest(app, 'image', 'glue', 'volume', 'digest', [])

    def test_metadata_does_not_claim_green_when_persisted_vpn_is_down(self):
        with TemporaryDirectory() as root:
            db = Path(root)/'db'
            vpn_runtime.write(db, requested=True, status='active', persistent=True, credential_volume='private-volume')
            with patch.object(vpn_runtime, 'required', return_value=True), patch.object(vpn_runtime, 'healthy', return_value=False):
                state = vpn_runtime.status(db)
            self.assertFalse(state['memory_only'])
            self.assertFalse(state['app_vpn_active'])
            self.assertEqual(state['status'], 'disconnected')

    def test_manifest_survives_source_upgrade_without_old_image(self):
        config = self.configuration()
        with TemporaryDirectory() as root:
            path = Path(root)/'runtime/vpn/docker-compose.vpn.json'
            vpn_deployment.save_manifest(path, config)
            self.assertEqual(json.loads(path.read_text()), config)
            self.assertEqual(config['services']['setup']['image'], '${M3U_IMAGE:-m3u-web-picker:new}')
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_off_uses_normal_network_and_dns_and_roundtrip_retains_keys(self):
        original=self.configuration()
        disabled=vpn_deployment.connection_mode(original,False)
        app,glue=disabled['services']['setup'],disabled['services']['gluetun']
        self.assertNotIn('network_mode',app);self.assertNotIn('depends_on',app)
        self.assertEqual(app['ports'],['9998:9998'])
        self.assertEqual(app['environment']['M3U_VPN_REQUIRED'],'false')
        self.assertIsNone(app['entrypoint']);self.assertEqual(app['command'],self.fixture()['Config']['Cmd'])
        self.assertEqual(glue['profiles'],['vpn']);self.assertNotIn('ports',glue)
        enabled=vpn_deployment.connection_mode(disabled,True)
        self.assertEqual(enabled,original)
        self.assertEqual(disabled['volumes'],original['volumes'])

    def test_failed_switch_rolls_back_manifest_and_records_failure(self):
        import vpn_startup_host as host
        original=self.configuration()
        with TemporaryDirectory() as root:
            path=Path(root)/'runtime/vpn/docker-compose.vpn.json'
            vpn_deployment.save_manifest(path,original)
            calls=[]
            def run(args,*_a,**_k):
                calls.append(args)
                if args[:2]==['docker','inspect']:return json.dumps([self.fixture()])
                if args[-3:]==['up','-d','--no-build'] and sum(c[-3:]==['up','-d','--no-build'] for c in calls)==1:raise RuntimeError('failed')
                return ''
            with patch.object(host,'ROOT',Path(root)),patch.object(host,'run',side_effect=run),patch.object(host,'check_idle'),patch.object(host,'public_baseline',return_value='203.0.113.1'),patch.object(host,'remote') as remote:
                with self.assertRaises(RuntimeError):host.switch_connection({'desired_on':False,'feature_enabled':False})
                self.assertEqual(json.loads(path.read_text()),original)
                self.assertEqual(remote.call_args.kwargs['control_status'],'failed')
                self.assertTrue(remote.call_args.kwargs['feature_enabled'])

    def test_switch_refuses_other_ports_before_docker_mutation(self):
        import vpn_startup_host as host
        config=self.configuration();config['services']['setup']['environment']['M3U_PORT']='9999'
        with TemporaryDirectory() as root:
            path=Path(root)/'runtime/vpn/docker-compose.vpn.json';vpn_deployment.save_manifest(path,config)
            with patch.object(host,'ROOT',Path(root)),patch.object(host,'run') as run:
                with self.assertRaises(ValueError):host.switch_connection({'desired_on':False})
                run.assert_not_called()
