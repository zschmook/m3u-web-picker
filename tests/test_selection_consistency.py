import os
os.environ['M3U_DISABLE_SCHEDULER'] = 'true'

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask
import core
from api.providers import register_provider_routes
from api.outputs import register_output_routes


class SelectionConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.multiple(core, DB_PATH=root/'test.db', PLAYLIST_PATH=root/'channels.m3u',
                                              channels=[], selected_ids=set(), provider_sources=[]))
        self.stack.enter_context(patch.object(core, 'save_config'))
        self.stack.enter_context(patch.object(core.sports, 'generated_rows', return_value=[]))
        self.stack.enter_context(patch.object(core.sports, 'generated_channel_payloads', return_value=[]))
        self.stack.enter_context(patch.object(core, 'active_public_epg_paths', return_value=[]))
        self.stack.enter_context(patch.object(core.availability, 'lookup', side_effect=lambda urls: (True, urls[0]) if urls else (False, '')))
        self.stack.enter_context(patch('api.outputs.custom_channels.playlist_lines', return_value=[]))
        self.stack.enter_context(patch('api.outputs.movie_channels.playlist_lines', return_value=[]))
        self.app = Flask(__name__)
        register_provider_routes(self.app)
        register_output_routes(self.app)
        self.client = self.app.test_client()
        core.db_connect().close()
        self.catalog(['Alpha', 'Bravo', 'Charlie'])
        core.selected_ids = {core.channels[0]['id']}
        core.save_selected_channels_to_db([core.channels[0]])
        core.write_current_playlist()

    def catalog(self, names):
        lines = ['#EXTM3U']
        for name in names:
            lines.extend([f'#EXTINF:-1 tvg-id="{name.lower()}",{name}', f'http://example.test/{name.lower()}'])
        core.channels = core.parse_m3u_text('\n'.join(lines)+'\n')

    def names(self):
        return [row['name'] for row in core.saved_manual_guide_channels()]

    def request(self, names, revision=None):
        by_name = {row['name']: core.channel_key(row) for row in core.channels}
        return {'keys': [by_name[name] for name in names],
                'selection_revision': revision or core.manual_selection_revision()}

    def test_old_catalog_keys_select_correct_channels_after_provider_reorders_ids(self):
        payload = self.request(['Alpha', 'Charlie'])
        self.catalog(['Bravo', 'Charlie', 'Alpha'])
        core.apply_saved_selections_to_loaded_channels()
        core.write_current_playlist()
        response = self.client.post('/api/selection', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.names(), ['Alpha', 'Charlie'])
        self.assertEqual(response.json['selection_revision'], core.manual_selection_revision())
        for path in ['/playlist/channels.m3u', '/playlist/channels.direct.m3u']:
            text = self.client.get(path).text
            self.assertIn(',Alpha\n', text)
            self.assertIn(',Charlie\n', text)
            self.assertNotIn(',Bravo\n', text)

    def test_stale_tab_cannot_restore_a_channel_removed_by_another_tab(self):
        initial = self.client.get('/api/channels').json
        self.client.post('/api/selection', json=self.request(['Alpha', 'Bravo']))
        old_payload = self.request(['Alpha', 'Charlie'])
        self.client.post('/api/selection', json=self.request(['Bravo']))
        before = core.PLAYLIST_PATH.read_bytes()
        response = self.client.post('/api/selection', json=old_payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.names(), ['Bravo'])
        self.assertEqual(core.PLAYLIST_PATH.read_bytes(), before)
        self.assertNotEqual(initial['selection_revision'], core.manual_selection_revision())

    def test_old_numeric_only_saves_are_rejected_without_changing_state(self):
        response = self.client.post('/api/selection', json={'ids': [1]})
        self.assertEqual(response.status_code, 409)
        self.assertIn('Reload', response.json['error'])
        self.assertEqual(self.names(), ['Alpha'])

    def test_unknown_stable_key_rejects_the_whole_save(self):
        payload = self.request(['Bravo'])
        payload['keys'].append('manual:missing')
        response = self.client.post('/api/selection', json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.names(), ['Alpha'])

    def test_reading_order_does_not_restore_stale_in_memory_selections(self):
        core.selected_ids.add(core.channels[1]['id'])
        before = core.manual_selection_revision()
        response = self.client.get('/api/selection/order')
        self.assertEqual([row['name'] for row in response.json['channels']], ['Alpha'])
        self.assertEqual(core.manual_selection_revision(), before)
        self.assertEqual(self.names(), ['Alpha'])

    def test_stale_order_dialog_cannot_overwrite_a_newer_order(self):
        self.client.post('/api/selection', json=self.request(['Alpha', 'Bravo', 'Charlie']))
        order = self.client.get('/api/selection/order').json
        keys = [row['key'] for row in order['channels']]
        response = self.client.post('/api/selection/order', json={'keys': keys[::-1], 'selection_revision': order['selection_revision']})
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/selection/order', json={'keys': keys, 'selection_revision': order['selection_revision']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.names(), ['Charlie', 'Bravo', 'Alpha'])

    def test_saving_a_selection_preserves_missing_saved_rows_and_their_order(self):
        self.client.post('/api/selection', json=self.request(['Alpha', 'Bravo']))
        self.catalog(['Bravo', 'Charlie'])
        core.apply_saved_selections_to_loaded_channels()
        response = self.client.post('/api/selection', json=self.request(['Charlie']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.names(), ['Alpha', 'Charlie'])

    def test_channel_and_order_reads_prevent_http_caching(self):
        for path in ['/api/channels', '/api/selection/order']:
            self.assertIn('no-store', self.client.get(path).headers['Cache-Control'])

    def test_export_does_not_restore_stale_server_cache_or_erase_database_selections(self):
        revision = core.manual_selection_revision()
        for cached_ids in [{1, 2}, set()]:
            core.selected_ids = cached_ids
            core.write_current_playlist()
            self.assertEqual(core.manual_selection_revision(), revision)
            self.assertEqual(self.names(), ['Alpha'])
            self.assertIn(',Alpha\n', core.PLAYLIST_PATH.read_text())
            self.assertNotIn(',Bravo\n', core.PLAYLIST_PATH.read_text())

    def test_channel_manager_and_xmltv_selection_use_sqlite_instead_of_server_cache(self):
        core.selected_ids = {1, 2}
        self.assertEqual(self.client.get('/api/channels').json['selected_ids'], [0])
        self.assertEqual([row['name'] for row in core.selected_channels_from_selected_ids_in_order()], ['Alpha'])
        self.assertEqual(core.selected_xmltv_ids(), {'alpha'})

    def test_export_respects_database_order_even_when_server_cache_is_stale(self):
        self.client.post('/api/selection', json=self.request(['Alpha', 'Bravo']))
        keys = [core.channel_key(row) for row in core.channels[:2]]
        core.save_channel_order(keys[::-1])
        core.selected_ids = set()
        revision = core.manual_selection_revision()
        core.write_current_playlist()
        self.assertEqual(core.manual_selection_revision(), revision)
        self.assertEqual(self.names(), ['Bravo', 'Alpha'])
        self.assertEqual(core.PLAYLIST_PATH.read_text().splitlines()[1].rsplit(',', 1)[1], 'Bravo')

    def test_setup_reads_and_commits_stable_keys_to_sqlite(self):
        import setup_app
        client = setup_app.app.test_client()
        snapshot = client.get('/api/setup/channels').json
        self.assertEqual(snapshot['selected_keys'], [core.channel_key(core.channels[0])])
        payload = self.request(['Alpha', 'Charlie'], snapshot['selection_revision'])
        self.catalog(['Bravo', 'Charlie', 'Alpha'])
        with patch('setup_wizard.load_state', return_value={'mode': 'provider'}), \
                patch('setup_wizard.save_state', return_value={}):
            response = client.post('/api/setup/channels', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.names(), ['Alpha', 'Charlie'])
        self.assertEqual(response.json['selected_count'], 2)

    def test_setup_cannot_overwrite_newer_sqlite_selections(self):
        import setup_app
        stale = self.request(['Bravo'])
        self.client.post('/api/selection', json=self.request(['Charlie']))
        response = setup_app.app.test_client().post('/api/setup/channels', json=stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.names(), ['Charlie'])

    def test_public_guide_matchers_follow_database_when_process_cache_is_empty(self):
        core.selected_ids = set()
        wanted_ids, wanted_names = core._public_epg_relevant_matchers()
        self.assertIn('alpha', wanted_ids)
        self.assertIn(core.sports._normalize('Alpha'), wanted_names)

    def test_lineup_revision_tracks_saved_selections_and_generated_sports(self):
        original = core.curated_lineup_revision()
        core.selected_ids = {1, 2}
        self.assertEqual(core.curated_lineup_revision(), original)
        self.client.post('/api/selection', json=self.request(['Bravo']))
        selected = core.curated_lineup_revision()
        self.assertNotEqual(selected, original)
        with patch.object(core.sports, 'generated_rows', return_value=[{
            'tvg_id': 'sports-one', 'assigned_number': 4000, 'display_name': 'Game', 'generated_at': '2026-10-06'}]):
            self.assertNotEqual(core.curated_lineup_revision(), selected)


if __name__ == '__main__':
    unittest.main()
