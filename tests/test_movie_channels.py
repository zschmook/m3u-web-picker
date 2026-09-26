from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from flask import Flask
import movie_channels as movies
import custom_channels as plex
from api import movie_channels as api
from media.scheduled_channel import segments_at, save_schedule


def film(identity, genres, release='', hallmark=False):
    return dict(id=identity,title=identity,year='2026',genres=genres,release_date=release,hallmark=hallmark,
        duration=600,part='/library/parts/'+identity+'/movie.mkv',filename=identity+'.mkv',
        server_id='server',season=1,episode=1,description='Movie description')


class MovieChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.patch=patch.object(movies,'root',return_value=self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.enable=patch.object(movies,'enabled',return_value=True);self.enable.start();self.addCleanup(self.enable.stop)
        self.servers=[dict(id='server',name='Plex',url='http://plex.local',token='secret')]

    def test_genre_overlap_hallmark_noir_and_release_date_top_five(self):
        films=[film(str(i),['Drama','Film-Noir'],f'2099-01-{i:02d}') for i in range(1,8)]
        films += [film('hallmark',['Romance'],hallmark=True),film('unknown',['Drama']),dict(films[0])]
        pools={row['id']:selected for row,selected in movies.select_channels(films)}
        self.assertEqual(len(pools['drama']),8)
        self.assertEqual(len(pools['film-noir']),7)
        self.assertEqual([m['id'] for m in pools['just-released']],['7','6','5','4','3'])
        self.assertEqual(pools['hallmark'][0]['id'],'hallmark')
        self.assertEqual(pools['romance'][0]['id'],'hallmark')

    def test_schedule_is_commercial_free_shared_and_reshuffled_only_on_refresh(self):
        films=[film('one',['Drama']),film('two',['Drama'])]
        with patch.object(movies,'scan_server',return_value=films):
            result=movies.refresh(self.servers,now=100)
        self.assertEqual(result['count'],1)
        state=movies.schedule('drama',now=105)
        self.assertFalse(state['ads']['enabled'])
        self.assertTrue(all(s['kind']=='episode' for s in state['segments']))
        self.assertEqual(len({p['id'] for p in state['programmes'][:2]}),2)
        self.assertEqual(next(segments_at(state,105))['start'],5)
        self.assertEqual(movies.schedule('drama',now=110)['epoch'],100)
        seed=state['seed']
        movies.toggle('drama',False)
        with patch.object(movies,'scan_server',return_value=films):movies.refresh(self.servers,now=200)
        self.assertNotEqual(movies.read('drama.json',{})['seed'],seed)
        self.assertFalse(movies.read('channels.json',[])[0]['enabled'])

    def test_failed_scan_preserves_complete_lineup(self):
        with patch.object(movies,'scan_server',return_value=[film('one',['Drama'])]):movies.refresh(self.servers,now=100)
        old=movies.read('drama.json',{})
        with patch.object(movies,'scan_server',side_effect=OSError('private-token')):
            result=movies.refresh(self.servers,now=200)
        self.assertEqual(result['status'],'warning')
        self.assertEqual(movies.read('drama.json',{}),old)
        self.assertNotIn('private-token',str(result))

    def test_scan_checks_files_uses_external_types_and_skips_tv_and_unavailable(self):
        sections=ET.fromstring('<MediaContainer><Directory key="1" type="movie"/><Directory key="2" type="show"/></MediaContainer>')
        page=ET.fromstring('<MediaContainer totalSize="4"><Video ratingKey="1"/><Video ratingKey="2"/><Video ratingKey="3"/><Video ratingKey="4"/></MediaContainer>')
        def video(key,title,imdb,exists='1'):
            return f'<Video type="movie" title="{title}" year="2026" originallyAvailableAt="2026-09-01" duration="600000"><Guid id="imdb://{imdb}"/><Genre tag="Drama"/><Media><Part key="/library/parts/{key}/movie.mkv" file="C:\\Hallmark\\{title}.mkv" exists="{exists}" accessible="1"/></Media></Video>'
        checked=ET.fromstring('<MediaContainer>'+video('1','Film','tt1')+video('2','TV','tt2')+video('3','Missing','tt3','0')+video('4','Pilot','tt0196712')+'</MediaContainer>')
        metadata={'imdb':{'tt1':{'type':'movie','genres':['Film-Noir']},'tt2':{'type':'tvSeries','genres':['Drama']}}}
        with patch.object(plex,'plex_xml',side_effect=[sections,page,checked]) as fetch:
            films=movies.scan_server(self.servers[0],metadata)
        self.assertEqual(len(films),1)
        self.assertEqual(films[0]['genres'],['Film-Noir'])
        self.assertTrue(films[0]['hallmark'])
        self.assertEqual(films[0]['release_date'],'2026-09-01')
        self.assertIn('checkFiles=1',fetch.call_args.args[1])

    def test_guide_export_and_restart_share_the_same_movie(self):
        with patch.object(movies,'scan_server',return_value=[film('one',['Drama'],'2026-09-01')]):movies.refresh(self.servers,now=100)
        app=Flask(__name__)
        with patch.object(api.time,'time',return_value=105),patch.object(plex,'read',return_value=self.servers),app.test_request_context('/'):
            items=api.guide_items()
            drama=next(c for c in items if c['name']=='Drama')
            self.assertEqual(drama['now']['title'],'one')
            self.assertTrue(drama['now']['restart_url'].startswith('/guide/play/restart/'))
            self.assertNotIn('secret',str(items))
            self.assertNotIn('/library/parts',str(items))
            lines=api.playlist_lines('http://local')
            self.assertIn('http://local/stream/movies/drama.ts',lines)
            xml=api.merge_epg(ET.Element('tv'),items)
            p=xml.find("programme[@channel='movie-drama']")
            self.assertEqual(p.findtext('title'),'one')
            self.assertEqual(p.findtext('date'),'20260901')
            self.assertIsNone(p.find('episode-num'))


if __name__=='__main__':unittest.main()
