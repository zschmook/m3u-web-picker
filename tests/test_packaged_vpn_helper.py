import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from contextlib import contextmanager
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'installer/docker'))
import helper_service as service
import vpn_helper as helper


class PackagedVpnHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'Picker with spaces';self.root.mkdir()
        (self.root/'src').mkdir();(self.root/'src/vpn_runtime.py').write_text('')
        (self.root/'docker-compose.release.yml').write_text('services: {}')
        self.docker=self.root/'docker';self.docker.write_text('')
        helper.write_json(helper.directory(self.root)/'config.json',{'root':str(self.root.resolve()),'port':9999,
            'image':'ghcr.io/zschmook/m3u-web-picker:v44','docker':str(self.docker),'launcher':['/helper with spaces']})

    def test_private_configuration_accepts_only_installed_production_release(self):
        self.assertEqual(helper.read_configuration(self.root)['port'],9999)
        for change in ({'port':9998},{'root':'another installation'},{'image':'someone/image:latest'},{'docker':'relative-docker'}):
            path=helper.directory(self.root)/'config.json';original=path.read_text()
            value=json.loads(original);value.update(change);helper.write_json(path,value)
            with self.assertRaises(ValueError):helper.read_configuration(self.root)
            path.write_text(original)

    def test_source_bundle_is_copied_privately_into_versioned_runtime(self):
        bundle=self.root/'bundle';bundle.mkdir()
        suffix='.exe' if os.name=='nt' else ''
        binary=bundle/(service.HELPER_NAME+suffix);binary.write_bytes(b'packaged-helper')
        staging=self.root/'staging';staging.mkdir()
        with patch.object(sys,'_MEIPASS',str(bundle),create=True),patch.object(sys,'frozen',True,create=True):
            config=service.prepare_helper(staging,self.root,self.docker,'ghcr.io/zschmook/m3u-web-picker:v44')
        relative=Path(config['launcher'][0]).relative_to(self.root)
        self.assertEqual((staging/relative).read_bytes(),binary.read_bytes())
        self.assertEqual(config['root'],str(self.root.resolve()))
        self.assertFalse(any('wireguard' in key.lower() for key in config))
        if os.name!='nt':self.assertEqual((staging/relative).stat().st_mode & 0o777,0o700)

    def test_frozen_installer_refuses_missing_bundle_before_service_changes(self):
        with patch.object(sys,'_MEIPASS',str(self.root/'missing'),create=True),patch.object(sys,'frozen',True,create=True):
            with self.assertRaisesRegex(RuntimeError,'missing its bundled'):
                service.prepare_helper(self.root/'stage',self.root,self.docker,'ghcr.io/zschmook/m3u-web-picker:v44')

    def test_owner_guard_refuses_another_compose_install_even_on_same_port(self):
        info={'Config':{'Labels':{'com.docker.compose.project.config_files':str(self.root/'docker-compose.release.yml')}}}
        helper.validate_owner(info,self.root)
        info['Config']['Labels']['com.docker.compose.project.config_files']=str(self.root.parent/'other/docker-compose.release.yml')
        with self.assertRaises(ValueError):helper.validate_owner(info,self.root)

    def test_os_lock_rejects_duplicate_supervisor_then_releases(self):
        with helper.instance_lock(self.root):
            with self.assertRaises(OSError):
                with helper.instance_lock(self.root):pass
        with helper.instance_lock(self.root):pass

    def test_launchagent_uses_argument_array_and_restarts_only_failed_exits(self):
        with patch.object(service.platform,'system',return_value='Darwin'),patch.object(Path,'home',return_value=self.root),\
                patch.object(os,'getuid',return_value=123,create=True),patch.object(service,'native_run') as run:
            service.register_startup(self.root)
        path=self.root/'Library/LaunchAgents'/(service.service_name(self.root)+'.plist')
        payload=plistlib.loads(path.read_bytes())
        self.assertEqual(payload['ProgramArguments'],service.command(self.root))
        self.assertEqual(payload['KeepAlive'],{'SuccessfulExit':False})
        self.assertEqual(run.call_args_list[-1].args[0],['launchctl','bootstrap','gui/123',str(path)])

    def test_systemd_user_unit_quotes_paths_and_has_bounded_stop_grace(self):
        with patch.object(service.platform,'system',return_value='Linux'),patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'xdg')}),\
                patch.object(service.shutil,'which',return_value='/bin/systemctl'),patch.object(service,'native_run',return_value=SimpleNamespace(returncode=0)) as run:
            service.register_startup(self.root)
        text=next((self.root/'xdg/systemd/user').glob('*.service')).read_text()
        self.assertIn('Restart=on-failure',text);self.assertIn('TimeoutStopSec=420',text)
        self.assertIn('"/helper with spaces"',text)
        self.assertEqual(run.call_args_list[-1].args[0][:4],['systemctl','--user','enable','--now'])
        self.assertIn('$$',service.systemd_quote('$USER'));self.assertIn('%%',service.systemd_quote('%u'))

    def test_linux_without_user_systemd_has_login_fallback_and_starts_now(self):
        with patch.object(service.platform,'system',return_value='Linux'),patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root/'xdg')}),\
                patch.object(service.shutil,'which',return_value=None),patch.object(service,'launch') as launch:
            self.assertEqual(service.register_startup(self.root),'Linux desktop login')
        self.assertIn('Terminal=false',next((self.root/'xdg/autostart').glob('*.desktop')).read_text())
        launch.assert_called_once_with(self.root)
        self.assertEqual(service.desktop_quote('a\\b'),'"a'+'\\'*4+'b"')

    def test_readiness_requires_both_workers_not_just_live_child_pids(self):
        helper.write_json(helper.directory(self.root)/'state.json',{'status':'running','heartbeat':time.time(),'workers':{'test':123,'manager':456}})
        responses=[{'helper_available':True},{'control_heartbeat':time.time()}]
        class Reply:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(responses.pop(0)).encode()
        with patch.object(service.urllib.request,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k:Reply())):
            service.wait_ready(self.root)
        with self.assertRaisesRegex(RuntimeError,'could not be verified'):
            service.wait_ready(self.root,seconds=0)

    def test_cooperative_stop_is_scoped_to_current_supervisor_nonce(self):
        run_id='a'*32;folder=helper.directory(self.root)
        helper.write_json(folder/'state.json',{'run_id':run_id,'status':'running','heartbeat':time.time()})
        def finished(_):helper.write_json(folder/'state.json',{'run_id':run_id,'status':'stopped','heartbeat':time.time()})
        @contextmanager
        def locking(_):
            if json.loads((folder/'state.json').read_text())['status']=='running':
                raise OSError('locked')
            yield
        with patch.object(service,'instance_lock',locking),patch.object(service.time,'sleep',side_effect=finished):
            self.assertTrue(service.stop_helper(self.root))
        self.assertTrue(helper.requested_stop(self.root,run_id))
        self.assertFalse(helper.requested_stop(self.root,'b'*32))

    def test_windows_startup_is_current_user_and_hidden_not_an_admin_service(self):
        calls=[]
        class Key:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        registry=SimpleNamespace(HKEY_CURRENT_USER=123,REG_SZ=1,
            CreateKey=lambda *args:(calls.append(args) or Key()),SetValueEx=lambda *args:calls.append(args))
        with patch.dict(sys.modules,{'winreg':registry}),patch.object(service.platform,'system',return_value='Windows'),\
                patch.object(service,'launch') as launch:
            self.assertEqual(service.register_startup(self.root),'Windows current-user login')
        self.assertEqual(calls[0][0],registry.HKEY_CURRENT_USER)
        self.assertIn('CurrentVersion\\Run',calls[0][1])
        self.assertIn('"/helper with spaces"',calls[1][-1])
        launch.assert_called_once_with(self.root)

    def test_connection_manager_uses_installed_env_to_keep_upgraded_image(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        import vpn_startup_host as manager
        (self.root/'.env').write_text('M3U_IMAGE=ghcr.io/zschmook/m3u-web-picker:v44\n')
        with patch.object(manager,'ROOT',self.root),patch.object(manager,'PORT',9999):
            command=manager.compose_command(self.root/'runtime/vpn/docker-compose.vpn.json')
        self.assertEqual(command[2:4],['--env-file',str(self.root/'.env')])
        self.assertTrue(manager.valid_app_image('ghcr.io/zschmook/m3u-web-picker:v44'))
        self.assertFalse(manager.valid_app_image('ghcr.io/another/image:v44'))

    def test_supervisor_restarts_failed_worker_and_stops_owned_children_cooperatively(self):
        # Real subprocesses with no Docker or network access validate lifecycle.
        script=self.root/'fake_worker.py'
        script.write_text('''import argparse,pathlib,time,sys
p=argparse.ArgumentParser();p.add_argument('--role');p.add_argument('--install-dir');p.add_argument('--run-id');a=p.parse_args()
f=pathlib.Path(a.install_dir)/'runtime/vpn-helper';marker=f/(a.role+'-once')
if a.role=='test' and not marker.exists():marker.write_text('crashed');sys.exit(1)
while True:
 try:
  if (f/'stop').read_text().strip()==a.run_id:break
 except FileNotFoundError:pass
 time.sleep(.02)
''')
        stop=threading.Event();errors=[]
        def run():
            try:helper.supervise(self.root,stop)
            except BaseException as exc:errors.append(exc)
        with patch.object(helper,'self_command',return_value=[sys.executable,str(script)]):
            thread=threading.Thread(target=run);thread.start()
            try:
                deadline=time.monotonic()+12;first=None;restarted=False
                while time.monotonic()<deadline:
                    try:state=json.loads((helper.directory(self.root)/'state.json').read_text())
                    except (OSError,ValueError):time.sleep(.05);continue
                    pid=state['workers'].get('test')
                    if first is None:first=pid
                    elif pid and pid!=first:restarted=True;break
                    time.sleep(.05)
                self.assertTrue(restarted,'A crashed worker must be restarted')
            finally:
                stop.set();thread.join(15)
            self.assertFalse(thread.is_alive());self.assertFalse(errors)
        state=json.loads((helper.directory(self.root)/'state.json').read_text())
        self.assertEqual(state['status'],'stopped');self.assertEqual(state['workers'],{})


if __name__=='__main__':unittest.main()
