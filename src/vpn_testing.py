"""SQLite queue and results for host-run, isolated VPN connection checks."""
from contextlib import closing
from datetime import datetime,timezone
import json
import time
import uuid
from database import connect
import vpn_config


def _db(path):
 c=connect(path)
 c.execute('CREATE TABLE IF NOT EXISTS vpn_connection_tests(id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, settings_json TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, results_json TEXT NOT NULL)')
 c.execute('CREATE TABLE IF NOT EXISTS vpn_test_helper(id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL)')
 c.commit();return c

def create(path,data_dir,session=None):
 profile=vpn_config.status(path,data_dir,session)
 if not profile['configuration_present']:raise ValueError('Select a VPN configuration file before testing the connection.')
 settings={k:profile[k] for k in ['profile_id','provider','lan_subnets']}
 now=time.time();job=uuid.uuid4().hex
 with closing(_db(path)) as c:
  c.execute('BEGIN IMMEDIATE')
  busy=c.execute("SELECT id FROM vpn_connection_tests WHERE status IN ('pending','running') AND updated_at>?",(now-600,)).fetchone()
  if busy:raise ValueError('A VPN connection test is already queued or running.')
  c.execute('INSERT INTO vpn_connection_tests VALUES(?,?,?,?,?,?,?,?)',(job,profile['profile_id'],json.dumps(settings),'pending','Waiting for the host helper',now,now,'{}'));c.commit()
 return latest(path,data_dir,session)

def claim(path):
 with closing(_db(path)) as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute("SELECT id,settings_json FROM vpn_connection_tests WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
  if not row:return None
  c.execute("UPDATE vpn_connection_tests SET status='running',stage='Preparing isolated probe',updated_at=? WHERE id=?",(time.time(),row[0]));c.commit()
  return {'id':row[0],**json.loads(row[1])}

def update(path,job,status,stage,results=None):
 with closing(_db(path)) as c:
  if results is None:c.execute('UPDATE vpn_connection_tests SET status=?,stage=?,updated_at=? WHERE id=?',(status,stage,time.time(),job))
  else:c.execute('UPDATE vpn_connection_tests SET status=?,stage=?,updated_at=?,results_json=? WHERE id=?',(status,stage,time.time(),json.dumps(results),job))
  c.commit()

def heartbeat(path):
 with closing(_db(path)) as c:
  c.execute('INSERT INTO vpn_test_helper VALUES(1,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat',(time.time(),));c.commit()

def latest(path,data_dir,session=None):
 profile=vpn_config.status(path,data_dir,session)
 with closing(_db(path)) as c:
  row=c.execute('SELECT id,profile_id,status,stage,created_at,updated_at,results_json,settings_json FROM vpn_connection_tests ORDER BY created_at DESC LIMIT 1').fetchone()
  helper=c.execute('SELECT heartbeat FROM vpn_test_helper WHERE id=1').fetchone()
 import vpn_runtime
 result={'helper_available':bool(helper and time.time()-helper[0]<20),'test':None,'app_vpn_active':vpn_runtime.healthy()}
 if row:result['test']={'id':row[0],'profile_id':row[1],'status':row[2],'stage':row[3],'created_at':datetime.fromtimestamp(row[4],timezone.utc).isoformat(),'updated_at':datetime.fromtimestamp(row[5],timezone.utc).isoformat(),'results':json.loads(row[6]),'profile_changed':row[1]!=profile['profile_id'] or sorted(json.loads(row[7])['lan_subnets'])!=sorted(profile['lan_subnets'])}
 return result
