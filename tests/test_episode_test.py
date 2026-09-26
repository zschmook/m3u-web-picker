from pathlib import Path
import io
import queue
import threading
import unittest
from unittest.mock import patch, Mock
from flask import Flask
from api import episode_test as episode


class EpisodeTestChannelTests(unittest.TestCase):
    def test_schedule_has_preroll_and_three_ads_per_two_episode_minutes(self):
        cfg={'duration':3122.592,'filename':'episode.mkv'}
        paths=[Path(f'ad_{i:04d}.mp4') for i in range(163)]
        plan=episode.make_plan(cfg,paths,{p.name:15 for p in paths})
        self.assertEqual(plan[0]['kind'],'commercial')
        show=[s for s in plan if s['kind']=='episode']
        ads=[s for s in plan if s['kind']=='commercial']
        self.assertEqual(len(show),27)
        self.assertEqual(len(ads),79)
        self.assertEqual(len({s['filename'] for s in ads}),79)
        self.assertAlmostEqual(sum(s['duration'] for s in show),cfg['duration'])
        for index,slot in enumerate(show):
            self.assertEqual(slot['start'],index*120)
        for index,slot in enumerate(plan[:-1]):
            if slot['kind']=='episode':
                self.assertEqual([s['kind'] for s in plan[index+1:index+4]],['commercial']*3)

    def test_exact_multiple_does_not_end_with_an_ad_break(self):
        plan=episode.make_plan({'duration':240,'filename':'episode.mkv'},[Path('ad.mp4')],{'ad.mp4':15})
        self.assertEqual(plan[-1]['kind'],'episode')
        self.assertEqual(len(plan),6)

    def test_playlist_and_head_start_nothing(self):
        app=Flask(__name__)
        episode.register_episode_test_routes(app)
        with patch.object(episode,'configuration',return_value={}),patch.object(episode,'BufferedSegment') as start:
            client=app.test_client()
            self.assertIn('tvg-chno="0.002"',client.get('/playlist/episode-test.m3u').text)
            self.assertEqual(client.head(episode.STREAM_PATH).status_code,200)
            start.assert_not_called()

    def test_episode_seek_and_common_encoding(self):
        cfg={'server':'http://plex.test:32400','part':'/library/parts/1/file.mkv','token':'test-token'}
        args=episode.segment_command({'kind':'episode','start':120,'duration':120},cfg,180)
        self.assertEqual(args[args.index('-ss')+1],'120')
        self.assertEqual(args[args.index('-output_ts_offset')+1],'180')
        self.assertEqual(args[args.index('-ac')+1],'2')
        self.assertIn('http://plex.test:32400/library/parts/1/file.mkv',args)
        self.assertEqual(args[args.index('-reconnect')+1],'1')

    def test_truncated_success_resumes_same_movie_at_emitted_position(self):
        from media.episode_buffer import END
        created=[]
        class Segment:
            def __init__(self,args,offset,duration,*rest):
                self.args=args;self.closed=False;self.started_at=0
                self.ready=threading.Event();self.ready.set();self.returncode=0
                self.output=queue.Queue();created.append(self)
                progress=2 if len(created)==1 else duration
                self.output.put((bytes([0x47,0x01,0,0x10])+bytes(184),progress))
                self.output.put(END)
            def close(self):self.closed=True
        plan=[dict(label='movie',kind='episode',filename='movie.mp4',part='/library/parts/1/movie.mp4',start=100,duration=10),
              dict(label='next',kind='episode',filename='next.mp4',part='/library/parts/2/next.mp4',start=0,duration=1)]
        cfg=dict(server='http://plex',token='private')
        from media.commercials_debug import snapshot
        with patch.object(episode,'BufferedSegment',Segment),patch.object(episode.time,'monotonic',side_effect=range(100,200)):
            self.assertEqual(len(list(episode.stream_plan(plan,cfg,channel='2500'))),3)
        self.assertEqual(len(created),4)
        self.assertEqual(created[2].args[created[2].args.index('-ss')+1],'102')
        self.assertEqual(created[2].args[created[2].args.index('-t')+1],'8')
        self.assertIn('http://plex/library/parts/2/next.mp4',created[3].args)
        self.assertTrue(all(s.closed for s in created))
        self.assertEqual(snapshot()['sessions'][0]['clips_completed'],2)

    def test_empty_plex_reads_stop_after_bounded_retries_without_completing_movie(self):
        from media.episode_buffer import END
        created=[]
        class Segment:
            def __init__(self,*args):
                self.closed=False;self.started_at=0;self.returncode=0
                self.ready=threading.Event();self.output=queue.Queue();self.output.put(END);created.append(self)
            def close(self):self.closed=True
        plan=[dict(label='movie',kind='episode',filename='movie.mp4',part='/library/parts/1/movie.mp4',start=100,duration=100)]
        from media.commercials_debug import snapshot
        with patch.object(episode,'BufferedSegment',Segment):
            self.assertEqual(list(episode.stream_plan(plan,dict(server='http://plex',token='private'),channel='2500')),[])
        self.assertEqual(len(created),4)
        self.assertTrue(all(s.closed for s in created))
        self.assertEqual(snapshot()['sessions'][0]['streamed_seconds'],0)
        self.assertEqual(snapshot()['sessions'][0]['clips_completed'],0)

    def test_next_segment_starts_when_current_begins_and_both_cancel(self):
        from media.episode_buffer import END
        created=[]
        class FakeSegment:
            def __init__(self,args,offset,duration,*rest):
                self.output=queue.Queue()
                self.output.put((bytes([0x47,0x01,0,0x10])+bytes(184),1))
                self.output.put(END)
                self.ready=threading.Event();self.ready.set()
                self.returncode=0;self.closed=False;self.started_at=0
                created.append(self)
            def close(self):self.closed=True
        plan=[{'label':'ad1','kind':'commercial','filename':'ad1','path':'/ad1.mp4','duration':12,'start':0},
              {'label':'ad2','kind':'commercial','filename':'ad2','path':'/ad2.mp4','duration':12,'start':0}]
        with patch.object(episode,'BufferedSegment',FakeSegment):
            stream=episode.stream_plan(plan,{'token':'test'})
            next(stream)
            self.assertEqual(len(created),2)
            self.assertFalse(any(s.closed for s in created))
            stream.close()
            self.assertTrue(all(s.closed for s in created))

    def test_timestamp_reader_and_transport_continuity(self):
        from media.episode_buffer import video_clock,TransportContinuity
        pts=90000*12
        encoded=bytes([0x21|((pts>>29)&14),(pts>>22)&255,((pts>>14)&254)|1,(pts>>7)&255,((pts<<1)&254)|1])
        pes=b'\x00\x00\x01\xe0\x00\x00\x80\x80\x05'+encoded
        packet=bytes([0x47,0x41,0,0x10])+pes+bytes(184-len(pes))
        self.assertEqual(video_clock(packet),12)
        continuity=TransportContinuity()
        a=continuity.rewrite(packet);b=continuity.rewrite(packet)
        self.assertEqual(a[3]&15,0)
        self.assertEqual(b[3]&15,1)

    def test_full_prefetch_buffer_can_be_cancelled(self):
        from media import episode_buffer as buffer
        process=Mock()
        process.stdout.read.return_value=bytes([0x47,0x01,0,0x10])+bytes(184)
        process.stderr=io.BytesIO()
        with patch.object(buffer,'BUFFER_CHUNKS',1),patch.object(buffer.subprocess,'Popen',return_value=process),patch.object(buffer,'terminate'):
            segment=buffer.BufferedSegment(['unused'],0,120,'test',lambda error:None)
            self.assertTrue(segment.ready.wait(1))
            segment.close()
            self.assertFalse(segment.reader.is_alive())
            self.assertFalse(segment.errors.is_alive())

if __name__=='__main__':unittest.main()
