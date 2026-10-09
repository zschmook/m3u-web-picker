import json
import unittest
from unittest.mock import patch
from scripts import vpn_test_host as helper

class VpnTestHostTargetTests(unittest.TestCase):
 def setUp(self):
  state=patch.multiple(helper,TARGET=helper.TARGET,DB=helper.DB,DATA=helper.DATA,PORT=helper.PORT)
  state.start();self.addCleanup(state.stop)
 def info(self,port):
  data='/app/setup-data' if port==9998 else '/app/data'
  env=['M3U_DATA_DIR='+data]
  if port==9998:env.append('M3U_PORT=9998')
  return {'Name':'/m3u-picker-setup' if port==9998 else '/m3u-picker','Config':{'Env':env},'HostConfig':{'NetworkMode':'bridge','PortBindings':{'9999/tcp':[{'HostPort':'9999'}]} if port==9999 else {}}}
 def test_default_target_stays_setup_and_explicit_production_uses_its_database(self):
  for port in (9998,9999):
   helper.configure_target(port);helper.validate_target(self.info(port))
   with patch.object(helper,'run',return_value='{}') as run:
    helper.remote('heartbeat')
   command=run.call_args.args[0];payload=json.loads(run.call_args.args[1])
   self.assertEqual(command[:4],['docker','exec','-i','m3u-picker-setup' if port==9998 else 'm3u-picker'])
   self.assertEqual(payload['db'],('/app/setup-data' if port==9998 else '/app/data')+'/m3u_picker.db')
   self.assertNotIn('up',command);self.assertNotIn('restart',command)
 def test_mismatched_name_data_port_or_publication_rejected_before_queue(self):
  helper.configure_target(9999)
  wrong=self.info(9998)
  with self.assertRaises(ValueError):helper.validate_target(wrong)
  wrong=self.info(9999);wrong['Config']['Env'].append('M3U_PORT=9998')
  with self.assertRaises(ValueError):helper.validate_target(wrong)
  wrong=self.info(9999);wrong['Config']['Env']=['M3U_DATA_DIR=/app/setup-data']
  with self.assertRaises(ValueError):helper.validate_target(wrong)
  wrong=self.info(9999);wrong['HostConfig']['PortBindings']={}
  with self.assertRaises(ValueError):helper.validate_target(wrong)
  with self.assertRaises(ValueError):helper.configure_target(10000)
