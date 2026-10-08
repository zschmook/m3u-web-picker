"""Clean-install the isolated port-9998 setup instance, never production."""
import argparse,json,re,subprocess,time,urllib.request
from pathlib import Path
TARGET='m3u-picker-setup'

def run(args):
 r=subprocess.run(args,capture_output=True,text=True,check=True)
 return r.stdout.strip()

def inspect(name):return json.loads(run(['docker','inspect',name]))[0]

def environment(info):return dict(item.split('=',1) for item in info['Config']['Env'])

def prepare(file):
 config=json.loads(run(['docker','compose','-f',str(file),'config','--format','json']))
 if config.get('name')!='m3u-picker-setup' or set(config['services'])!={'setup'}:raise ValueError('Only the isolated setup project is allowed.')
 service=config['services']['setup']
 if service.get('container_name')!=TARGET or str(service['environment'].get('M3U_PORT'))!='9998':raise ValueError('Only port 9998 is allowed.')
 ports=service.get('ports',[])
 if len(ports)!=1 or str(ports[0]['published'])!='9998' or ports[0]['target']!=9998:raise ValueError('Only the port-9998 mapping is allowed.')
 if service.get('network_mode') or service['environment'].get('M3U_VPN_REQUIRED')=='true':raise ValueError('CL must return to unconfigured setup.')
 run(['docker','image','inspect',service['image']])
 production=inspect('m3u-picker');old=inspect(TARGET)
 if environment(old).get('M3U_PORT')!='9998':raise ValueError('The target container is not the isolated instance.')
 protected={m.get('Name') for m in production['Mounts'] if m['Type']=='volume'}
 old_volumes={m['Name']:m['Destination'] for m in old['Mounts'] if m['Type']=='volume'}
 erase=[name for name,target in old_volumes.items() if target in ('/app/setup-data','/app/setup-output','/backups','/jellyfin-cache')]
 if any(name in protected or not name.startswith('m3u-picker-setup_') for name in erase):raise ValueError('Refusing a production or unexpected volume.')
 for mount in service['volumes']:
  if mount['type']!='volume' or mount['source'] in protected:raise ValueError('Fresh setup must use isolated named volumes.')
  if mount['target'] in ('/app/setup-data','/app/setup-output') and mount['source'] in old_volumes:raise ValueError('CL must use fresh application volumes.')
 owned=[TARGET]
 names=run(['docker','ps','-a','--format','{{.Names}}']).splitlines()
 for name in names:
  if name.startswith(TARGET+'-relay-backup-') or name.startswith(TARGET+'-pre-vpn-'):
   info=inspect(name)
   if info['State']['Running'] or environment(info).get('M3U_PORT')!='9998':raise ValueError('Unexpected active backup instance.')
   mounts={m['Name'] for m in info['Mounts'] if m['Type']=='volume'}
   if not mounts.issubset(set(old_volumes)):raise ValueError('Unexpected backup data volumes.')
   owned.append(name)
 mode=old['HostConfig']['NetworkMode']
 vpn=None
 if mode.startswith('container:'):
  info=inspect(mode.split(':',1)[1]);name=info['Name'].lstrip('/')
  label=info['Config']['Labels'].get('m3u.vpn-startup','')
  if not name.startswith('m3u-picker-vpn-') or not re.fullmatch('[a-f0-9]{32}',label):raise ValueError('Unexpected VPN container.')
  bindings=info['HostConfig']['PortBindings']
  if set(bindings)!={'9998/tcp'} or any(p['HostPort']!='9998' for p in bindings['9998/tcp']):raise ValueError('VPN publishes unexpected ports.')
  vpn=name
 return {'production_id':production['Id'],'remove_containers':owned,'remove_vpn':vpn,'erase_volumes':erase,'recordings_retained':True,'image':service['image']}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mode',choices=['CL'],required=True);p.add_argument('--compose-file',type=Path,required=True);p.add_argument('--plan',action='store_true');args=p.parse_args()
 file=args.compose_file.resolve();plan=prepare(file)
 print(json.dumps({'mode':'CL','port':9998,**plan},indent=2),flush=True)
 if args.plan:return
 for name in plan['remove_containers']:run(['docker','rm','-f',name])
 if plan['remove_vpn']:run(['docker','rm','-f',plan['remove_vpn']])
 for name in plan['erase_volumes']:run(['docker','volume','rm',name])
 run(['docker','compose','-f',str(file),'up','-d','--no-deps','--pull','never','setup'])
 deadline=time.monotonic()+90
 while True:
  try:
   with urllib.request.urlopen('http://localhost:9998/api/setup/state',timeout=3) as r:state=json.load(r)
   if state['state']['current_step']=='choices' and not state['state']['completed'] and state['selected_count']==0:break
  except Exception:pass
  if time.monotonic()>deadline:raise RuntimeError('Fresh setup did not become ready.')
  time.sleep(1)
 if inspect('m3u-picker')['Id']!=plan['production_id']:raise RuntimeError('Production identity changed unexpectedly.')
 print('CL complete: fresh setup on 9998; production 9999 unchanged; recordings retained.',flush=True)
if __name__=='__main__':main()
