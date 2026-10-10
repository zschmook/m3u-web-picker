"""Install the bundled VPN supervisor and current-user startup integration."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from vpn_helper import directory, external_environment, instance_lock, write_json

HELPER_NAME = 'M3U-Web-Picker-VPN-Helper'


def verify_bundle():
    suffix='.exe' if platform.system()=='Windows' else ''
    folder=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent/'dist'))
    executable=folder/(HELPER_NAME+suffix)
    if not executable.is_file():
        raise RuntimeError('The installer is missing its bundled VPN helper.')
    executable.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix='m3u-vpn-helper-check-') as temporary:
        report=Path(temporary)/'report.json'
        env=external_environment();env['PYINSTALLER_RESET_ENVIRONMENT']='1'
        subprocess.run([str(executable),'--self-check','--report',str(report)],check=True,env=env,
            **({'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if os.name=='nt' else {}))
        result=json.loads(report.read_text())
    if not result.get('passed') or not result.get('frozen'):
        raise RuntimeError('The bundled VPN helper did not pass its frozen runtime check.')
    return result


def service_name(root):
    suffix=hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
    return 'local.m3u.webpicker.vpn-'+suffix


def prepare_helper(staging, final_root, docker, image):
    """Prepare before any running helper/container is stopped."""
    staging, final_root = Path(staging), Path(final_root).resolve()
    if any(char in str(final_root)+str(docker) for char in '\r\n\0'):
        raise ValueError('Install and Docker paths cannot contain control characters.')
    folder = directory(staging)
    folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
    suffix = '.exe' if platform.system()=='Windows' else ''
    parent=Path(__file__).resolve().parent
    if getattr(sys,'frozen',False):
        candidates=[Path(sys._MEIPASS)/(HELPER_NAME+suffix)]
    else:
        candidates=[parent/(HELPER_NAME+suffix),parent/'dist'/(HELPER_NAME+suffix)]
    bundled=next((path for path in candidates if path.is_file()),candidates[0])
    if bundled.is_file():
        digest=hashlib.sha256(bundled.read_bytes()).hexdigest()[:16]
        destination=folder/'bin'/digest/bundled.name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(bundled,destination);destination.chmod(0o700)
        launcher=[str(final_root/destination.relative_to(staging))]
    elif getattr(sys,'frozen',False):
        raise RuntimeError('The installer is missing its bundled VPN helper; no containers were stopped.')
    else:
        # Readable Linux fallback keeps using its existing Python interpreter.
        script=staging/'installer/docker/vpn_helper.py'
        if not script.is_file():
            raise RuntimeError('This source release does not include the VPN helper.')
        launcher=[sys.executable,str(final_root/script.relative_to(staging))]
    config={'root':str(final_root),'port':9999,'image':image,'docker':str(Path(docker).resolve()),'launcher':launcher}
    write_json(folder/'config.json',config)
    return config


def stop_helper(root, timeout=360):
    """Ask only the supervisor owned by this directory to finish and exit."""
    folder=directory(root)
    try:
        state=json.loads((folder/'state.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return False
    try:
        with instance_lock(root):
            return False
    except OSError:
        pass
    run_id=state.get('run_id','')
    if not __import__('re').fullmatch('[a-f0-9]{32}',run_id):
        raise RuntimeError('The installed VPN helper has an invalid ownership record.')
    (folder/'stop').write_text(run_id,encoding='utf-8')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        current=json.loads((folder/'state.json').read_text(encoding='utf-8'))
        if current.get('run_id')==run_id and current.get('status')=='stopped':
            try:
                with instance_lock(root):
                    return True
            except OSError:
                pass
        time.sleep(1)
    raise RuntimeError('The VPN helper is finishing a test or connection change. Retry the upgrade after it completes; no container was stopped.')


def command(root):
    config=json.loads((directory(root)/'config.json').read_text(encoding='utf-8'))
    return [*config['launcher'],'--install-dir',str(Path(root).resolve())]


def native_run(arguments,check=True):
    result=subprocess.run(arguments,capture_output=True,text=True,check=False,env=external_environment(),
        **({'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if os.name=='nt' else {}))
    if check and result.returncode:
        raise RuntimeError('Could not register the current-user VPN helper startup service.')
    return result


def launch(root):
    env=external_environment();env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    log=directory(root)/'supervisor.log'
    with log.open('a',encoding='utf-8') as stream:
        log.chmod(0o600)
        subprocess.Popen(command(root),env=env,stdout=stream,stderr=stream,
            **({'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if os.name=='nt' else {'start_new_session':True}))


def systemd_quote(value):
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def desktop_quote(value):
    for char in ('\\','"','`','$'):
        value=value.replace(char,'\\'+char)
    # Desktop Entry string escaping is applied before Exec quote parsing.
    return '"'+value.replace('\\','\\\\').replace('%','%%')+'"'


def register_startup(root):
    root=Path(root).resolve();args=command(root);name=service_name(root)
    system=platform.system()
    if system=='Windows':
        import winreg
        value=subprocess.list2cmdline(args)
        if len(value)>260:
            raise RuntimeError('Choose a shorter install path for Windows login startup (command exceeds 260 characters).')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            winreg.SetValueEx(key,name,0,winreg.REG_SZ,value)
        launch(root)
        return 'Windows current-user login'
    if system=='Darwin':
        folder=Path.home()/'Library/LaunchAgents';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(name+'.plist')
        payload={'Label':name,'ProgramArguments':args,'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},
            'ThrottleInterval':5,'EnvironmentVariables':{'PYINSTALLER_RESET_ENVIRONMENT':'1'},
            'StandardOutPath':str(directory(root)/'supervisor.log'),'StandardErrorPath':str(directory(root)/'supervisor.log')}
        path.write_bytes(plistlib.dumps(payload));path.chmod(0o600)
        domain='gui/'+str(os.getuid())
        native_run(['launchctl','bootout',domain+'/'+name],check=False)
        native_run(['launchctl','bootstrap',domain,str(path)])
        return 'macOS LaunchAgent'
    config_home=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))
    if shutil.which('systemctl'):
        folder=config_home/'systemd/user';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(name+'.service')
        path.write_text('[Unit]\nDescription=M3U Web Picker VPN helper\n\n[Service]\nType=simple\nExecStart='+
            ' '.join(systemd_quote(value) for value in args)+'\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=420\n'+
            'Environment=PYINSTALLER_RESET_ENVIRONMENT=1\n\n[Install]\nWantedBy=default.target\n',encoding='utf-8')
        path.chmod(0o600)
        if native_run(['systemctl','--user','daemon-reload'],check=False).returncode==0:
            native_run(['systemctl','--user','enable','--now',name+'.service'])
            return 'Linux systemd user service'
    folder=config_home/'autostart';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(name+'.desktop')
    path.write_text('[Desktop Entry]\nType=Application\nName=M3U Web Picker VPN helper\nTerminal=false\nExec='+
        ' '.join(desktop_quote(value) for value in args)+'\n',encoding='utf-8')
    path.chmod(0o600);launch(root)
    return 'Linux desktop login'


def wait_ready(root, seconds=60):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            owned=json.loads((directory(root)/'state.json').read_text(encoding='utf-8'))
            if owned.get('status')!='running' or time.time()-owned.get('heartbeat',0)>=20 or not {'test','manager'}.issubset(owned.get('workers',{})):
                time.sleep(1);continue
            with opener.open('http://localhost:9999/api/vpn-test',timeout=2) as response:
                test=json.load(response)
            with opener.open('http://localhost:9999/api/vpn-state',timeout=2) as response:
                state=json.load(response)
            if test.get('helper_available') and time.time()-state.get('control_heartbeat',0)<20:
                return
        except (OSError,ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('VPN helper startup could not be verified. Check runtime/vpn-helper logs; do not upload a VPN configuration until the helpers are available.')


def install_helper(root):
    startup=register_startup(root)
    wait_ready(root)
    print('VPN helper installed and ready ('+startup+').')
    return startup
