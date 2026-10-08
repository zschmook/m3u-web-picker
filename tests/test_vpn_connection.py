import unittest,json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from flask import Flask
import core,vpn_testing,vpn_config
from api.vpn import register_vpn_routes
from tests.test_vpn_config import PROFILE,PRIVATE

class VpnConnectionQueueTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.db=self.root/'test.db';self.ram=TemporaryDirectory();self.addCleanup(self.ram.cleanup);self.ram_patch=patch.object(vpn_config,'RAM_ROOT',Path(self.ram.name));self.ram_patch.start();self.addCleanup(self.ram_patch.stop)
 def save(self):return vpn_config.save(self.db,self.root,{'provider':'protonvpn','lan_subnets':['10.0.0.0/24'],'wireguard_config':PROFILE},'a'*32)
 def test_profile_required_before_any_job_is_created(self):
  with self.assertRaises(ValueError):vpn_testing.create(self.db,self.root)
  self.assertIsNone(vpn_testing.latest(self.db,self.root)['test'])
 def test_queue_rejects_duplicate_tests_and_claims_once(self):
  self.save();first=vpn_testing.create(self.db,self.root)
  with self.assertRaises(ValueError):vpn_testing.create(self.db,self.root)
  claimed=vpn_testing.claim(self.db);self.assertEqual(first['test']['id'],claimed['id']);self.assertIsNone(vpn_testing.claim(self.db))
  self.assertNotIn(PRIVATE,json.dumps(claimed))
 def test_results_are_separate_from_activation(self):
  self.save();job=vpn_testing.create(self.db,self.root)['test']['id']
  vpn_testing.update(self.db,job,'passed','Finished',{'checks':{'dns_resolved':True},'app_vpn_active':False})
  latest=vpn_testing.latest(self.db,self.root);self.assertEqual(latest['test']['status'],'passed');self.assertFalse(latest['app_vpn_active']);self.assertEqual(vpn_config.status(self.db,self.root)['activation'],'not_applied')
 def test_changed_profile_marks_old_result_stale(self):
  self.save();job=vpn_testing.create(self.db,self.root)['test']['id'];vpn_testing.update(self.db,job,'passed','Finished',{})
  vpn_config.save(self.db,self.root,{'lan_subnets':['10.0.0.0/24'],'wireguard_config':PROFILE.replace('8.8.8.8','1.1.1.1')})
  self.assertTrue(vpn_testing.latest(self.db,self.root)['test']['profile_changed'])
 def test_changed_lan_exceptions_also_make_results_stale(self):
  self.save();job=vpn_testing.create(self.db,self.root)['test']['id'];vpn_testing.update(self.db,job,'passed','Finished',{})
  vpn_config.save(self.db,self.root,{'lan_subnets':['192.168.50.0/24'],'wireguard_config':PROFILE})
  self.assertTrue(vpn_testing.latest(self.db,self.root)['test']['profile_changed'])
 def test_helper_heartbeat_has_an_expiration(self):
  self.assertFalse(vpn_testing.latest(self.db,self.root)['helper_available']);vpn_testing.heartbeat(self.db);self.assertTrue(vpn_testing.latest(self.db,self.root)['helper_available'])
  with patch('vpn_testing.time.time',return_value=99999999999):self.assertFalse(vpn_testing.latest(self.db,self.root)['helper_available'])
 def test_api_starts_queue_and_returns_only_metadata(self):
  self.save();app=Flask(__name__);register_vpn_routes(app)
  with patch.multiple(core,DB_PATH=self.db,DATA_DIR=self.root):
   client=app.test_client();client.environ_base['HTTP_X_VPN_SESSION']='a'*32;result=client.post('/api/vpn-test');self.assertEqual(result.status_code,202);self.assertIn('no-store',result.headers['Cache-Control']);self.assertNotIn(PRIVATE,result.get_data(as_text=True));self.assertFalse(result.json['app_vpn_active'])
   self.assertEqual(client.post('/api/vpn-test').status_code,409);self.assertEqual(client.get('/api/vpn-test').json['test']['status'],'pending')
 def test_wizard_continue_requires_current_passing_test(self):
  import setup_app
  self.save()
  with patch.multiple(core,DB_PATH=self.db,DATA_DIR=self.root),patch('setup_app.setup_wizard.save_choices',return_value={'mode':'provider'}),patch('setup_app.setup_wizard.save_state',return_value={'mode':'provider'}):
   client=setup_app.app.test_client();client.environ_base['HTTP_X_VPN_SESSION']='a'*32
   body={'mode':'provider','vpn_requested':True}
   self.assertEqual(client.post('/api/setup/choices',json=body).status_code,409)
   job=vpn_testing.create(self.db,self.root,'a'*32)['test']['id']
   vpn_testing.update(self.db,job,'passed','Finished',{'passed':True})
   self.assertEqual(client.post('/api/setup/choices',json=body).status_code,202)
   self.assertEqual(client.post('/api/setup/choices',json=body,headers={'X-VPN-Session':'b'*32}).status_code,409)
   vpn_config.discard(self.db,self.root,'a'*32)
   self.assertEqual(client.post('/api/setup/choices',json=body).status_code,409)
   self.assertEqual(client.post('/api/setup/choices',json={'mode':'provider','vpn_requested':False}).status_code,200)
