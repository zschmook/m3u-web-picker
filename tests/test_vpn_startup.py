import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import vpn_runtime,vpn_config,vpn_testing
from tests.test_vpn_config import PROFILE
class StartupQueueTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.db=self.root/'db'
  mock=patch.object(vpn_config,'RAM_ROOT',self.root/'ram');mock.start();self.addCleanup(mock.stop)
 def passed(self):
  profile=vpn_config.save(self.db,self.root,{'provider':'protonvpn','lan_subnets':['10.0.0.0/24'],'wireguard_config':PROFILE})
  job=vpn_testing.create(self.db,self.root)['test']['id'];vpn_testing.update(self.db,job,'passed','Passed',{});return profile
 def test_requires_current_pass_and_claims_handoff_once(self):
  with self.assertRaises(ValueError):vpn_runtime.queue(self.db,self.root,'missing')
  profile=self.passed();queued=vpn_runtime.queue(self.db,self.root,profile['profile_id']);self.assertEqual(queued['status'],'pending');self.assertNotIn('PrivateKey',str(queued))
  self.assertEqual(vpn_runtime.claim(self.db)['status'],'applying');self.assertIsNone(vpn_runtime.claim(self.db))
 def test_changed_or_expired_upload_cannot_be_applied(self):
  profile=self.passed();vpn_config.discard(self.db,self.root)
  with self.assertRaises(ValueError):vpn_runtime.queue(self.db,self.root,profile['profile_id'])
 def test_activation_never_claims_active_without_runtime_health(self):
  vpn_runtime.write(self.db,requested=True,status='active')
  with patch.object(vpn_runtime,'required',return_value=True),patch.object(vpn_runtime,'healthy',return_value=False):
   result=vpn_runtime.status(self.db);self.assertEqual(result['status'],'disconnected');self.assertFalse(result['app_vpn_active'])
 def test_missing_tunnel_blocks_before_http_health_probe(self):
  with patch.object(vpn_runtime,'required',return_value=True),patch.object(vpn_runtime.Path,'read_text',side_effect=FileNotFoundError),patch.object(vpn_runtime.urllib.request,'build_opener') as opener:
   self.assertFalse(vpn_runtime.healthy());opener.assert_not_called()
 def test_provider_validation_makes_no_requests_before_vpn_or_during_outage(self):
  import core,setup_app
  client=setup_app.app.test_client()
  for protected in (False,True):
   with self.subTest(protected=protected),patch.multiple(core,DB_PATH=self.db,DATA_DIR=self.root),patch.object(setup_app.setup_wizard,'load_state',return_value={'vpn':{'requested':True}}),patch.object(vpn_runtime,'required',return_value=protected),patch.object(vpn_runtime,'healthy',return_value=False),patch.object(core,'detect_provider_source') as detect:
    response=client.post('/api/setup/provider',json={'url':'http://provider.example','username':'test','password':'test'})
    self.assertEqual(response.status_code,409);detect.assert_not_called()
 def test_setup_phase_queue_is_explicit(self):
  profile=self.passed();queued=vpn_runtime.queue(self.db,self.root,profile['profile_id'],phase='setup');self.assertEqual(queued['phase'],'setup')
 def test_completed_upgrade_does_not_load_main_app_without_vpn(self):
  import core,setup_runtime
  from werkzeug.test import Client
  from werkzeug.wrappers import Response
  with patch.multiple(core,DB_PATH=self.db,DATA_DIR=self.root),patch.object(setup_runtime.setup_wizard,'load_state',return_value={'completed':True,'vpn':{'requested':True}}),patch.object(vpn_runtime,'required',return_value=False),patch.object(setup_runtime,'_main_app') as main:
   response=Client(setup_runtime.application,Response).get('/api/providers')
   self.assertEqual(response.status_code,503);main.assert_not_called()
 def test_checking_vpn_blocks_provider_even_before_a_file_is_uploaded(self):
  import core,setup_app,os
  with patch.multiple(core,DB_PATH=self.db,DATA_DIR=self.root),patch.dict(os.environ,{'M3U_SETUP_STATE_PATH':str(self.root/'setup-state.json')}),patch.object(vpn_runtime,'required',return_value=False),patch.object(vpn_runtime,'healthy',return_value=False),patch.object(core,'detect_provider_source') as detect:
   client=setup_app.app.test_client()
   response=client.post('/api/setup/vpn-intent',json={'enabled':True},headers={'X-VPN-Session':'a'*32})
   self.assertEqual(response.status_code,200);self.assertTrue(vpn_runtime.read(self.db)['requested'])
   response=client.post('/api/setup/provider',json={'url':'https://provider.example','username':'fixture','password':'fixture'})
   self.assertEqual(response.status_code,409);detect.assert_not_called()
