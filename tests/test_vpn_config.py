import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from flask import Flask
import core
import vpn_config
import vpn_runtime
from api.vpn import register_vpn_routes

PRIVATE=base64.b64encode(b'A'*32).decode();PUBLIC=base64.b64encode(b'B'*32).decode()
PROFILE=f'''[Interface]
PrivateKey = {PRIVATE}
Address = 10.64.0.2/32, fd00::2/128
DNS = 10.64.0.1
[Peer]
PublicKey = {PUBLIC}
Endpoint = 8.8.8.8:51820
AllowedIPs = 0.0.0.0/0, ::/0
'''
class VpnConfigTests(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.db=self.root/'test.db';self.ram=TemporaryDirectory();self.addCleanup(self.ram.cleanup);self.ram_patch=patch.object(vpn_config,'RAM_ROOT',Path(self.ram.name));self.ram_patch.start();self.addCleanup(self.ram_patch.stop)
 def save(self,**updates):
  return vpn_config.save(self.db,self.root,{'provider':'mullvad','lan_subnets':['10.0.0.1/24'],'wireguard_config':PROFILE,**updates})
 def test_saved_metadata_and_api_never_contain_keys(self):
  result=self.save();self.assertNotIn(PRIVATE,json.dumps(result));self.assertNotIn(PUBLIC,json.dumps(result));self.assertEqual(result['activation'],'not_applied')
  with vpn_config.closing(vpn_config.connect(self.db)) as conn:
   text=conn.execute('SELECT settings_json FROM vpn_settings').fetchone()[0]
   self.assertNotIn(PRIVATE,text);self.assertNotIn(PUBLIC,text)
  self.assertEqual(result['lan_subnets'],['10.0.0.0/24'])
  self.assertFalse((self.root/'vpn').exists());secret=vpn_config.profile_directory(self.db)/(result['profile_id']+'.conf');self.assertIn(PRIVATE,secret.read_text())
  self.assertNotIn('DNS',secret.read_text());self.assertNotIn('fd00',secret.read_text())
 def test_new_upload_requires_secret_and_replaces_previous_memory_copy(self):
  result=self.save()
  with self.assertRaises(ValueError):vpn_config.save(self.db,self.root,{'provider':'custom','lan_subnets':['192.168.1.0/24']})
  updated=self.save();self.assertNotEqual(result['profile_id'],updated['profile_id'])
  self.assertFalse((vpn_config.profile_directory(self.db)/(result['profile_id']+'.conf')).exists())
 def test_session_scope_clear_and_expiry(self):
  result=self.save()
  self.assertFalse(vpn_config.status(self.db,self.root,'other')['configuration_present'])
  vpn_config.discard(self.db,self.root,'other');self.assertTrue(vpn_config.status(self.db,self.root,'internal')['configuration_present'])
  vpn_config.discard(self.db,self.root,'internal');self.assertFalse(vpn_config.status(self.db,self.root)['configuration_present'])
  self.assertFalse((vpn_config.profile_directory(self.db)/(result['profile_id']+'.conf')).exists())
  result=self.save()
  with patch('vpn_config.time.time',return_value=result['expires_at']+1):self.assertFalse(vpn_config.status(self.db,self.root)['configuration_present'])
  self.assertFalse((vpn_config.profile_directory(self.db)/(result['profile_id']+'.conf')).exists())
 def test_rejects_broad_public_ipv6_or_missing_lan_exceptions(self):
  for subnets in [[],['0.0.0.0/0'],['8.8.8.0/24'],['::/0']]:
   with self.subTest(subnets=subnets),self.assertRaises(ValueError):self.save(lan_subnets=subnets)
 def test_rejects_tunnel_lan_overlap(self):
  with self.assertRaisesRegex(ValueError,'overlap'):self.save(lan_subnets=['10.0.0.0/8'])
 def test_rejects_hooks_and_multiple_peers(self):
  for profile in [PROFILE.replace('DNS =','PostUp = echo bad\nDNS ='),PROFILE+'\n[Peer]\nPublicKey = '+PUBLIC]:
   with self.subTest(profile=profile),self.assertRaises(ValueError):self.save(wireguard_config=profile)
 def test_rejects_invalid_keys_partial_routes_and_private_endpoints(self):
  for profile in [PROFILE.replace(PRIVATE,'bad'),PROFILE.replace('0.0.0.0/0','10.0.0.0/8'),PROFILE.replace('8.8.8.8','10.0.0.3')]:
   with self.subTest(profile=profile),self.assertRaises(ValueError):self.save(wireguard_config=profile)
 def test_invalid_save_does_not_replace_existing_profile(self):
  before=self.save()
  with self.assertRaises(ValueError):self.save(wireguard_config='bad')
  self.assertEqual(vpn_config.status(self.db,self.root)['profile_id'],before['profile_id'])
 def test_status_has_no_config_by_default(self):
  self.assertFalse(vpn_config.status(self.db,self.root)['configuration_present'])
 def test_first_settings_opt_in_without_activation_returns_json_and_uploads(self):
  app=Flask(__name__);register_vpn_routes(app)
  with patch.multiple(core,DB_PATH=self.db,DATA_DIR=self.root),patch.object(vpn_runtime,'required',return_value=False):
   client=app.test_client();client.environ_base['HTTP_X_VPN_SESSION']='a'*32
   response=client.post('/api/vpn-preference',json={'enabled':True})
   self.assertEqual(response.status_code,200);self.assertEqual(response.json['status'],'not_applied')
   for endpoint in ['/api/vpn-config','/api/vpn-test']:
    response=client.get(endpoint)
    self.assertEqual(response.status_code,200);self.assertTrue(response.is_json)
   response=client.patch('/api/vpn-config',json={'provider':'protonvpn','lan_subnets':['10.0.0.0/24'],'wireguard_config':PROFILE})
   self.assertEqual(response.status_code,200);self.assertTrue(response.json['configuration_present'])
   self.assertEqual(response.json['activation'],'not_applied');self.assertNotIn(PRIVATE,response.text)
   response=client.post('/api/vpn-test')
   self.assertEqual(response.status_code,202);self.assertTrue(response.is_json)
   self.assertEqual(response.json['test']['status'],'pending')
 def test_status_defaults_for_existing_preference_only_row_preserve_fields(self):
  vpn_runtime.write(self.db,feature_enabled=True)
  self.assertEqual(vpn_config.status(self.db,self.root)['activation'],'not_applied')
  self.assertTrue(vpn_runtime.status(self.db)['enabled'])
  vpn_runtime.write(self.db,status='failed',error='Previous activation failed')
  self.assertEqual(vpn_runtime.status(self.db)['status'],'failed')
 def test_provider_resource_catalog_matches_supported_options(self):
  resources=json.loads((Path(__file__).resolve().parents[1]/'static/vpn-provider-links.json').read_text())['providers']
  self.assertEqual(set(resources),set(vpn_config.PROVIDERS))
  from urllib.parse import urlsplit,parse_qs
  for provider,info in resources.items():
   self.assertTrue(info['website']);self.assertTrue(info['guide'])
   if provider!='custom':self.assertTrue(info['pricing'])
   for field in ['website','guide','pricing']:
    if not info.get(field):continue
    link=urlsplit(info[field]);self.assertEqual(link.scheme,'https');self.assertFalse(link.username);self.assertFalse(link.password)
    self.assertFalse(set(parse_qs(link.query))&{'affid','affiliate','aff_id','coupon'})
 def test_suggests_home_subnet_from_configured_host(self):
  self.assertEqual(vpn_config.suggested_lan_subnet('10.0.0.18'),'10.0.0.0/24')
  self.assertEqual(vpn_config.suggested_lan_subnet('192.168.50.7'),'192.168.50.0/24')
  for host in ['', 'localhost','127.0.0.1','100.80.123.46','8.8.8.8','::1']:
   self.assertEqual(vpn_config.suggested_lan_subnet(host),'')
  with patch.dict('os.environ',{'M3U_LAN_HOST':'10.0.0.18'}):
   before=vpn_config.status(self.db,self.root);self.assertEqual(before['suggested_lan_subnet'],'10.0.0.0/24')
   saved=self.save(lan_subnets=['192.168.50.0/24'])
   self.assertEqual(saved['lan_subnets'],['192.168.50.0/24'])
 def test_actual_mask_takes_priority_over_home_estimate(self):
  with patch.dict('os.environ',{'M3U_LAN_HOST':'192.168.50.7','M3U_LAN_SUBNET':'192.168.50.0/23'}):
   result=vpn_config.status(self.db,self.root)
   self.assertEqual(result['suggested_lan_subnet'],'192.168.50.0/23');self.assertEqual(result['lan_subnet_detection'],'detected')
  self.assertEqual(vpn_config.detected_lan_subnet('10.0.0.18','192.168.1.0/24'),'')
 def test_api_validation_secret_redaction_and_no_cache(self):
  app=Flask(__name__);register_vpn_routes(app)
  with patch.multiple(core,DB_PATH=self.db,DATA_DIR=self.root):
   client=app.test_client();client.environ_base['HTTP_X_VPN_SESSION']='a'*32;response=client.patch('/api/vpn-config',json={'provider':'mullvad','lan_subnets':['10.0.0.0/24'],'wireguard_config':PROFILE})
   self.assertEqual(response.status_code,200);self.assertNotIn(PRIVATE,response.get_data(as_text=True));self.assertIn('no-store',response.headers['Cache-Control'])
   self.assertEqual(client.patch('/api/vpn-config',json={'wireguard_config':'bad'}).status_code,400)
   self.assertTrue(client.get('/api/vpn-config').json['configuration_present']);self.assertFalse(client.get('/api/vpn-config',headers={'X-VPN-Session':'b'*32}).json['configuration_present']);self.assertEqual(client.patch('/api/vpn-config',headers={'X-VPN-Session':''},json={}).status_code,400)
 def test_prepare_test_overlay_has_only_9998_and_no_inline_key(self):
  from scripts.prepare_vpn_test import prepare
  source=self.root/'source.conf';source.write_text(PROFILE)
  output=prepare(source,['10.0.0.0/24'],self.root/'output');text=output.read_text()
  self.assertIn('9998:9998',text);self.assertNotIn('9999',text);self.assertNotIn(PRIVATE,text)
  self.assertIn('ports: !reset []',text);self.assertIn('127.0.0.1:9990',text)
  self.assertEqual(json.loads((output.parent/'manifest.json').read_text())['port'],9998)
