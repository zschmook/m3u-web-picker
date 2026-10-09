"""Host helper for disposable VPN probes. It never recreates a Picker container."""
from __future__ import annotations
import argparse,atexit,ipaddress,json,os,re,secrets,shutil,subprocess,sys,threading,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from vpn_config import parse_wireguard,lan_networks
TARGET='m3u-picker-setup'
DB='/app/setup-data/m3u_picker.db'
DATA='/app/setup-data'
PORT=9998
REMOTE='''import json,sys,vpn_testing,vpn_config
p=json.load(sys.stdin);op=p['operation'];db=p['db'];data=p['data']
if op=='create':r=vpn_testing.create(db,data)
elif op=='claim':r=vpn_testing.claim(db)
elif op=='latest':r=vpn_testing.latest(db,data)
elif op=='heartbeat':vpn_testing.heartbeat(db);r={}
elif op=='update':vpn_testing.update(db,p['id'],p['status'],p['stage'],p.get('results'));r={}
elif op=='save_profile':r=vpn_config.retain_verified_profile(db,data,p)
else:raise ValueError('Unknown operation')
print(json.dumps(r))'''

def run(args,body=None,timeout=30):
 result=subprocess.run(args,input=body,capture_output=True,text=True,timeout=timeout,check=False,
  **({'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)} if os.name=='nt' else {}))
 if result.returncode:raise RuntimeError('A Docker or host probe command failed; inspect the local test environment.')
 return result.stdout.strip()

def configure_target(port):
 global TARGET,DB,DATA,PORT
 if port not in (9998,9999):raise ValueError('Choose the setup port 9998 or production port 9999.')
 PORT=port;TARGET='m3u-picker-setup' if port==9998 else 'm3u-picker'
 DATA='/app/setup-data' if port==9998 else '/app/data';DB=DATA+'/m3u_picker.db'

def validate_target(info):
 env=dict(v.split('=',1) for v in info['Config']['Env'])
 expected='m3u-picker-setup' if PORT==9998 else 'm3u-picker'
 expected_data='/app/setup-data' if PORT==9998 else '/app/data'
 if info.get('Name')!='/'+expected or TARGET!=expected or DATA!=expected_data or DB!=expected_data+'/m3u_picker.db':
  raise ValueError('VPN test target does not match the selected instance.')
 if env.get('M3U_PORT','9999')!=str(PORT) or env.get('M3U_DATA_DIR')!=expected_data:
  raise ValueError('VPN test port or data directory does not match the selected instance.')
 shared=info['HostConfig'].get('NetworkMode','').startswith('container:') and env.get('M3U_VPN_REQUIRED')=='true'
 if PORT==9999 and not shared and not any(str(p.get('HostPort'))=='9999' for p in info['HostConfig'].get('PortBindings',{}).get('9999/tcp',[]) or []):
  raise ValueError('Production container is not published on port 9999.')
 return env

def remote(operation,**data):return json.loads(run(['docker','exec','-i',TARGET,'python','-c',REMOTE],json.dumps({'operation':operation,'db':DB,'data':DATA,**data})))
def stage(job,message):
 remote('update',id=job,status='running',stage=message);print(message,flush=True)

def public_baseline():
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('https://api.ipify.org',timeout=10) as response:
  value=response.read(1024).decode().strip();ipaddress.ip_address(value);return value

def tailnet_ip():
 executable=shutil.which('tailscale')
 if not executable and os.name=='nt' and Path('C:/Program Files/Tailscale/tailscale.exe').is_file():executable='C:/Program Files/Tailscale/tailscale.exe'
 if not executable:return ''
 try:
  value=run([executable,'ip','-4'],timeout=10).splitlines()[0]
  return value if ipaddress.ip_address(value) in ipaddress.ip_network('100.64.0.0/10') else ''
 except (ValueError,RuntimeError,IndexError):return ''

def execute(job):
 if not re.fullmatch('[a-f0-9]{32}',job['id']) or not re.fullmatch('[a-f0-9]{64}',job['profile_id']):raise ValueError('Invalid test identity')
 inspect=json.loads(run(['docker','inspect',TARGET]))[0]
 env=validate_target(inspect)
 image=inspect['Config']['Image'];lan=env.get('M3U_LAN_HOST','')
 subnets=lan_networks(job['lan_subnets']);local={}
 if lan and ipaddress.ip_address(lan).version==4 and any(ipaddress.ip_address(lan) in ipaddress.ip_network(n) for n in subnets):local['lan']='http://'+lan+':'+str(PORT)+'/api/vpn-config'
 tail=tailnet_ip()
 if tail:local['tailscale_host']='http://'+tail+':'+str(PORT)+'/api/vpn-config';subnets.append(tail+'/32')
 glue='m3u-vpn-probe-'+job['id'];client='m3u-vpn-client-'+job['id'];owned=[]
 def cleanup():
  # Only the uniquely labelled containers created by this test are removed.
  for name in reversed(owned):
   try:
    label=run(['docker','inspect','--format','{{index .Config.Labels "m3u.vpn-test"}}',name])
    if label==job['id']:run(['docker','rm','-f',name])
   except Exception:pass
 atexit.register(cleanup)
 report={'scope':'isolated_connection_test','app_vpn_active':False,'checks':{},'test_network_exceptions':subnets,'tailscale_scope':'Checks this host’s tailnet address using a narrow temporary exception, not a remote-device playback session.'}
 probe_source=(ROOT/'scripts/vpn_probe_client.py').read_text(encoding='utf-8-sig')
 key=''.join(secrets.choice('123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz') for _ in range(22))
 def probe(action,**fields):
  return json.loads(run(['docker','exec','-i',client,'python','-c',probe_source],json.dumps({'action':action,'api_key':key,'local_urls':local,**fields}),timeout=45))
 def healthy(timeout=90):
  until=time.monotonic()+timeout
  while time.monotonic()<until:
   if probe('health').get('healthy'):return True
   time.sleep(2)
  return False
 try:
  stage(job['id'],'Reading the temporary upload and measuring normal egress')
  source='import vpn_config;print(vpn_config.profile_for_test('+repr(DB)+','+repr(job['profile_id'])+'))'
  canonical,_=parse_wireguard(run(['docker','exec',TARGET,'python','-c',source]))
  auth='[[roles]]\nname = "picker-test"\nroutes = ["PUT /v1/vpn/status"]\nauth = "apikey"\napikey = "'+key+'"\n'
  report['baseline_public_ip']=public_baseline()
  digest=json.loads(run(['docker','image','inspect','qmcgaw/gluetun:latest']))[0]['RepoDigests'][0]
  report['gluetun_image']=digest
  stage(job['id'],'Starting a disposable tunnel (Picker networking is unchanged)')
  args=['docker','run','-d','--rm','--name',glue,'--label','m3u.vpn-test='+job['id'],'--cap-add','NET_ADMIN','--device','/dev/net/tun:/dev/net/tun','--sysctl','net.ipv6.conf.all.disable_ipv6=1',
   '--tmpfs','/gluetun:rw,noexec,nosuid,size=16m','--entrypoint','/bin/sh']
  for name,value in {'VPN_SERVICE_PROVIDER':'custom','VPN_TYPE':'wireguard','HEALTH_SERVER_ADDRESS':'127.0.0.1:9990','HEALTH_RESTART_VPN':'off','FIREWALL_OUTBOUND_SUBNETS':','.join(subnets),'BLOCK_MALICIOUS':'off','DNS_UPDATE_PERIOD':'0'}.items():args+=['-e',name+'='+value]
  run(args+[digest,'-c','while [ ! -f /gluetun/wireguard/wg0.conf ]; do sleep 1; done; exec /gluetun-entrypoint'],timeout=60);owned.append(glue)
  # Feed keys through stdin into tmpfs. Never put credentials on host disk or Docker arguments.
  run(['docker','exec','-i',glue,'sh','-c','umask 077; mkdir -p /gluetun/auth /gluetun/wireguard; cat > /gluetun/auth/config.toml'],auth)
  run(['docker','exec','-i',glue,'sh','-c','umask 077; cat > /gluetun/wireguard/upload; mv /gluetun/wireguard/upload /gluetun/wireguard/wg0.conf'],canonical)
  canonical='';auth=''
  run(['docker','run','-d','--rm','--name',client,'--label','m3u.vpn-test='+job['id'],'--network','container:'+glue,'--entrypoint','python',image,'-c','import time;time.sleep(300)']);owned.append(client)
  if not healthy():raise RuntimeError('The tunnel did not become healthy within 90 seconds.')
  report['checks']['tunnel_healthy']=True
  stage(job['id'],'Checking VPN egress, internal DNS, LAN, and tailnet-host access')
  connected=probe('connect')
  if connected.get('error'):raise RuntimeError('The encrypted egress/DNS check failed ('+connected['error']+').')
  report['vpn_public_ip']=connected['public_ip'];report['checks']['egress_changed']=connected['public_ip']!=report['baseline_public_ip']
  report['checks']['dns_resolved']=connected['dns_resolved'];report['dns_resolvers']=connected['resolvers']
  report['checks'].update({name+'_reachable':value for name,value in connected['local_access'].items()})
  stage(job['id'],'Testing the kill switch by disconnecting only the disposable tunnel')
  if not probe('stop').get('accepted'):raise RuntimeError('The disposable tunnel could not be stopped for the kill-switch check.')
  time.sleep(2)
  blocked=probe('blocked',probe_address=connected['probe_address'])
  if 'internet_blocked' not in blocked:raise RuntimeError('The kill-switch check could not complete.')
  report['checks']['internet_blocked_when_vpn_down']=blocked['internet_blocked']
  report['checks'].update({name+'_reachable_when_vpn_down':value for name,value in blocked['local_access'].items()})
  stage(job['id'],'Restoring the disposable tunnel and checking recovery')
  if not probe('start').get('accepted'):raise RuntimeError('The disposable tunnel could not be restarted.')
  ready=healthy(45)
  recovered=probe('connect') if ready else {}
  report['checks']['tunnel_recovers']=ready and not recovered.get('error') and recovered.get('public_ip') not in (None,report['baseline_public_ip'])
  required=['tunnel_healthy','egress_changed','dns_resolved','internet_blocked_when_vpn_down','tunnel_recovers']
  required += [name+suffix for name in local for suffix in ('_reachable','_reachable_when_vpn_down')]
  report['passed']=all(report['checks'].get(name) is True for name in required)
  if report['passed']:
   stage(job['id'],'Saving the verified configuration in private Docker storage')
   import vpn_deployment
   canonical,_=parse_wireguard(run(['docker','exec',TARGET,'python','-c',source]))
   auth='[[roles]]\nname = "picker-host"\nroutes = ["PUT /v1/vpn/status"]\nauth = "apikey"\napikey = "'+key+'"\n'
   volume=vpn_deployment.create_credentials(job['id'],canonical,auth,digest)
   canonical='';auth=''
   try:remote('save_profile',id=job['id'],profile_id=job['profile_id'],volume=volume,image=digest,results=report)
   except Exception:
    run(['docker','volume','rm',volume]);raise
   report['configuration_saved']=True
  else:remote('update',id=job['id'],status='failed',stage='Connection test failed; app networking is unchanged',results=report)
  print(json.dumps(report,indent=2),flush=True)
 except Exception as error:
  report['passed']=False;report['error']=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__
  remote('update',id=job['id'],status='failed',stage='Connection test failed; app networking is unchanged',results=report)
  print(json.dumps(report,indent=2),flush=True)
 finally:cleanup();atexit.unregister(cleanup)
 return report

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--watch',action='store_true');p.add_argument('--port',type=int,choices=[9998,9999],default=9998);args=p.parse_args()
 configure_target(args.port)
 validate_target(json.loads(run(['docker','inspect',TARGET]))[0])
 print('Watching isolated VPN tests for port '+str(PORT)+'. Picker networking is unchanged.',flush=True)
 stop=threading.Event()
 def beat():
  while not stop.is_set():
   try:remote('heartbeat')
   except Exception:pass
   stop.wait(5)
 thread=threading.Thread(target=beat,daemon=True);thread.start()
 try:
  if not args.watch:remote('create')
  while True:
   try:job=remote('claim')
   except (RuntimeError,subprocess.TimeoutExpired,json.JSONDecodeError):
    if not args.watch:raise
    time.sleep(3);continue
   if job:result=execute(job)
   if not args.watch:return 0 if job and result.get('passed') else 1
   time.sleep(2)
 finally:stop.set()
if __name__=='__main__':raise SystemExit(main())
