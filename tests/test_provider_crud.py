import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import core

class ProviderCrudTests(unittest.TestCase):
    def test_disable_edit_enable_delete_preserve_saved_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            channel=core.parse_m3u_text('#EXTM3U\n#EXTINF:-1 tvg-id="one",One\nhttp://example.test/one\n')[0]
            source={'id':'primary','role':'primary','name':'Original','url':'http://example.test','username':'user','password':'pass','kind':'xtream'}
            with patch.multiple(core, DB_PATH=root/'db',PLAYLIST_PATH=root/'playlist',MASTER_CACHE_PATH=root/'master',EPG_CACHE_PATH=root/'epg',channels=[channel],selected_ids={channel['id']},provider_sources=[source],source_mode='url'), patch('core.save_config'), patch('core.sports.generated_rows',return_value=[]), patch('core.ensure_epg_exports_current'):
                core.db_connect().close()
                core.write_current_playlist()
                keys=core.load_selected_keys_from_db()
                core.update_provider('primary',{'enabled':False,'name':'Renamed','password':''})
                self.assertEqual(core.manual_channel_catalog(),[])
                self.assertEqual(source['password'],'pass')
                self.assertEqual(core.load_selected_keys_from_db(),keys)
                with patch('core.load_provider_playlist') as load:
                    core.refresh_provider_source(source)
                    load.assert_not_called()
                self.assertEqual(core.enabled_sports_candidates({'url':channel['url']}),[])
                core.update_provider('primary',{'enabled':True})
                self.assertEqual(core.selected_ids,{channel['id']})
                with self.assertRaises(ValueError): core.update_provider('primary',{'url':'file:///bad'})
                self.assertEqual(source['url'],'http://example.test')
                self.assertTrue(core.remove_primary_source())
                self.assertEqual(core.load_selected_keys_from_db(),keys)
                self.assertEqual(core.channels,[])

if __name__=='__main__': unittest.main()
