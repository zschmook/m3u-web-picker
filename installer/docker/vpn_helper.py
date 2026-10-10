"""Packaged, per-user supervisor for the installed production VPN workers."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid


def directory(root):
    return Path(root) / 'runtime' / 'vpn-helper'


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


@contextmanager
def instance_lock(root):
    path = directory(root) / 'instance.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_configuration(root):
    root = Path(root).resolve()
    if not (root/'src/vpn_runtime.py').is_file() or not (root/'docker-compose.release.yml').is_file():
        raise ValueError('The helper requires an installed Docker Picker source tree.')
    config = json.loads((directory(root)/'config.json').read_text(encoding='utf-8'))
    if config.get('port') != 9999 or config.get('root') != str(root):
        raise ValueError('Helper configuration does not match this production installation.')
    import re
    if not re.fullmatch(r'ghcr.io/zschmook/m3u-web-picker:(?:v\d+|latest)',config.get('image','')):
        raise ValueError('The helper requires an official Picker release image.')
    docker = Path(config.get('docker',''))
    if not docker.is_absolute() or not docker.is_file():
        raise ValueError('The installed Docker executable is unavailable.')
    return config


def self_command(root):
    if getattr(sys,'frozen',False):
        return [sys.executable]
    return [sys.executable,str(Path(root)/'installer/docker/vpn_helper.py')]


def external_environment():
    env = dict(os.environ)
    # System Docker/Compose must not load the frozen Python runtime's libraries.
    for key in ('LD_LIBRARY_PATH','DYLD_LIBRARY_PATH'):
        original = env.get(key+'_ORIG')
        if original is not None:
            env[key] = original
        else:
            env.pop(key,None)
    if os.name == 'nt' and getattr(sys,'frozen',False):
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    return env


def modules(root):
    sys.path[:0] = [str(Path(root)/'scripts'),str(Path(root)/'src')]
    # Static imports allow PyInstaller to bundle all required stdlib modules.
    import vpn_test_host
    import vpn_startup_host
    import vpn_deployment
    vpn_test_host.ROOT = Path(root)
    vpn_startup_host.ROOT = Path(root)
    return vpn_test_host, vpn_startup_host, vpn_deployment


def validate_owner(info, root):
    files = (info['Config'].get('Labels') or {}).get('com.docker.compose.project.config_files','')
    allowed = {str((Path(root)/name).resolve()) for name in ('docker-compose.release.yml','runtime/vpn/docker-compose.vpn.json')}
    if not any(str(Path(value.strip()).resolve()) in allowed for value in files.split(',')):
        raise ValueError('Picker is not owned by this installer directory.')


def run_worker(root, role, stop):
    config = read_configuration(root)
    test, manager, deployment = modules(root)
    original = test.run
    env = external_environment()
    os.environ.clear(); os.environ.update(env)
    def run(arguments, *args, **kwargs):
        if arguments[0] == 'docker':
            arguments = [config['docker'],*arguments[1:]]
        return original(arguments,*args,**kwargs)
    test.run = manager.run = deployment.run = run
    worker = test if role == 'test' else manager
    validate = worker.validate_target
    def owned(info):
        validate_owner(info,root)
        return validate(info)
    worker.validate_target = owned
    sys.argv = ['vpn-worker','--watch','--port','9999']
    if role == 'manager':
        sys.argv += ['--allow-restart-9999','--image',config['image'],'--manifest',str(Path(root)/'runtime/vpn/docker-compose.vpn.json')]
    return worker.main(stop)


def requested_stop(root, run_id):
    try:
        return (directory(root)/'stop').read_text(encoding='utf-8').strip() == run_id
    except OSError:
        return False


def supervise(root, stop):
    read_configuration(root)
    folder = directory(root)
    run_id = uuid.uuid4().hex
    children = {}
    logs = {}
    with instance_lock(root):
        try:
            while not stop.is_set() and not requested_stop(root,run_id):
                for role in ('test','manager'):
                    if role in children and children[role].poll() is None:
                        continue
                    if role not in logs:
                        logs[role] = (folder/(role+'.log')).open('a',encoding='utf-8',buffering=1)
                        (folder/(role+'.log')).chmod(0o600)
                    env = external_environment()
                    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
                    command = [*self_command(root),'--install-dir',str(root),'--role',role,'--run-id',run_id]
                    try:
                        children[role] = subprocess.Popen(command,env=env,stdout=logs[role],stderr=logs[role],
                            **({'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if os.name=='nt' else {}))
                    except OSError:
                        pass
                write_json(folder/'state.json',{'run_id':run_id,'pid':os.getpid(),'status':'running','heartbeat':time.time(),
                    'workers':{role:child.pid for role,child in children.items() if child.poll() is None}})
                stop.wait(5)
        finally:
            (folder/'stop').write_text(run_id,encoding='utf-8')
            # Workers finish their current test/switch before exiting. Never kill
            # an app midway through a network handoff to update the helper.
            while any(child.poll() is None for child in children.values()):
                time.sleep(1)
            write_json(folder/'state.json',{'run_id':run_id,'status':'stopped','heartbeat':time.time(),'workers':{}})
            for stream in logs.values():
                stream.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir',type=Path)
    parser.add_argument('--role',choices=('supervisor','test','manager'),default='supervisor')
    parser.add_argument('--run-id',default='')
    parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--report',type=Path)
    args = parser.parse_args()
    if args.self_check:
        test, manager, deployment = modules(Path(__file__).resolve().parents[2])
        test.configure_target(9999);manager.configure_target(9999)
        assert test.TARGET == manager.TARGET == 'm3u-picker'
        assert manager.valid_app_image('ghcr.io/zschmook/m3u-web-picker:v44')
        assert deployment.credential_volume('a'*32).endswith('a'*32)
        result={'passed':True,'frozen':bool(getattr(sys,'frozen',False)),'workers':['test','manager'],'port':9999}
        if args.report:
            write_json(args.report,result)
        elif sys.stdout is not None:
            print(json.dumps(result))
        return 0
    if args.install_dir is None:
        parser.error('--install-dir is required')
    root = args.install_dir.resolve()
    if sys.stdout is None or sys.stderr is None:
        folder=directory(root);folder.mkdir(parents=True,exist_ok=True)
        log=(folder/(args.role+'.log')).open('a',encoding='utf-8',buffering=1)
        (folder/(args.role+'.log')).chmod(0o600)
        sys.stdout=sys.stderr=log
    stop = threading.Event()
    for signum in (signal.SIGTERM,signal.SIGINT):
        signal.signal(signum,lambda *_:stop.set())
    if args.role == 'supervisor':
        try:
            return supervise(root,stop)
        except (BlockingIOError,OSError) as exc:
            if isinstance(exc,BlockingIOError) or getattr(exc,'winerror',None)==33:
                return 0
            raise
    if not __import__('re').fullmatch('[a-f0-9]{32}',args.run_id):
        parser.error('Workers require a supervisor run identity')
    def watch_stop():
        while not stop.wait(1):
            if requested_stop(root,args.run_id):
                stop.set()
    threading.Thread(target=watch_stop,daemon=True).start()
    return run_worker(root,args.role,stop) or 0


if __name__ == '__main__':
    raise SystemExit(main())
