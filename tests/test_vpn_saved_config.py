import json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import vpn_config,vpn_runtime,vpn_testing
from tests.test_vpn_config import PROFILE,PRIVATE

class VerifiedConfigurationTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.db=self.root/'db'
  patcher=patch.object(vpn_config,'RAM_ROOT',self.root/'ram');patcher.start();self.addCleanup(patcher.stop)
  profile=vpn_config.save(self.db,self.root,{'provider':'protonvpn','lan_subnets':['10.0.0.0/24'],'wireguard_config':PROFILE})
  self.profile=profile['profile_id'];vpn_testing.create(self.db,self.root);self.job=vpn_testing.claim(self.db)['id']
  self.report={'passed':True,'checks':{key:True for key in ['tunnel_healthy','egress_changed','dns_resolved','internet_blocked_when_vpn_down','tunnel_recovers','lan_reachable']}}
 def payload(self,**changes):
  return {'id':self.job,'profile_id':self.profile,'volume':'m3u-picker-vpn-config-'+self.job,'image':'qmcgaw/gluetun@sha256:'+'a'*64,'results':self.report,**changes}
 def test_verified_profile_is_saved_and_activation_is_queued_without_claiming_connection(self):
  state=vpn_config.retain_verified_profile(self.db,self.root,self.payload())
  self.assertTrue(state['configuration_saved']);self.assertTrue(state['vpn_runtime']['configured'])
  self.assertFalse(state['vpn_runtime']['app_vpn_active']);self.assertEqual(state['activation'],'switching')
  self.assertTrue(vpn_runtime.protection_missing(self.db));self.assertEqual(vpn_runtime.read(self.db)['status'],'pending')
  result=vpn_testing.latest(self.db,self.root)['test'];self.assertEqual(result['status'],'passed');self.assertTrue(result['results']['configuration_saved'])
  self.assertNotIn(PRIVATE,json.dumps(state));self.assertFalse(state['vpn_runtime']['memory_only'])
 def test_saved_profile_survives_loss_of_temporary_upload_and_remains_current(self):
  vpn_config.retain_verified_profile(self.db,self.root,self.payload())
  (vpn_config.profile_directory(self.db)/(self.profile+'.conf')).unlink()
  state=vpn_config.status(self.db,self.root,'new-page-session')
  self.assertTrue(state['configuration_present']);self.assertFalse(state['temporary_upload']);self.assertEqual(state['profile_id'],self.profile)
  self.assertEqual(state['provider'],'protonvpn');self.assertEqual(state['lan_subnets'],['10.0.0.0/24'])
  self.assertFalse(vpn_testing.latest(self.db,self.root)['test']['profile_changed'])
 def test_unverified_or_changed_upload_does_not_replace_saved_configuration(self):
  bad={'passed':True,'checks':{'tunnel_healthy':True}}
  with self.assertRaises(ValueError):vpn_config.retain_verified_profile(self.db,self.root,self.payload(results=bad))
  self.assertFalse(vpn_runtime.read(self.db).get('profile_saved'))
  vpn_config.save(self.db,self.root,{'provider':'protonvpn','lan_subnets':['10.0.0.0/24'],'wireguard_config':PROFILE})
  with self.assertRaises(ValueError):vpn_config.retain_verified_profile(self.db,self.root,self.payload())
 def test_retain_existing_passing_test_without_activation_for_preparation(self):
  vpn_testing.update(self.db,self.job,'passed','Finished',self.report)
  state=vpn_config.retain_verified_profile(self.db,self.root,self.payload(activate=False))
  self.assertTrue(state['configuration_saved']);self.assertFalse(vpn_runtime.read(self.db).get('requested'))
  self.assertFalse(vpn_runtime.protection_missing(self.db))
 def test_unowned_volume_cannot_be_saved(self):
  with self.assertRaises(ValueError):vpn_config.retain_verified_profile(self.db,self.root,self.payload(volume='another-instance-secret'))

class ProductionManifestTests(unittest.TestCase):
 def test_9999_graph_preserves_exact_ports_mounts_and_gpu_and_is_separate(self):
  import sys
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
  import vpn_deployment
  info={'Name':'/m3u-picker','Config':{'Labels':{'com.docker.compose.project':'m3u-picker','com.docker.compose.service':'m3u-picker'},'Env':['M3U_DATA_DIR=/app/data'],'Entrypoint':None,'Cmd':['waitress-serve','src.app:app']},
        'HostConfig':{'PortBindings':{'9999/tcp':[{'HostIp':'127.0.0.1','HostPort':'9999'}]},'DeviceRequests':[{'Driver':'nvidia'}]},
        'Mounts':[{'Type':'volume','Name':'production-data','Destination':'/app/data','RW':True}]}
  config=vpn_deployment.startup_manifest(info,'m3u-web-picker:current','production-glue','private-volume','qmcgaw/gluetun@sha256:'+'a'*64,['10.0.0.0/24'])
  self.assertEqual(config['services']['gluetun']['ports'],['127.0.0.1:9999:9999'])
  self.assertEqual(config['services']['gluetun']['environment']['FIREWALL_INPUT_PORTS'],'9999')
  self.assertEqual(config['volumes']['app-volume-0']['name'],'production-data')
  self.assertIn('deploy',config['services']['m3u-picker']);self.assertNotIn('9998',json.dumps(config))
  off=vpn_deployment.connection_mode(config,False)
  self.assertEqual(off['services']['m3u-picker']['ports'],['127.0.0.1:9999:9999'])
  self.assertEqual(vpn_deployment.connection_mode(off,True),config)
 def test_host_target_and_manifest_switch_only_with_explicit_selected_port(self):
  import sys
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
  import vpn_startup_host as host
  with patch.multiple(host,PORT=host.PORT,TARGET=host.TARGET,DATA=host.DATA,DB=host.DB):
   host.configure_target(9999)
   self.assertIn('vpn-9999',str(host.manifest_path()))
   self.assertEqual(host.DB,'/app/data/m3u_picker.db')
   valid={'Name':'/m3u-picker','Config':{'Env':['M3U_DATA_DIR=/app/data']}}
   host.validate_target(valid)
   with self.assertRaises(ValueError):host.validate_target({'Name':'/m3u-picker-setup','Config':{'Env':['M3U_DATA_DIR=/app/setup-data','M3U_PORT=9998']}})
