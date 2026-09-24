import unittest
from unittest.mock import patch
from contextlib import ExitStack
from flask import Flask
from api.outputs import register_output_routes
import core

class PlaylistAvailabilityTests(unittest.TestCase):
    def test_both_exports_hide_offline_keep_numbers_and_restore_recovery(self):
        manual=[{'name':name,'key':'manual:'+name,'url':name,'raw':['#EXTINF:-1,'+name,name]} for name in ['online','offline','pending']]
        generated=[{'assigned_number':1000+i,'url':name,'raw':['#EXTINF:-1 tvg-chno="'+str(1000+i)+'",'+name,name]} for i,name in enumerate(['sports-online','sports-offline','sports-pending'])]
        states={'online':(True,'working-backup'),'offline':(False,''),'pending':(None,'pending'),'sports-online':(True,'sports-backup'),'sports-offline':(False,''),'sports-pending':(None,'sports-pending')}
        app=Flask(__name__); register_output_routes(app)
        with ExitStack() as stack:
            for name,value in [('saved_manual_guide_channels',manual),('manual_fallback_channel_sets',[]),('active_primary_channels',manual),('manual_channel_catalog',manual),('active_public_epg_paths',[])]:
                stack.enter_context(patch.object(core,name,return_value=value))
            stack.enter_context(patch('api.outputs.sports.generated_rows',return_value=generated))
            stack.enter_context(patch.object(core,'candidate_urls',side_effect=lambda row,*args:[row['url']]))
            stack.enter_context(patch.object(core,'enabled_sports_candidates',side_effect=lambda row,**kwargs:[row['url']]))
            stack.enter_context(patch.object(core.availability,'lookup',side_effect=lambda urls:states[urls[0]]))
            stack.enter_context(patch('api.outputs.media_pipeline.settings',return_value={'enabled':True}))
            with app.test_client() as client:
                for path in ['/playlist/channels.m3u','/playlist/custom.m3u','/playlist/channels.direct.m3u']:
                    response=client.get(path)
                    text=response.get_data(as_text=True)
                    self.assertEqual(response.status_code,200)
                    self.assertNotIn(',offline\n',text)
                    self.assertNotIn(',sports-offline\n',text)
                    self.assertIn(',pending\n',text)
                    self.assertIn('tvg-chno="3"',text)
                    self.assertIn(',sports-pending\n',text)
                    if '.direct.' in path:
                        self.assertIn('working-backup\n',text)
                        self.assertIn('sports-backup\n',text)
                    else:
                        self.assertIn('/stream/channel/manual/online/mpegts',text)
                states['offline']=(True,'recovered')
                self.assertIn(',offline\n',client.get('/playlist/channels.direct.m3u').get_data(as_text=True))
                self.assertEqual(len(manual),3)

if __name__=='__main__': unittest.main()
