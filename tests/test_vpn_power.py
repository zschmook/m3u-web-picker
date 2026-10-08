import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from flask import Flask
import core,vpn_runtime
from api.vpn import register_vpn_routes


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'db'
        app=Flask(__name__);register_vpn_routes(app);self.client=app.test_client()
        mock=patch.object(core,'DB_PATH',self.db);mock.start();self.addCleanup(mock.stop)
        vpn_runtime.write(self.db,requested=True,persistent=True,desired_on=True,control_heartbeat=time.time())

    def test_off_is_persisted_and_requests_are_allowed_only_on_normal_network(self):
        with patch.object(vpn_runtime,'required',return_value=True),patch.object(vpn_runtime,'healthy',return_value=False):
            response=self.client.post('/api/vpn-control',json={'enabled':False})
            self.assertEqual(response.status_code,202);self.assertFalse(response.json['desired_on'])
            self.assertTrue(vpn_runtime.protection_missing(self.db))
        with patch.object(vpn_runtime,'required',return_value=False):
            self.assertFalse(vpn_runtime.protection_missing(self.db,True))
            vpn_runtime.write(self.db,control_status='applied')
            self.assertEqual(self.client.get('/api/vpn-state').json['status'],'off')
        self.assertFalse(vpn_runtime.read(self.db)['desired_on'])

    def test_enabled_unapplied_or_disconnected_blocks_provider_requests(self):
        with patch.object(vpn_runtime,'required',return_value=False):
            self.assertTrue(vpn_runtime.protection_missing(self.db))
        with patch.object(vpn_runtime,'required',return_value=True),patch.object(vpn_runtime,'healthy',return_value=False):
            self.assertEqual(self.client.get('/api/vpn-state').json['status'],'disconnected')
            self.assertTrue(vpn_runtime.protection_missing(self.db))

    def test_control_requires_manager_configuration_boolean_and_same_origin(self):
        for value in ('false',None,1):
            self.assertEqual(self.client.post('/api/vpn-control',json={'enabled':value}).status_code,409)
        self.assertEqual(self.client.post('/api/vpn-control',json={'enabled':False},headers={'Origin':'https://another.example'}).status_code,403)
        vpn_runtime.write(self.db,control_heartbeat=0)
        self.assertEqual(self.client.post('/api/vpn-control',json={'enabled':False}).status_code,409)
        vpn_runtime.write(self.db,persistent=False,control_heartbeat=time.time())
        self.assertEqual(self.client.post('/api/vpn-control',json={'enabled':False}).status_code,409)

    def test_duplicate_click_cannot_overwrite_switch_and_claim_is_once(self):
        self.assertEqual(self.client.post('/api/vpn-control',json={'enabled':False}).status_code,202)
        self.assertEqual(self.client.post('/api/vpn-control',json={'enabled':True}).status_code,409)
        self.assertFalse(vpn_runtime.claim_control(self.db)['desired_on'])
        self.assertIsNone(vpn_runtime.claim_control(self.db))

    def test_vpn_outage_page_keeps_power_access_without_starting_scheduler(self):
        import setup_runtime
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        with patch.object(setup_runtime.setup_wizard,'load_state',return_value={'completed':True,'vpn':{'requested':True}}),patch.object(vpn_runtime,'required',return_value=True),patch.object(vpn_runtime,'healthy',return_value=False),patch.object(setup_runtime,'_main_app') as main:
            client=Client(setup_runtime.application,Response)
            response=client.get('/')
            self.assertEqual(response.status_code,200);self.assertIn('uiVpnStatus',response.text)
            self.assertEqual(client.get('/api/providers').status_code,503)
            self.assertEqual(client.post('/api/vpn-control',json={'enabled':False}).status_code,202)
            main.assert_not_called()

    def test_activity_reports_local_registry_even_during_outage(self):
        response=self.client.get('/api/vpn-activity')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['active_streams'],0)

    def test_legacy_applied_setting_is_enabled_even_when_power_is_off(self):
        vpn_runtime.write(self.db,desired_on=False)
        with patch.object(vpn_runtime,'required',return_value=False):
            state=self.client.get('/api/vpn-state').json
        self.assertTrue(state['enabled']);self.assertFalse(state['desired_on'])

    def test_settings_disable_hides_control_stops_vpn_and_retains_credentials(self):
        vpn_runtime.write(self.db,credential_volume='private-config')
        with patch.object(vpn_runtime,'required',return_value=True):
            response=self.client.post('/api/vpn-preference',json={'enabled':False})
        self.assertEqual(response.status_code,202);self.assertFalse(response.json['enabled'])
        self.assertFalse(response.json['desired_on']);self.assertEqual(response.json['control_status'],'pending')
        self.assertEqual(vpn_runtime.read(self.db)['credential_volume'],'private-config')
        self.assertTrue(vpn_runtime.read(self.db)['persistent'])
        vpn_runtime.write(self.db,control_status='applied')
        self.assertEqual(self.client.post('/api/vpn-control',json={'enabled':True}).status_code,409)

    def test_power_off_leaves_use_vpn_checked_and_reload_consistent(self):
        self.client.post('/api/vpn-control',json={'enabled':False})
        vpn_runtime.write(self.db,control_status='applied')
        with patch.object(vpn_runtime,'required',return_value=False):
            state=self.client.get('/api/vpn-config').json['vpn_runtime']
        self.assertTrue(state['enabled']);self.assertFalse(state['desired_on']);self.assertEqual(state['status'],'off')

    def test_enabling_saved_configuration_queues_on_and_blocks_until_connected(self):
        vpn_runtime.write(self.db,feature_enabled=False,desired_on=False)
        with patch.object(vpn_runtime,'required',return_value=False):
            response=self.client.post('/api/vpn-preference',json={'enabled':True})
            self.assertEqual(response.status_code,202);self.assertTrue(response.json['enabled']);self.assertTrue(response.json['desired_on'])
            self.assertTrue(vpn_runtime.protection_missing(self.db))

    def test_duplicate_enabled_preference_does_not_turn_power_back_on(self):
        vpn_runtime.write(self.db,feature_enabled=True,desired_on=False)
        with patch.object(vpn_runtime,'required',return_value=False):
            response=self.client.post('/api/vpn-preference',json={'enabled':True})
        self.assertEqual(response.status_code,200);self.assertFalse(response.json['desired_on'])

    def test_unavailable_manager_does_not_commit_a_disable_that_cannot_apply(self):
        vpn_runtime.write(self.db,control_heartbeat=0)
        with patch.object(vpn_runtime,'required',return_value=True):
            response=self.client.post('/api/vpn-preference',json={'enabled':False})
        self.assertEqual(response.status_code,409);self.assertTrue(vpn_runtime.feature_enabled(vpn_runtime.read(self.db)))

    def test_settings_are_persistent_without_configuration_and_reject_bad_requests(self):
        vpn_runtime.write(self.db,persistent=False,requested=False,desired_on=False)
        with patch.object(vpn_runtime,'required',return_value=False):
            self.assertEqual(self.client.post('/api/vpn-preference',json={'enabled':True}).status_code,200)
            self.assertTrue(self.client.get('/api/vpn-state').json['enabled'])
            self.assertEqual(self.client.post('/api/vpn-preference',json={'enabled':False}).status_code,200)
            self.assertFalse(self.client.get('/api/vpn-state').json['enabled'])
        self.assertEqual(self.client.post('/api/vpn-preference',json={'enabled':'false'}).status_code,409)
        self.assertEqual(self.client.post('/api/vpn-preference',json={'enabled':True},headers={'Origin':'https://other.example'}).status_code,403)
