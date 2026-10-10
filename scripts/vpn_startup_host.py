"""Explicitly authorized VPN handoff for one selected Picker instance. No secret files."""
from __future__ import annotations
import argparse,json,os,re,secrets,sys,time,urllib.request
from pathlib import Path
from vpn_test_host import run,TARGET,DB,DATA,parse_wireguard,lan_networks,tailnet_ip,public_baseline
import vpn_deployment
ROOT=Path(__file__).resolve().parents[1]
PORT=9998
MANIFEST_OVERRIDE=None

def configure_target(port):
 global PORT,TARGET,DB,DATA
 if port not in (9998,9999):raise ValueError('Choose Picker port 9998 or 9999.')
 PORT=port;TARGET='m3u-picker-setup' if port==9998 else 'm3u-picker'
 DATA='/app/setup-data' if port==9998 else '/app/data';DB=DATA+'/m3u_picker.db'

def manifest_path():
 return MANIFEST_OVERRIDE or ROOT/('runtime/vpn/docker-compose.vpn.json' if PORT==9998 else 'runtime/vpn-9999/docker-compose.vpn.json')

def compose_command(path):
 args=['docker','compose']
 if PORT==9999 and (ROOT/'.env').is_file():args+=['--env-file',str(ROOT/'.env')]
 return args+['-f',str(path)]

def remote(operation,**fields):
 source=(ROOT/'src/vpn_runtime.py').read_text(encoding='utf-8-sig')
 source+='\nimport sys\np=json.loads(sys.stdin.read());op=p.pop("operation");db='+repr(DB)+'\n'
 source+='\nif op=="claim":r=claim(db)\nelif op=="claim_control":r=claim_control(db)\nelif op=="read":r=read(db)\nelif op=="write":r=write(db,**p)\nelse:raise ValueError("Unknown operation")\nprint(json.dumps(r))\n'
 return json.loads(run(['docker','exec','-i',TARGET,'python','-c',source],json.dumps({'operation':operation,**fields})))

def queue_current():
 source=(ROOT/'src/vpn_runtime.py').read_text(encoding='utf-8-sig')
 source+='\nimport vpn_config\np=vpn_config.status('+repr(DB)+','+repr(DATA)+');print(json.dumps(queue('+repr(DB)+','+repr(DATA)+',p["profile_id"])))'
 return json.loads(run(['docker','exec',TARGET,'python','-c',source]))

def read_upload(job):
 if job.get('profile_saved') and job.get('saved_credential_volume'):return ''
 source='import vpn_config;print(vpn_config.profile_for_test('+repr(DB)+','+repr(job['profile_id'])+'))'
 return parse_wireguard(run(['docker','exec',TARGET,'python','-c',source]))[0]

def validate_target(info):
 env=dict(value.split('=',1) for value in info['Config']['Env'])
 expected='m3u-picker-setup' if PORT==9998 else 'm3u-picker'
 if env.get('M3U_PORT','9999')!=str(PORT) or info['Name']!='/'+expected or TARGET!=expected or env.get('M3U_DATA_DIR')!=DATA:
  raise ValueError('VPN handoff target does not match the explicitly selected Picker instance.')
 return env

def check_idle():
 try:
  try:
   with urllib.request.urlopen('http://localhost:'+str(PORT)+'/api/vpn-activity',timeout=5) as r:data=json.load(r)
  except urllib.error.HTTPError as exc:
   if exc.code!=404:raise
   with urllib.request.urlopen('http://localhost:'+str(PORT)+'/api/ui/status',timeout=5) as r:previous=json.load(r)
   data={'active_streams':previous.get('devices',{}).get('active_streams',0),'active_sessions':previous.get('playback',{}).get('runtime',{}).get('active_sessions',0)}
  if data.get('active_streams',0) or data.get('active_sessions',0):raise RuntimeError('Playback is active on '+str(PORT)+'. Stop playback before applying the VPN.')
 except urllib.error.HTTPError as exc:
  raise RuntimeError('Playback status on '+str(PORT)+' is unavailable; no restart was performed.') from exc
 top=run(['docker','top',TARGET])
 if re.search(r'\bffmpeg\b',top):raise RuntimeError('Playback is active on '+str(PORT)+'. Stop playback before applying the VPN.')
 print('Port '+str(PORT)+': no active playback detected; applying its authorized restart.',flush=True)

def app_arguments(info,image,glue,job):
 env=validate_target(info);env.update(M3U_VPN_REQUIRED='true',M3U_VPN_CONTAINER=glue)
 args=['docker','create','--name',TARGET,'--network','container:'+glue,'--restart','unless-stopped','--label','m3u.vpn-startup='+job['id']]
 for key,value in env.items():args+=['-e',key+'='+value]
 for mount in info['Mounts']:
  if mount['Type'] not in ('volume','bind'):raise ValueError('Unsupported application mount.')
  value='type='+mount['Type']+',source='+ (mount['Name'] if mount['Type']=='volume' else mount['Source'])+',target='+mount['Destination']
  if not mount.get('RW',True):value+=',readonly'
  args+=['--mount',value]
 if info['HostConfig'].get('DeviceRequests'):args+=['--gpus','all']
 args+=['--entrypoint','/bin/sh',image,'-c','printf "nameserver 127.0.0.1\\n" > /etc/resolv.conf; exec "$@"','picker',*info['Config']['Cmd']]
 return args

def apply(job,canonical,image):
 info=json.loads(run(['docker','inspect',TARGET]))[0];env=validate_target(info);check_idle()
 if not re.fullmatch('[a-f0-9]{32}',job['id']):raise ValueError('Invalid handoff identity.')
 glue='m3u-picker-vpn-'+job['id'];backup=TARGET+'-pre-vpn-'+job['id'];renamed=False;created=False;glue_created=False;volume=None
 subnets=lan_networks(job['lan_subnets']);tail=tailnet_ip()
 if tail:subnets.append(tail+'/32')
 key=secrets.token_urlsafe(24);auth='[[roles]]\nname="picker-host"\nroutes=["PUT /v1/vpn/status"]\nauth="apikey"\napikey="'+key+'"\n'
 baseline=public_baseline();digest=job.get('saved_gluetun_image') or json.loads(run(['docker','image','inspect','qmcgaw/gluetun:latest']))[0]['RepoDigests'][0]
 if not re.fullmatch(r'qmcgaw/gluetun@sha256:[a-f0-9]{64}',digest):raise ValueError('Invalid verified VPN image.')
 before=json.loads(run(['docker','exec',TARGET,'python','-c','import setup_wizard,json;print(json.dumps(setup_wizard.load_state()))'])) if PORT==9998 else {'completed':True}
 path=manifest_path();previous_manifest=path.read_bytes() if path.exists() else None
 compose=compose_command(path);compose_started=False
 try:
  if job.get('profile_saved') and job.get('saved_credential_volume'):
   volume=job['saved_credential_volume']
   if volume!=vpn_deployment.credential_volume(job['saved_test_id']):raise ValueError('Saved configuration ownership is invalid.')
   label=run(['docker','volume','inspect','--format','{{index .Labels "m3u.vpn-config"}}',volume])
   if label!=job['saved_test_id']:raise ValueError('Saved configuration volume ownership is invalid.')
   if job.get('saved_gluetun_image')!=digest:raise ValueError('Saved configuration image changed; test the file again.')
  else:volume=vpn_deployment.create_credentials(job['id'],canonical,auth,digest)
  canonical='';auth=''
  remote('write',requested=True,status='applying',error='',container=glue)
  if PORT==9998:run(['docker','exec',TARGET,'python','-c','import setup_wizard;setup_wizard.save_state({"completed":False})'])
  # Retain only preferences and the passing test result in SQLite; hand off the key in RAM.
  run(['docker','exec',TARGET,'python','-c','import vpn_config;vpn_config.discard('+repr(DB)+','+repr(DATA)+')'])
  run(['docker','stop','--time','15',TARGET],timeout=30);run(['docker','rename',TARGET,backup]);renamed=True
  configuration=vpn_deployment.startup_manifest(info,image,glue,volume,digest,subnets)
  vpn_deployment.save_manifest(path,configuration)
  compose_started=True
  run(compose+['up','-d','--no-build'],timeout=150)
  until=time.monotonic()+60
  while True:
   try:
    with urllib.request.urlopen('http://localhost:'+str(PORT)+'/api/vpn-state',timeout=3) as r:state=json.load(r)
    if state.get('app_vpn_active'):break
   except Exception:pass
   if time.monotonic()>until:raise RuntimeError('Picker did not report a healthy VPN network.')
   time.sleep(2)
  public=run(['docker','exec',TARGET,'python','-c','import urllib.request;print(urllib.request.urlopen("https://api.ipify.org",timeout=15).read().decode())'])
  if public==baseline:raise RuntimeError('Picker egress did not change to the VPN.')
  remote('write',requested=True,feature_enabled=True,desired_on=True,status='active',vpn_public_ip=public,baseline_public_ip=baseline,error='',container=glue,persistent=True,credential_volume=volume,control_status='applied',control_error='',control_heartbeat=time.time())
  # Record startup choice for the existing test install, without resetting WGAL or its guide.
  source='import setup_wizard;setup_wizard.save_state({"vpn":{"requested":True,"profile_id":None,"provider":'+repr(job['provider'])+'}})'
  if PORT==9998:run(['docker','exec',TARGET,'python','-c',source])
  if PORT==9998 and before.get('completed'):
   run(['docker','exec',TARGET,'python','-c','import setup_wizard;setup_wizard.save_state({"completed":True})'])
  canonical='';key=''
  print('VPN active for Picker on '+str(PORT)+'; protected egress verified.',flush=True)
  if not before.get('completed') and job.get('phase','build')=='build':
   with urllib.request.urlopen(urllib.request.Request('http://localhost:'+str(PORT)+'/api/setup/build',data=b'{}',headers={'Content-Type':'application/json'}),timeout=30) as r:
    if r.status!=200:raise RuntimeError('Initial guide preparation could not resume.')
  # Backup is stopped and contains no VPN upload; keep it for manual rollback, with no auto-restart.
  run(['docker','update','--restart','no',backup])
  return True
 except Exception as exc:
  print('VPN startup failed; restoring the setup screen. '+type(exc).__name__,flush=True)
  if compose_started:run(compose+['down'],timeout=60)
  if previous_manifest is not None:path.write_bytes(previous_manifest)
  else:path.unlink(missing_ok=True)
  if volume and volume!=job.get('saved_credential_volume'):run(['docker','volume','rm',volume])
  if renamed:run(['docker','rename',backup,TARGET]);run(['docker','start',TARGET])
  remote('write',requested=True,status='failed',error='VPN startup failed. Upload and test the file again; playback remains blocked.')
  source='import setup_wizard;setup_wizard.save_state({"completed":False,"current_step":"choices","vpn":{"requested":True}})'
  if PORT==9998:run(['docker','exec',TARGET,'python','-c',source])
  raise

def switch_connection(job):
 path=manifest_path()
 previous=json.loads(path.read_text(encoding='utf-8'))
 service=previous['services'][previous['x-m3u-vpn']['service']]
 if service['container_name']!=TARGET or str(service['environment'].get('M3U_PORT','9999'))!=str(PORT) or service['environment'].get('M3U_DATA_DIR')!=DATA:raise ValueError('VPN graph does not match the selected instance.')
 validate_target(json.loads(run(['docker','inspect',TARGET]))[0])
 enabled=job['desired_on'];configuration=vpn_deployment.connection_mode(previous,enabled)
 compose=compose_command(path);changed=False
 try:
  check_idle()
  print('Port '+str(PORT)+': switching to '+('VPN' if enabled else 'normal internet')+'; playback will be briefly unavailable.',flush=True)
  baseline=public_baseline()
  run(compose+['down'],timeout=60);changed=True
  vpn_deployment.save_manifest(path,configuration)
  run(compose+['up','-d','--no-build'],timeout=150)
  until=time.monotonic()+60
  while True:
   try:
    with urllib.request.urlopen('http://localhost:'+str(PORT)+'/api/vpn-state',timeout=3) as response:state=json.load(response)
    if state.get('network_protected') is enabled and (not enabled or state.get('app_vpn_active')):break
   except Exception:pass
   if time.monotonic()>until:raise RuntimeError('Connection switch failed health checks.')
   time.sleep(2)
  actual=run(['docker','exec',TARGET,'python','-c','import urllib.request;print(urllib.request.build_opener(urllib.request.ProxyHandler({})).open("https://api.ipify.org",timeout=15).read().decode().strip())'])
  if (enabled and actual==baseline) or (not enabled and actual!=baseline):raise RuntimeError('Connection switch failed egress verification.')
  remote('write',control_status='applied',control_error='',status='active' if enabled else 'off',control_heartbeat=time.time(),**({'vpn_public_ip':actual} if enabled else {}))
  print('Port '+str(PORT)+' connection verified: '+('VPN connected.' if enabled else 'VPN off; normal internet active.'),flush=True)
 except Exception:
  if changed:
   run(compose+['down'],timeout=60);vpn_deployment.save_manifest(path,previous);run(compose+['up','-d','--no-build'],timeout=150)
  fields={'control_status':'failed','control_error':'Connection switch failed or playback is active. Stop streams and try again.'}
  # A failed settings disable restored the protected graph: keep its control visible.
  if not enabled and job.get('feature_enabled') is False:fields['feature_enabled']=True
  remote('write',**fields)
  raise

def valid_app_image(value):
 return bool(re.fullmatch(r'(?:m3u-web-picker|ghcr.io/zschmook/m3u-web-picker):[a-zA-Z0-9_.-]+',value))

def main(stop_event=None):
 global MANIFEST_OVERRIDE
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--watch',action='store_true');p.add_argument('--apply-current',action='store_true');p.add_argument('--allow-restart-9998',action='store_true');p.add_argument('--allow-restart-9999',action='store_true');p.add_argument('--port',type=int,choices=[9998,9999],default=9998);p.add_argument('--manifest');p.add_argument('--image',required=True);args=p.parse_args()
 configure_target(args.port)
 if args.manifest:
  selected=Path(args.manifest)
  if PORT!=9999 or not selected.is_absolute() or selected.name!='docker-compose.vpn.json' or selected.parent.name!='vpn':p.error('Use the selected production installation runtime/vpn/docker-compose.vpn.json path.')
  MANIFEST_OVERRIDE=selected
 if not (args.allow_restart_9998 if PORT==9998 else args.allow_restart_9999):p.error('Explicit restart authorization for the selected port is required.')
 if not valid_app_image(args.image):p.error('Use a Picker local or official release image tag.')
 if args.apply_current:
  job=queue_current();canonical=read_upload(job)
  print('Passing upload captured in process memory for the startup handoff.',flush=True)
  while True:
   try:
    run(['docker','image','inspect',args.image])
    if PORT==9999 or (ROOT/'runtime/deploy/vpn-startup-20261008/apply-ready').is_file():break
   except RuntimeError:time.sleep(2)
   time.sleep(1)
  apply(job,canonical,args.image)
  canonical=''
 if not args.watch:return
 import threading
 stop=stop_event or threading.Event()
 while not stop.is_set():
  try:
   validate_target(json.loads(run(['docker','inspect',TARGET]))[0])
   remote('write',control_heartbeat=time.time())
   control=remote('claim_control')
   if control:
    if manifest_path().is_file():switch_connection(control)
    elif control.get('desired_on'):
     queue_current()
    else:remote('write',requested=False,desired_on=False,status='off',control_status='applied',control_error='')
   job=remote('claim')
   if job:apply(job,read_upload(job),args.image)
  except Exception:print('Startup helper waiting for an available test instance or a corrected upload.',flush=True)
  stop.wait(3)
if __name__=='__main__':main()
