from copy import deepcopy
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask

from api import breaking_bad as channel
from api.episode_test import segment_command
from media.scheduled_channel import new_schedule, extend_schedule, save_schedule, segments_at


def fixture():
    episodes = [dict(filename=f'S01E{i:02d}.mkv',path=f'/episodes/{i}.mkv',title=f'Breaking Bad S01E{i:02d}',
                     season=1,episode=i,duration=1020+i*13) for i in range(1,5)]
    ads = [dict(filename=f'ad_{i:04d}.mp4',path=f'/ads/{i}.mp4',duration=15+i) for i in range(8)]
    return new_schedule(episodes,ads,1000.0,'repeatable-test-seed')


class BreakingBadChannelTests(unittest.TestCase):
    def test_schedule_has_prerolls_exact_break_clock_and_no_gaps(self):
        state = fixture()
        extend_schedule(state,15000)
        segments = state['segments']
        self.assertEqual(segments[0]['ad_role'],'preroll')
        elapsed = 0
        for i,slot in enumerate(segments):
            if slot['kind']=='episode':
                elapsed += slot['duration']
                self.assertLessEqual(elapsed,900.000001)
            elif slot.get('ad_role')=='break' and (i==0 or segments[i-1].get('ad_role')!='break'):
                self.assertAlmostEqual(elapsed,900)
                self.assertEqual([s.get('ad_role') for s in segments[i:i+3]],['break']*3)
                elapsed = 0
        for a,b in zip(segments,segments[1:]):
            self.assertAlmostEqual(a['wall_stop'],b['wall_start'])
        for programme in state['programmes']:
            entries = [s for s in segments if programme['start']<=s['wall_start']<programme['stop']]
            self.assertEqual(entries[0]['ad_role'],'preroll')
            source = next(e for e in state['episodes'] if e['filename']==programme['filename'])
            shows = [s for s in entries if s['kind']=='episode']
            self.assertEqual(shows[0]['start'],0)
            self.assertAlmostEqual(sum(s['duration'] for s in shows),source['duration'])
            for a,b in zip(shows,shows[1:]):
                self.assertAlmostEqual(a['start']+a['duration'],b['start'])
            self.assertAlmostEqual(entries[-1]['wall_stop'],programme['stop'])

    def test_episode_and_ad_shuffle_bags_do_not_repeat_early(self):
        state = fixture();extend_schedule(state,30000)
        episodes = [p['filename'] for p in state['programmes']]
        ads = [s['filename'] for s in state['segments'] if s['kind']=='commercial']
        for values,size in [(episodes,4),(ads,8)]:
            for start in range(0,len(values)-size+1,size):
                self.assertEqual(len(set(values[start:start+size])),size)
            self.assertTrue(all(a!=b for a,b in zip(values,values[1:])))

    def test_clients_and_restarts_seek_the_same_episode_or_commercial(self):
        state=fixture();extend_schedule(state,10000)
        reloaded=json.loads(json.dumps(state))
        for slot in [state['segments'][0],next(s for s in state['segments'] if s['kind']=='episode'),
                     next(s for s in state['segments'] if s.get('ad_role')=='break')]:
            instant=slot['wall_start']+7
            a=next(segments_at(state,instant));b=next(segments_at(reloaded,instant))
            self.assertEqual(a,b)
            self.assertEqual(a['start'],slot['start']+7)
            self.assertAlmostEqual(a['duration'],slot['duration']-7)
        later=next(segments_at(state,8000))
        self.assertNotEqual(later['sequence'],state['segments'][0]['sequence'])

    def test_extending_after_restart_keeps_published_schedule(self):
        state=fixture();extend_schedule(state,6000)
        old=deepcopy(state['segments'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'schedule.json';save_schedule(path,state)
            state=json.loads(path.read_text());extend_schedule(state,20000)
        self.assertEqual(state['segments'][:len(old)],old)
        fresh=fixture();extend_schedule(fresh,20000)
        self.assertEqual(state['segments'],fresh['segments'])

    def test_guide_and_xmltv_use_ad_inclusive_programme_times(self):
        state=fixture();extend_schedule(state,20000)
        now=1002
        with patch.object(channel,'schedule',return_value=state):
            item=channel.guide_item(now)
        self.assertEqual(item['number'],'0.03')
        self.assertEqual(item['now']['title'],state['programmes'][0]['title'])
        self.assertGreater(len(item['upcoming']),0)
        self.assertIn(item['now']['title'].encode(),channel.xmltv(state,now))

    def test_head_playlist_and_guide_never_start_an_encoder(self):
        state=fixture();extend_schedule(state,10000)
        app=Flask(__name__)
        with tempfile.TemporaryDirectory() as directory,patch.object(channel.core,'DATA_DIR',Path(directory)):
            channel.register_breaking_bad_routes(app)
            with patch.object(channel,'schedule',return_value=state),patch.object(channel,'stream_plan') as play:
                client=app.test_client()
                self.assertEqual(client.head(channel.STREAM_PATH).status_code,200)
                playlist=client.get('/playlist/breaking-bad.m3u').text
                self.assertIn('tvg-chno="0.03"',playlist)
                self.assertIn('/epg/breaking-bad.xml',playlist)
                channel.guide_item(1002)
                play.assert_not_called()

    def test_local_hevc_input_does_not_require_plex(self):
        args=segment_command(dict(kind='episode',path='/episodes/show.mkv',start=901,duration=15),{},0,False)
        self.assertNotIn('-headers',args)
        self.assertNotIn('-re',args)
        self.assertEqual(args[args.index('-i')+1],'/episodes/show.mkv')
        self.assertEqual(args[args.index('-ss')+1],'901')

    def test_schedule_continues_past_current_horizon(self):
        state=fixture();extend_schedule(state,4000)
        newer=deepcopy(state);extend_schedule(newer,8000)
        with patch.object(channel,'schedule',return_value=newer):
            values=list(itertools.islice(channel.playout(state,3999),len(list(segments_at(state,3999)))+1))
        self.assertGreater(values[-1]['sequence'],state['segments'][-1]['sequence'])

    def test_timestamp_wrap_does_not_reset_long_running_channel_clock(self):
        from media.episode_buffer import video_clock
        period=(1<<33)/90000
        pts=90000*12
        encoded=bytes([0x21|((pts>>29)&14),(pts>>22)&255,((pts>>14)&254)|1,(pts>>7)&255,((pts<<1)&254)|1])
        pes=b'\x00\x00\x01\xe0\x00\x00\x80\x80\x05'+encoded
        packet=bytes([0x47,0x41,0,0x10])+pes+bytes(184-len(pes))
        self.assertAlmostEqual(video_clock(packet,period+10),period+12)

    def test_rolling_debug_history_is_bounded(self):
        from media.commercials_debug import PlayoutDebugSession
        session=PlayoutDebugSession('0.03')
        for i in range(200):
            slot=dict(label=str(i),filename='episode',kind='episode',start=i,duration=1)
            session.begin(slot,i,i);session.advance(i+.5,100);session.complete(i+1,100)
            session.transition({'from':str(i),'to':str(i+1)})
        self.assertEqual(len(session.record['plan']),50)
        self.assertEqual(len(session.record['transitions']),100)
        self.assertEqual(session.record['clips_completed'],200)
        session.close()

    def test_tuning_into_an_empty_tail_continues_to_next_segment(self):
        import queue
        import threading
        from api import episode_test
        from media.episode_buffer import END
        class Segment:
            def __init__(self,args,*rest):
                self.output=queue.Queue();self.ready=threading.Event();self.ready.set()
                self.started_at=0;self.returncode=0
                if '/next.mp4' in args:
                    self.output.put((bytes([0x47,0x01,0,0x10])+bytes(184),1))
                self.output.put(END)
            def close(self):pass
        plan=[dict(kind='commercial',filename='tail',path='/tail.mp4',label='tail',start=0,duration=.001),
              dict(kind='commercial',filename='next',path='/next.mp4',label='next',start=0,duration=1)]
        with patch.object(episode_test,'BufferedSegment',Segment):
            self.assertEqual(len(list(episode_test.stream_plan(plan,{},channel='0.03'))),1)


if __name__=='__main__':unittest.main()
