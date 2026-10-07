import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core

class FallbackCatalogTests(unittest.TestCase):
    def test_sources_have_stable_distinct_ids_and_persist_selections(self):
        with tempfile.TemporaryDirectory() as temp:
            primary={'id':0,'name':'News','url':'https://example.test/news','tvg_id':'news','group':'News','raw':['#EXTINF:-1,News','https://example.test/news']}
            sources=[{'id':'a','name':'Fallback A','role':'fallback'},{'id':'b','name':'Fallback B','role':'fallback'}]
            with patch.multiple(core, DB_PATH=Path(temp)/'test.db',PLAYLIST_PATH=Path(temp)/'custom.m3u',channels=[primary],provider_sources=sources,selected_ids=set()), patch('core._load_provider_cache',return_value=[primary]), patch('core.sports.generated_rows',return_value=[]), patch('core.sports.generated_channel_payloads',return_value=[]):
                core.db_connect().close()
                rows=core.combined_channels_for_api()
                self.assertEqual(len(rows),3)
                self.assertEqual(len({r['id'] for r in rows}),3)
                self.assertTrue(all(r['id'] < 2**53 for r in rows))
                self.assertEqual(rows[1]['provider_source_name'],'Fallback A')
                core.selected_ids={rows[0]['id'],rows[1]['id']}
                core.save_selected_channels_to_db([rows[0], rows[1]])
                core.write_current_playlist()
                keys=core.load_selected_keys_from_db()
                self.assertEqual(len(keys),2)
                # Restart and source reorder must not migrate the fallback to primary.
                core.selected_ids=set()
                core.provider_sources.reverse()
                core.apply_saved_selections_to_loaded_channels()
                self.assertEqual(core.selected_ids,{rows[0]['id'],rows[1]['id']})
                self.assertEqual(core.load_selected_keys_from_db(),keys)
                # Missing fallback survives an update and reappears checked on recovery.
                with patch('core._load_provider_cache',return_value=[]):
                    core.apply_saved_selections_to_loaded_channels()
                    core.write_current_playlist()
                    self.assertEqual(core.load_selected_keys_from_db(),keys)
                core.apply_saved_selections_to_loaded_channels()
                self.assertIn(rows[1]['id'],core.selected_ids)
                core.selected_ids={rows[0]['id']}
                core.save_selected_channels_to_db([rows[0]], preserve_missing=True)
                core.write_current_playlist()
                self.assertEqual(len(core.load_selected_keys_from_db()),1)

if __name__=='__main__': unittest.main()
