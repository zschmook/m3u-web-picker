"""Non-secret startup VPN handoff state and live tunnel health."""
from contextlib import closing
import json,os,time,uuid,urllib.request
from database import connect
from pathlib import Path

def _db(path):
 c=connect(path);c.execute('CREATE TABLE IF NOT EXISTS vpn_activation(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)');c.commit();return c

def read(path):
 with closing(_db(path)) as c:row=c.execute('SELECT payload FROM vpn_activation WHERE id=1').fetchone()
 return json.loads(row[0]) if row else {'requested':False,'status':'not_applied'}

def write(path,**fields):
 with closing(_db(path)) as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM vpn_activation WHERE id=1').fetchone();data=json.loads(row[0]) if row else {};data.update(fields);data['updated_at']=time.time()
  c.execute('INSERT INTO vpn_activation VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(data),));c.commit()
 return data

def queue(path,data_dir,profile_id,phase='build'):
 import vpn_testing
 state=vpn_testing.latest(path,data_dir);test=state.get('test')
 if not test or test['status']!='passed' or test['profile_changed'] or test['profile_id']!=profile_id:raise ValueError('Upload and pass the VPN tests again before building.')
 import vpn_config
 profile=vpn_config.status(path,data_dir)
 current=read(path)
 if current.get('status') in ('pending','applying'):return current
 return write(path,id=uuid.uuid4().hex,requested=True,feature_enabled=True,desired_on=True,status='pending',phase=phase,profile_id=profile_id,provider=profile['provider'],lan_subnets=profile['lan_subnets'],error='')

def claim(path):
 with closing(_db(path)) as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM vpn_activation WHERE id=1').fetchone();data=json.loads(row[0]) if row else {}
  if data.get('status')!='pending':return None
  data.update(status='applying',updated_at=time.time());c.execute('UPDATE vpn_activation SET payload=? WHERE id=1',(json.dumps(data),));c.commit();return data

def required():return os.environ.get('M3U_VPN_REQUIRED','').lower()=='true'

def healthy():
 if not required():return False
 try:
  # The health HTTP response can lag a stopped tunnel. Check its shared interface first.
  flags=int(Path('/sys/class/net/tun0/flags').read_text().strip(),16)
  if not flags & 1:return False
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  with opener.open('http://127.0.0.1:9990/',timeout=2) as r:return r.status==200
 except Exception:return False

def status(path):
 data=read(path);data.pop('profile_id',None)
 data['app_vpn_active']=healthy();data['memory_only']=not bool(data.get('persistent'))
 data['desired_on']=bool(data.get('desired_on',data.get('requested') or required()))
 data['network_protected']=required()
 data['configured']=bool(data.get('persistent'))
 data['enabled']=feature_enabled(data)
 data['control_available']=data['configured'] and time.time()-data.get('control_heartbeat',0)<20
 if data.get('control_status') in ('pending','applying'):data['status']='switching'
 elif required():data['status']='active' if data['desired_on'] and data['app_vpn_active'] else 'disconnected' if data['desired_on'] else 'switching'
 elif data['configured']:data['status']='disconnected' if data['desired_on'] else 'off'
 return data

def feature_enabled(data):
 # Existing applied installations opted into VPN before this separate setting existed.
 return bool(data.get('feature_enabled',data.get('persistent') or data.get('requested') or required()))

def set_enabled(path, enabled):
 if type(enabled) is not bool:raise ValueError('Choose whether to use a VPN.')
 with closing(_db(path)) as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM vpn_activation WHERE id=1').fetchone();data=json.loads(row[0]) if row else {}
  previous=feature_enabled(data)
  if data.get('control_status') in ('pending','applying'):raise ValueError('Wait for the current connection change to finish.')
  switching=bool(data.get('persistent')) and ((enabled and not previous and not required()) or (not enabled and required()))
  if switching and time.time()-data.get('control_heartbeat',0)>=20:raise ValueError('VPN connection manager is unavailable. The setting has not changed.')
  data.update(feature_enabled=enabled,updated_at=time.time())
  if not enabled or (enabled and not previous and data.get('persistent')):data['desired_on']=enabled
  if switching:data.update(control_id=uuid.uuid4().hex,control_status='pending',control_error='')
  elif not enabled:data.update(control_status='applied',control_error='')
  c.execute('INSERT INTO vpn_activation VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(data),));c.commit()
 return status(path)

def protection_missing(path, wizard_requested=False):
 data=read(path)
 wanted=data.get('desired_on',data.get('requested') or wizard_requested or required())
 return (bool(wanted) and not required()) or (required() and not healthy())

def request_control(path, enabled):
 if type(enabled) is not bool:raise ValueError('Choose VPN on or off.')
 with closing(_db(path)) as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM vpn_activation WHERE id=1').fetchone();data=json.loads(row[0]) if row else {}
  if not data.get('persistent'):raise ValueError('Apply a VPN configuration before using the power button.')
  if not feature_enabled(data):raise ValueError('Enable Use a VPN in Settings before using the power button.')
  if time.time()-data.get('control_heartbeat',0)>=20:raise ValueError('VPN connection manager is unavailable. Try again when it is running.')
  if data.get('control_status') in ('pending','applying'):raise ValueError('The connection is already switching.')
  data.update(desired_on=enabled,control_id=uuid.uuid4().hex,control_status='pending',control_error='',updated_at=time.time())
  c.execute('UPDATE vpn_activation SET payload=? WHERE id=1',(json.dumps(data),));c.commit()
 return status(path)

def claim_control(path):
 with closing(_db(path)) as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM vpn_activation WHERE id=1').fetchone();data=json.loads(row[0]) if row else {}
  if data.get('control_status')!='pending':return None
  data.update(control_status='applying',updated_at=time.time());c.execute('UPDATE vpn_activation SET payload=? WHERE id=1',(json.dumps(data),));c.commit();return data
