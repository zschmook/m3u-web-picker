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
                self.assertTrue(remote.call_args.kwargs['desired_on'])
                self.assertEqual(remote.call_args.kwargs['status'],'active')

    def test_switch_refuses_other_ports_before_docker_mutation(self):
        import vpn_startup_host as host
        config=self.configuration();config['services']['setup']['environment']['M3U_PORT']='9999'
        with TemporaryDirectory() as root:
            path=Path(root)/'runtime/vpn/docker-compose.vpn.json';vpn_deployment.save_manifest(path,config)
            with patch.object(host,'ROOT',Path(root)),patch.object(host,'run') as run:
                with self.assertRaises(ValueError):host.switch_connection({'desired_on':False})
                run.assert_not_called()

    def test_reimport_replaces_managed_graph_without_same_project_backup(self):
        import io
        import vpn_startup_host as host
        original=self.configuration();info=self.fixture()
        info['Config']['Labels']['com.docker.compose.project']=original['name']
        job={'id':'c'*32,'lan_subnets':['10.0.0.0/24'],'provider':'custom','profile_saved':True,
             'saved_test_id':'a'*32,'saved_credential_volume':vpn_deployment.credential_volume('a'*32),
             'saved_gluetun_image':'qmcgaw/gluetun@sha256:'+'b'*64}
        calls=[]
        def run(args,*_a,**_k):
            calls.append(args)
            if args[:2]==['docker','inspect']:return json.dumps([info])
            if args[:3]==['docker','volume','inspect']:return 'a'*32
            if 'setup_wizard.load_state()' in args[-1]:return json.dumps({'completed':True})
            if 'https://api.ipify.org' in args[-1]:return '203.0.113.2'
            return ''
        with TemporaryDirectory() as root:
            path=Path(root)/'runtime/vpn/docker-compose.vpn.json';vpn_deployment.save_manifest(path,original)
            with patch.multiple(host,ROOT=Path(root),MANIFEST_OVERRIDE=None),patch.object(host,'run',side_effect=run),patch.object(host,'check_idle'),patch.object(host,'tailnet_ip',return_value=''),patch.object(host,'public_baseline',return_value='203.0.113.1'),patch.object(host,'remote'),patch.object(host.urllib.request,'urlopen',return_value=io.BytesIO(b'{"app_vpn_active":true}')):
                self.assertTrue(host.apply(job,'','m3u-web-picker:new'))
            self.assertFalse(any(args[:2] in (['docker','rename'],['docker','update']) for args in calls))
            downs=[i for i,args in enumerate(calls) if args[-1]=='down'];ups=[i for i,args in enumerate(calls) if args[-3:]==['up','-d','--no-build']]
            self.assertLess(downs[0],ups[0])
            current=json.loads(path.read_text());self.assertEqual(current['name'],original['name'])
            self.assertEqual(current['services']['setup']['volumes'],original['services']['setup']['volumes'])

    def test_compose_uses_the_selected_install_env(self):
        import vpn_startup_host as host
        with TemporaryDirectory() as root:
            install=Path(root)/'install';(install/'runtime/vpn').mkdir(parents=True)
            (install/'.env').write_text('M3U_IMAGE=m3u-web-picker:current\n')
            manifest=install/'runtime/vpn/docker-compose.vpn.json'
            with patch.object(host,'PORT',9999),patch.object(host,'ROOT',Path(root)/'source'):
                args=host.compose_command(manifest)
            self.assertEqual(args,['docker','compose','--env-file',str(install/'.env'),'-f',str(manifest)])

    def test_validation_failure_finishes_claimed_control_instead_of_hanging(self):
        import threading
        import vpn_startup_host as host
        stop=threading.Event();info=self.fixture();info['Config']['Env'].append('M3U_VPN_REQUIRED=true')
        control={'control_id':'a'*32,'control_status':'applying','desired_on':False,'feature_enabled':False}
        writes=[]
        def remote(operation,**fields):
            if operation in ('claim_control','read'):return control.copy()
            writes.append(fields)
            if fields.get('control_status')=='failed':stop.set()
            return fields
        with TemporaryDirectory() as root:
            path=Path(root)/'runtime/vpn/docker-compose.vpn.json';path.parent.mkdir(parents=True);path.write_text('{}')
            argv=['vpn_startup_host.py','--watch','--port','9998','--allow-restart-9998','--image','m3u-web-picker:test']
            with patch.multiple(host,ROOT=Path(root),MANIFEST_OVERRIDE=None,PORT=host.PORT,TARGET=host.TARGET,DATA=host.DATA,DB=host.DB),patch.object(sys,'argv',argv),patch.object(host,'run',return_value=json.dumps([info])),patch.object(host,'remote',side_effect=remote),patch.object(host,'switch_connection',side_effect=ValueError('Invalid graph')):
                host.main(stop)
            self.assertTrue(stop.is_set());self.assertEqual(writes[-1]['control_status'],'failed')
            self.assertTrue(writes[-1]['desired_on']);self.assertTrue(writes[-1]['feature_enabled'])
