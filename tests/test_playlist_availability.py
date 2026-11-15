import unittest
from unittest.mock import patch
from contextlib import ExitStack
from flask import Flask
from types import SimpleNamespace
from api.outputs import register_output_routes
from media import upstream_relay
import network_config
import core

class PlaylistAvailabilityTests(unittest.TestCase):
    def test_local_download_advertises_lan_urls_with_and_without_vpn(self):
        provider='http://provider.example.test/live/private/channel.ts'
        manual=[{'name':'Alpha','key':'manual:alpha','url':provider,'raw':['#EXTINF:-1,Alpha',provider]}]
        app=Flask(__name__);register_output_routes(app);upstream_relay.register_routes(app)
        with ExitStack() as stack:
            for name,value in [('saved_manual_guide_channels',manual),('manual_fallback_channel_sets',[]),('active_primary_channels',manual),('manual_channel_catalog',manual),('active_public_epg_paths',[])]:
                stack.enter_context(patch.object(core,name,return_value=value))
            stack.enter_context(patch.object(core,'candidate_urls',return_value=[provider]))
            stack.enter_context(patch.object(core.availability,'lookup',return_value=(True,provider)))
            stack.enter_context(patch('api.outputs.sports.generated_rows',return_value=[]))
            stack.enter_context(patch('api.outputs.custom_channels.playlist_lines',return_value=[]))
            stack.enter_context(patch('api.outputs.movie_channels.playlist_lines',return_value=[]))
            stack.enter_context(patch('api.outputs.director.inject_channel',side_effect=lambda text,origin:text))
            stack.enter_context(patch('api.outputs.media_pipeline.settings',return_value={'enabled':True}))
            stack.enter_context(patch.object(network_config,'load_settings',return_value=SimpleNamespace(lan_host='10.0.0.18',external_port=9999)))
            client=app.test_client()
            for vpn in (False,True):
                with self.subTest(vpn=vpn),patch.object(upstream_relay.vpn_runtime,'required',return_value=vpn):
                    for path in ('/playlist/channels.m3u','/playlist/custom.m3u','/playlist/channels.direct.m3u'):
                        response=client.get(path,base_url='http://localhost:9998')
                        self.assertEqual(response.status_code,200)
                        self.assertIn('url-tvg="http://10.0.0.18:9999/epg/epg.xml"',response.text)
                        self.assertNotIn('localhost',response.text)
                        if '.direct.' not in path:
                            self.assertIn('http://10.0.0.18:9999/stream/channel/manual/alpha/mpegts',response.text)
                            self.assertNotIn('/stream/relay/',response.text)
                        elif vpn:
                            self.assertNotIn(provider,response.text)
                            self.assertIn('http://10.0.0.18:9999/stream/relay/',response.text)
                        else:self.assertIn(provider,response.text)

    def test_exports_keep_saved_manual_channels_when_probe_fails(self):
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
                    self.assertIn(',offline\n',text)
                    self.assertIn('tvg-chno="2"',text)
                    self.assertIn(',sports-offline\n',text)
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
