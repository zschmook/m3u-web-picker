import os
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from flask import Flask

import custom_channels as service
from api import custom_channels,guide,outputs


def guide_item():
    return dict(tvg_id='custom-123456789abcdef0',number='9000',name='Show & Friends',
        now=dict(title='S09E03 — Missing two',start='2026-09-24T12:00:00+00:00',stop='2026-09-24T13:00:00+00:00',season=9,episode=3),upcoming=[])


class CustomChannelRevisionTests(unittest.TestCase):
    def test_custom_browser_playback_remuxes_normalized_stream(self):
        app=Flask(__name__);custom_channels.register_custom_channel_routes(app)
        target='http://127.0.0.1:9999/stream/custom/123456789abcdef0.ts'
        with patch.object(custom_channels,'local_url',return_value=target),patch.object(
            custom_channels.browser,'response_for',return_value='browser video'
        ) as play:
            response=app.test_client().get('/guide/play/custom/123456789abcdef0')
            self.assertEqual(response.status_code,200)
            play.assert_called_once_with(target,remux_only=True)

    def test_custom_guide_channels_follow_manual_and_precede_sports(self):
        items=[dict(play_url='/guide/play/local/breaking-bad'),dict(play_url='/guide/play/manual/one'),
               dict(play_url='/guide/play/manual/two'),dict(play_url='/guide/play/sports/2000')]
        self.assertEqual(guide.custom_channel_insert_position(items),3)

    def test_folder_picker_navigation_and_host_path_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/'clips';base.mkdir();(base/'child').mkdir()
            with patch.dict(os.environ,{'M3U_COMMERCIALS_DIR':str(base),'M3U_COMMERCIALS_HOST_DIR':r'C:\Media\Ads','M3U_CUSTOM_MEDIA_DIRS':'[]'}),patch.object(service,'settings',return_value=service.DEFAULTS):
                self.assertEqual(service.resolve_folder(r'c:\media\ads\child'),base/'child')
                self.assertEqual(service.browse_folders()['folders'][0]['label'],r'C:\Media\Ads')
                inside=service.browse_folders(str(base));self.assertEqual(inside['parent'],'');self.assertEqual(inside['folders'][0]['name'],'child')
                with self.assertRaises(ValueError):service.browse_folders(temp)
                with self.assertRaises(ValueError):service.resolve_folder(r'C:\Media\Ads\..\private')

    def test_folder_picker_does_not_follow_symlinks_outside_shared_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/'clips';base.mkdir();outside=Path(temp)/'private';outside.mkdir()
            (base/'escape').symlink_to(outside,target_is_directory=True)
            with patch.dict(os.environ,{'M3U_COMMERCIALS_DIR':str(base),'M3U_CUSTOM_MEDIA_DIRS':'[]'}),patch.object(service,'settings',return_value=service.DEFAULTS):
                self.assertEqual(service.browse_folders(str(base))['folders'],[])
                with self.assertRaises(ValueError):service.browse_folders(str(base/'escape'))

    def test_partial_autosave_preserves_other_settings_and_is_atomic_on_failure(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'root',return_value=Path(temp)):
            service.save_settings({'minimum_episodes':50,'commercial_count':4})
            service.save_settings({'enabled':True})
            self.assertEqual(service.settings()['minimum_episodes'],50)
            with self.assertRaises(ValueError):service.save_settings({'commercial_count':0})
            self.assertTrue(service.settings()['enabled'])
            self.assertEqual(service.settings()['commercial_count'],4)

    def test_preroll_can_be_changed_when_saved_commercial_folder_is_temporarily_unavailable(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'root',return_value=Path(temp)):
            service.save_schedule(service.root()/'settings.json',{
                **service.DEFAULTS,'commercials_enabled':True,
                'commercials_folder':str(Path(temp)/'missing'),'preroll':True,
            })
            saved=service.save_settings({'preroll':False})
            self.assertFalse(saved['preroll'])
            self.assertTrue(saved['commercials_enabled'])

    def test_commercial_policy_changes_rebuild_existing_channel_schedules(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'root',return_value=Path(temp)/'data'),patch.object(service,'commercials_root',return_value=Path(temp)/'media'):
            episode=dict(season=1,episode=1,filename='show.mp4',title='Show',duration=3600,part='/show.mp4',folder='/')
            old_options=dict(enabled=False,mode='minutes',minutes=15,episodes=1,count=3,preroll=True)
            state=service.new_schedule([episode],[],1000,'old-seed',order='ordered',ads=old_options)
            row=dict(id='show-channel',show_id='show',name='Show',number='1000',order='ordered',enabled=True,auto_numbered=True,started_at=1000)
            service.save_schedule(service.root()/'channels.json',[row]);service.save_schedule(service.root()/'show-channel.json',state)
            commercial=dict(filename='ad.mp4',path='/ad.mp4',duration=30)
            with patch.object(service,'commercial_assets',return_value=[commercial]),patch.object(service.time,'time',return_value=2000):
                service.save_settings({'commercials_enabled':True})
            rebuilt=service.read('show-channel.json',{})
            self.assertTrue(rebuilt['ads']['enabled'])
            self.assertEqual(rebuilt['commercials'],[commercial])
            self.assertIn('commercial',{segment['kind'] for segment in rebuilt['segments']})
            self.assertEqual(service.read('channels.json',[])[0]['started_at'],2000)

    def test_completed_import_starts_processing_immediately(self):
        app=Flask(__name__);custom_channels.register_custom_channel_routes(app)
        with tempfile.TemporaryDirectory() as temp,patch.object(service,'root',return_value=Path(temp)/'data'),patch.object(service,'commercials_root',return_value=Path(temp)/'media'),patch.object(service,'start_commercial_processing') as start:
            client=app.test_client();created=client.post('/api/custom-channels/commercial-imports',json={'name':'retro.mp4','size':3})
            self.assertEqual(created.status_code,201);identity=created.get_json()['import_']['id']
            copied=client.put('/api/custom-channels/commercial-imports/'+identity,data=b'abc',headers={'Upload-Offset':'0','Content-Type':'application/octet-stream'})
            self.assertEqual(copied.status_code,200);start.assert_called_once_with(identity)

    def test_existing_playlist_and_epg_routes_include_custom_channels(self):
        app=Flask(__name__)
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/'epg.xml';original=b'<tv><channel id="provider"/><programme channel="provider" start="20260924120000 +0000" stop="20260924130000 +0000"><title>Original</title></programme></tv>';base.write_bytes(original)
            with ExitStack() as stack:
                stack.enter_context(patch.object(outputs.core,'DATA_DIR',Path(temp)))
                outputs.register_output_routes(app)
                for name in ['saved_manual_guide_channels','manual_fallback_channel_sets','active_primary_channels','manual_channel_catalog','active_public_epg_paths']:
                    stack.enter_context(patch.object(outputs.core,name,return_value=[]))
                stack.enter_context(patch.object(outputs.sports,'generated_rows',return_value=[]))
                stack.enter_context(patch.object(outputs.core,'ensure_epg_exports_current'))
                stack.enter_context(patch.object(outputs.core,'COMBINED_EPG_PATH',base))
                stack.enter_context(patch.object(outputs.media_pipeline,'settings',return_value={'enabled':False}))
                items=stack.enter_context(patch.object(custom_channels,'guide_items',return_value=[guide_item()]))
                client=app.test_client()
                for url in ['/playlist/channels.m3u','/playlist/channels.direct.m3u','/playlist/custom.m3u']:
                    response=client.get(url);self.assertEqual(response.status_code,200)
                    self.assertIn('tvg-id="custom-123456789abcdef0"',response.text)
                    self.assertIn('/stream/custom/123456789abcdef0.ts',response.text)
                    self.assertIn('/epg/epg.xml',response.text)
                for url in ['/epg/epg.xml','/epg/combined.xml']:
                    response=client.get(url);self.assertEqual(response.status_code,200)
                    xml=ET.fromstring(response.data)
                    self.assertEqual(len(xml.findall('channel')),2)
                    self.assertEqual(len(xml.findall('programme')),2)
                    self.assertEqual(xml.find("programme[@channel='custom-123456789abcdef0']/episode-num").text,'8.2.')
                    self.assertEqual(base.read_bytes(),original)
                items.return_value=[]
                self.assertNotIn('custom-123456789abcdef0',client.get('/playlist/channels.m3u').text)
                self.assertEqual(client.get('/epg/epg.xml').data,original)

    def test_merge_does_not_duplicate_entries_and_preserves_xmltv_order(self):
        root=ET.fromstring('<tv><channel id="p"/><programme channel="p"/></tv>')
        custom_channels.merge_epg(root,[guide_item()]);custom_channels.merge_epg(root,[guide_item()])
        self.assertEqual([e.tag for e in root],['channel','channel','programme','programme'])


if __name__=='__main__':unittest.main()
