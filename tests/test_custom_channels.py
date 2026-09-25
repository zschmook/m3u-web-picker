import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import custom_channels as cc
from media.scheduled_channel import new_schedule,extend_schedule,segments_at
from api.episode_test import segment_command


def episodes():
    return [dict(season=s,episode=e,filename=f'S{s}E{e}.mkv',title=f'S{s}E{e}',duration=600,
                 part=f'/library/parts/{s}{e}/file.mkv',folder='/tv/show',covered_episodes=[e])
            for s,e in [(9,1),(9,3),(11,2),(22,1),(23,1)]]


class CustomChannelsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.mock=patch.object(cc,'root',return_value=Path(self.temp.name));self.mock.start();self.addCleanup(self.mock.stop)
        cc.JOB.update(running=False,message='')

    def test_disabled_refresh_never_contacts_plex(self):
        with patch.object(cc,'plex_xml') as get,patch.object(cc,'discover_servers') as discover:
            self.assertEqual(cc.refresh(True)['status'],'disabled');get.assert_not_called();discover.assert_not_called()

    def test_date_based_episode_numbers_do_not_expand_millions_of_gaps(self):
        ep=dict(episodes()[0],episode=20260924,covered_episodes=[20260924])
        value=cc.summary(dict(episodes=[ep]))
        self.assertEqual(value['count'],1)
        self.assertLess(len(value['gaps'][0]),100)

    def test_ordered_range_skips_missing_seasons_and_episodes_and_loops(self):
        selected=cc.selected_episodes({'episodes':episodes()},9,22)
        state=new_schedule(selected,[],100,'seed',order='ordered',ads={'enabled':False})
        extend_schedule(state,5100)
        self.assertEqual([(p['season'],p['episode']) for p in state['programmes'][:8]],[(9,1),(9,3),(11,2),(22,1)]*2)
        self.assertTrue(all(s['kind']=='episode' for s in state['segments']))
        self.assertEqual(next(segments_at(state,110))['start'],10)

    def test_random_exhausts_range_before_repeat(self):
        state=new_schedule(episodes(),[],0,'seed',ads={'enabled':False})
        extend_schedule(state,6200)
        self.assertEqual(len({p['filename'] for p in state['programmes'][:5]}),5)

    def test_episode_interval_with_ads_and_no_preroll(self):
        ads=[dict(path='/ads/a.mp4',filename='a.mp4',duration=15)]
        state=new_schedule(episodes(),ads,0,'seed',order='ordered',ads=dict(enabled=True,mode='episodes',episodes=2,count=3,preroll=False))
        extend_schedule(state,1500)
        self.assertEqual([s['kind'] for s in state['segments']],['episode','episode','commercial','commercial','commercial','episode'])

    def test_commercial_library_is_managed_inside_application_storage(self):
        with tempfile.TemporaryDirectory() as media,patch.object(cc,'commercials_root',return_value=Path(media)):
            saved=cc.save_settings({'commercials_enabled':True})
            self.assertTrue(saved['commercials_enabled'])
            self.assertEqual(Path(saved['commercials_folder']),Path(media)/'clips')
            self.assertTrue((Path(media)/'incoming').is_dir())
            self.assertTrue((Path(media)/'clips').is_dir())
        for value in [0,True,30.5,'30']:
            with self.assertRaises(ValueError):cc.save_settings({'minimum_episodes':value})

    def test_large_mp4_import_is_appended_and_published_only_when_complete(self):
        with tempfile.TemporaryDirectory() as media,patch.object(cc,'commercials_root',return_value=Path(media)):
            row=cc.start_commercial_import('retro ads.mp4',6)
            first=cc.append_commercial_import(row['id'],0,io.BytesIO(b'abc'))
            self.assertEqual((first['received'],first['status']),(3,'copying'))
            self.assertTrue((Path(media)/'incoming'/(row['id']+'.partial')).exists())
            with self.assertRaises(ValueError):cc.append_commercial_import(row['id'],0,io.BytesIO(b'x'))
            done=cc.append_commercial_import(row['id'],3,io.BytesIO(b'def'))
            self.assertEqual((done['received'],done['status']),(6,'ready'))
            self.assertEqual((Path(media)/'incoming'/(row['id']+'.mp4')).read_bytes(),b'abcdef')
            self.assertFalse((Path(media)/'incoming'/(row['id']+'.partial')).exists())
            with self.assertRaises(ValueError):cc.start_commercial_import('not-video.txt',1)

    def test_successful_processing_deletes_source_but_failure_keeps_it(self):
        with tempfile.TemporaryDirectory() as media,patch.object(cc,'commercials_root',return_value=Path(media)):
            complete=cc.start_commercial_import('complete.mp4',3)
            cc.append_commercial_import(complete['id'],0,io.BytesIO(b'abc'))
            with patch('media.commercial_processor.process',return_value={'duration':90,'clips':3,'review':0}):
                cc._process_commercial_import(complete['id'])
            self.assertFalse((Path(media)/'incoming'/(complete['id']+'.mp4')).exists())
            self.assertEqual(cc.commercial_imports()[0]['status'],'complete')
            failed=cc.start_commercial_import('failed.mp4',3)
            cc.append_commercial_import(failed['id'],0,io.BytesIO(b'abc'))
            with patch('media.commercial_processor.process',side_effect=ValueError('bad cuts')):
                cc._process_commercial_import(failed['id'])
            self.assertTrue((Path(media)/'incoming'/(failed['id']+'.mp4')).exists())
            self.assertEqual(cc.commercial_imports()[0]['status'],'failed')

    def test_refresh_failure_excludes_cached_shows_and_preserves_saved_channels(self):
        cc.save_settings({'enabled':True})
        cc.save_schedule(cc.root()/'servers.json',[dict(id='s',name='Server',url='http://local',token='secret')])
        cc.save_schedule(cc.root()/'catalog.json',dict(shows=[dict(id='show',server_id='s',episodes=[])]))
        saved=[dict(id='channel',number='9000',name='Saved',enabled=True)]
        schedule=dict(epoch=100,programmes=[dict(title='Still saved')])
        cc.save_schedule(cc.root()/'channels.json',saved)
        cc.save_schedule(cc.root()/'channel.json',schedule)
        with patch.object(cc,'scan_server',side_effect=OSError('secret')):
            result=cc.refresh()
        self.assertEqual(result['status'],'warning')
        catalog=cc.read('catalog.json',{})
        self.assertEqual(catalog['shows'],[])
        self.assertEqual(catalog['servers'][0]['status'],'unavailable')
        self.assertEqual(cc.read('channels.json',[]),saved)
        self.assertEqual(cc.read('channel.json',{}),schedule)
        self.assertIn('no cached shows reused',json.dumps(result))
        self.assertNotIn('secret',json.dumps(result))

    def test_refresh_hook_runs_even_if_primary_update_fails(self):
        def fail(**kwargs):raise RuntimeError('provider offline')
        core=types.SimpleNamespace(run_master_update=fail)
        cc.save_settings({'enabled':True});cc.install(core)
        with patch.object(cc,'refresh',return_value={'status':'success'}) as refresh:
            with self.assertRaises(RuntimeError):core.run_master_update(trigger='manual')
            refresh.assert_called_once()

    def test_scan_filters_movies_specials_duplicate_files_and_paginates(self):
        sections=ET.fromstring('<MediaContainer><Directory type="movie" key="1"/><Directory type="show" key="2" title="TV"/></MediaContainer>')
        genres=ET.fromstring('<MediaContainer totalSize="1"><Directory ratingKey="5" title="Show"><Genre tag="Comedy"/></Directory></MediaContainer>')
        def episode(n,part,season=9):return f'<Video parentIndex="{season}" index="{n}" duration="600000" grandparentRatingKey="5" grandparentTitle="Show" title="Episode"><Media><Part key="/library/parts/{part}" file="/tv/{part}.mkv"/></Media></Video>'
        first=ET.fromstring('<MediaContainer totalSize="4">'+episode(1,'a')+episode(2,'a')+'</MediaContainer>')
        second=ET.fromstring('<MediaContainer totalSize="4">'+episode(3,'b')+episode(1,'special',0)+'</MediaContainer>')
        with patch.object(cc,'plex_xml',side_effect=[sections,genres,first,second]) as get:
            shows=cc.scan_server(dict(id='server',name='Plex',url='http://local',token='secret'))
        self.assertEqual(len(shows),1);self.assertEqual(len(shows[0]['episodes']),2)
        self.assertEqual(shows[0]['genres'],['Comedy'])
        summary=cc.summary(shows[0]);self.assertEqual(summary['count'],3);self.assertEqual(summary['gaps'],[])
        self.assertAlmostEqual(summary['hours'],.33,places=2)
        self.assertIn('Start=2',get.call_args.args[1])

    def test_eligibility_checks_real_file_availability_not_just_plex_entries(self):
        sections=ET.fromstring('<MediaContainer><Directory type="show" key="2" title="TV"/></MediaContainer>')
        genres=ET.fromstring('<MediaContainer totalSize="0"/>')
        catalog=ET.fromstring('<MediaContainer totalSize="1"><Video ratingKey="123"/></MediaContainer>')
        unavailable=ET.fromstring('<MediaContainer><Video ratingKey="123" parentIndex="1" index="1" duration="600000" grandparentRatingKey="5" grandparentTitle="Show"><Media><Part exists="0" accessible="0" key="/library/parts/123" file="/missing.mkv"/></Media></Video></MediaContainer>')
        with patch.object(cc,'plex_xml',side_effect=[sections,genres,catalog,unavailable]) as get:
            self.assertEqual(cc.scan_server(dict(id='s',name='Plex',url='http://local',token='secret')),[])
        self.assertEqual(get.call_args.args[1],'/library/metadata/123?checkFiles=1')

    def test_category_channels_are_deduplicated_and_reshuffled_on_every_scan(self):
        regular=dict(id='show-channel',number='1000',name='Saved Show',show_id='show',server_id='s1',
                     order='ordered',season_start=1,season_end=1,count=1,started_at=1,enabled=True,auto_numbered=True)
        cc.save_schedule(cc.root()/'channels.json',[regular])
        one=dict(id='one',server_id='s1',title='Same Show',genres=['Comedy','Drama'],episodes=[
            dict(episodes()[0],server_id='ignored')])
        duplicate=dict(id='two',server_id='s2',title='Same Show',genres=['Comedy'],episodes=[episodes()[0]])
        other=dict(id='three',server_id='s2',title='Other Show',genres=['Comedy'],episodes=[episodes()[1]])
        with patch.object(cc.secrets,'token_hex',side_effect=['first-comedy','first-drama','second-comedy','second-drama']):
            created=cc.refresh_category_channels([one,duplicate,other],100)
            first=cc.read('category-comedy.json',{})
            rebuilt=cc.refresh_category_channels([one,duplicate,other],200)
            second=cc.read('category-comedy.json',{})
        self.assertEqual([row['name'] for row in created],['Comedy','Drama'])
        self.assertEqual([row['number'] for row in created],['1902','1905'])
        self.assertEqual(len(first['episodes']),2)
        self.assertEqual(first['episodes'][0]['server_id'],'s1')
        self.assertNotEqual(first['seed'],second['seed'])
        self.assertEqual(second['epoch'],200)
        self.assertIn(regular,cc.read('channels.json',[]))
        self.assertEqual([row['name'] for row in rebuilt],['Comedy','Drama'])

    def test_creation_starts_now_and_refresh_does_not_reset_schedule(self):
        cc.save_settings({'enabled':True,'minimum_episodes':2})
        show=dict(id='show',server_id='server',title='Show',episodes=episodes())
        cc.save_schedule(cc.root()/'catalog.json',dict(shows=[show]))
        with patch.object(cc.time,'time',return_value=1000):
            row=cc.create_channel(dict(show_id='show',order='ordered',season_start=9,season_end=22))
        self.assertEqual(row['number'],'1000')
        state=cc.schedule(row['id'],1005)
        self.assertEqual(state['epoch'],1000);self.assertEqual(state['programmes'][0]['episode'],1)
        self.assertEqual(len(state['episodes']),4)
        cc.save_settings({'minimum_episodes':30})
        self.assertEqual(cc.schedule(row['id'],1006)['epoch'],1000)

    def test_add_all_creates_each_new_series_once_and_skips_existing(self):
        cc.save_settings({'enabled':True,'minimum_episodes':2})
        first=dict(id='first',server_id='server',title='First',episodes=episodes())
        second=dict(id='second',server_id='server',title='Second',episodes=episodes()[:1])
        cc.save_schedule(cc.root()/'catalog.json',dict(shows=[first,second]))
        existing=cc.create_channel(dict(show_id='first',order='ordered'))
        with patch.object(cc.time,'time',return_value=2000):
            result=cc.create_channels(dict(show_ids=['first','second','second'],allow_below_minimum=True))
        self.assertEqual(result['skipped'],1)
        self.assertEqual(len(result['created']),1)
        self.assertEqual(result['created'][0]['show_id'],'second')
        self.assertEqual(result['created'][0]['number'],'1001')
        self.assertEqual(result['created'][0]['order'],'ordered')
        self.assertEqual(len(cc.read('channels.json',[])),2)
        self.assertEqual(cc.read(existing['id']+'.json',{})['epoch'],existing['started_at'])

    def test_legacy_generated_numbers_move_into_custom_1000_block_once(self):
        rows=[dict(id='a',number='9000'),dict(id='b',number='42.5'),dict(id='c',number='9001')]
        cc.save_schedule(cc.root()/'channels.json',rows)
        self.assertEqual(cc.migrate_channel_numbers(),2)
        self.assertEqual([row['number'] for row in cc.read('channels.json',[])],['1000','42.5','1001'])
        self.assertEqual(cc.migrate_channel_numbers(),0)

    def test_automatic_custom_numbers_follow_series_title(self):
        rows=[dict(id='office',name='The Office',number='1000',auto_numbered=True),
              dict(id='abbott',name='Abbott Elementary',number='1001',auto_numbered=True)]
        ordered=cc.rebalance_channel_numbers(rows)
        self.assertEqual([(row['number'],row['name']) for row in ordered],
            [('1000','Abbott Elementary'),('1001','The Office')])

    def test_channel_edit_updates_metadata_and_rebuilds_only_when_programming_changes(self):
        cc.save_settings({'enabled':True,'minimum_episodes':2})
        show=dict(id='show',server_id='server',title='Show',episodes=episodes())
        cc.save_schedule(cc.root()/'catalog.json',dict(shows=[show]))
        with patch.object(cc.time,'time',return_value=1000):
            row=cc.create_channel(dict(show_id='show',order='ordered',season_start=9,season_end=22))
        original=cc.read(row['id']+'.json',{})
        cc.update_channel(row['id'],dict(name='My Show',number='90.01',enabled=False))
        edited=cc.read('channels.json',[])[0]
        self.assertEqual((edited['name'],edited['number'],edited['enabled']),('My Show','90.01',False))
        self.assertEqual(cc.read(row['id']+'.json',{}),original)
        with patch.object(cc.time,'time',return_value=2000):
            cc.update_channel(row['id'],dict(order='random',season_start=9,season_end=11))
        rebuilt=cc.read(row['id']+'.json',{})
        edited=cc.read('channels.json',[])[0]
        self.assertEqual((rebuilt['epoch'],rebuilt['order'],len(rebuilt['episodes'])),(2000,'random',3))
        self.assertEqual((edited['season_start'],edited['season_end'],edited['count']),(9,11,3))

    def test_channel_delete_removes_only_custom_channel_and_schedule(self):
        cc.save_settings({'enabled':True,'minimum_episodes':2})
        show=dict(id='show',server_id='server',title='Show',episodes=episodes())
        catalog=dict(shows=[show],updated_at=123)
        cc.save_schedule(cc.root()/'catalog.json',catalog)
        with patch.object(cc.time,'time',return_value=1000):
            row=cc.create_channel(dict(show_id='show',order='ordered',season_start=9,season_end=22))
        cc.delete_channel(row['id'])
        self.assertEqual(cc.read('channels.json',[]),[])
        self.assertFalse((cc.root()/(row['id']+'.json')).exists())
        self.assertEqual(cc.read('catalog.json',{}),catalog)
        self.assertTrue(cc.settings()['enabled'])

    def test_stream_uses_each_episode_part_and_redacts_token_from_payload(self):
        slot=dict(kind='episode',part='/library/parts/second.mkv',start=5,duration=10)
        args=segment_command(slot,dict(server='http://plex',token='secret'),0,False)
        self.assertEqual(args[args.index('-i')+1],'http://plex/library/parts/second.mkv')
        cc.save_schedule(cc.root()/'servers.json',[dict(id='s',name='Server',url='http://local',token='secret')])
        self.assertNotIn('secret',json.dumps(cc.payload()))

    def test_mixed_channel_segment_selects_its_own_plex_server(self):
        slot=dict(kind='episode',part='/library/parts/two.mkv',server_id='two',start=0,duration=10)
        cfg=dict(servers={'one':dict(server='http://one',token='first'),
                          'two':dict(server='http://two',token='second')})
        args=segment_command(slot,cfg,0,False)
        self.assertEqual(args[args.index('-i')+1],'http://two/library/parts/two.mkv')
        self.assertIn('X-Plex-Token: second',args[args.index('-headers')+1])


if __name__=='__main__':unittest.main()
