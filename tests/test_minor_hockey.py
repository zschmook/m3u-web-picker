import collections
from datetime import datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image
import core
import event_logos
import sports
from sports import feeds
from minor_hockey_teams import MINOR_HOCKEY_TEAMS


class MinorHockeyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'sports.db';sports.init_db(self.db)
        self.lookup=sports._build_team_lookup(self.db)
        self.now=datetime(2026,10,8,10,tzinfo=ZoneInfo('America/New_York'))

    def event(self,name):
        channel=core.parse_m3u_text('#EXTM3U\n#EXTINF:-1 group-title="FloSports",'+name+'\nhttp://provider.test/stream.ts\n')[0]
        return sports._event_from_text(self.db,channel,name,dict(sports.DEFAULT_SETTINGS),self.now,team_lookup=self.lookup)

    def test_catalog_contains_current_leagues_and_all_91_teams_without_network_or_enabled_rules(self):
        counts=collections.Counter(t['league_id'] for t in sports.catalog_payload(self.db,scope_type='team') if t['league_id'] in {'ahl','echl','sphl','fphl'})
        self.assertEqual(counts,{'ahl':32,'echl':30,'sphl':12,'fphl':17})
        leagues={item['id']:item for item in sports.catalog_payload(self.db,scope_type='league')}
        for league in counts:
            self.assertEqual(leagues[league]['metadata']['sport_id'],'hockey')
            self.assertTrue(leagues[league]['logo_url'].startswith('/static/icons/hockey/'))
        with sports.closing(sports._connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sports_rules').fetchone()[0],0)
        self.assertFalse(sports.get_settings(self.db)['enabled'])

    def test_all_bundled_team_marks_are_valid_small_pngs(self):
        root=Path(__file__).resolve().parents[1]
        for team in MINOR_HOCKEY_TEAMS:
            path=root/team['logo_url'].lstrip('/')
            with self.subTest(team=team['name']),Image.open(path) as image:
                self.assertEqual(image.format,'PNG')
                self.assertLessEqual(max(image.size),256)
                self.assertIsNotNone(image.getbbox())
                self.assertGreater(path.stat().st_size,100)

    def test_existing_league_blocks_stay_stable(self):
        for league,index in {'mlb':0,'nhl':1,'ahl':19,'ncaa-hockey':20,'snooker':217}.items():
            self.assertEqual(sports.LEAGUE_BLOCK_INDEX[league],index)
        self.assertEqual([sports.LEAGUE_BLOCK_INDEX[league] for league in ('echl','sphl','fphl')],[218,219,220])

    def test_real_flosports_ahl_feeds_infer_league_and_canonical_teams(self):
        event=self.event('(FLSP 531) | hockey: Toronto Marlies vs Hamilton Hammers (Home) (2026-10-09 19:00:55)')
        self.assertEqual(event['league_id'],'ahl')
        self.assertEqual(event['sport_id'],'hockey')
        self.assertEqual(event['away_team_id'],'ahl:toronto-marlies')
        self.assertEqual(event['home_team_id'],'ahl:hamilton-hammers')

    def test_real_echl_feed_resolves_city_variant_and_accent(self):
        event=self.event('(FLSP 373) | hockey: Trois_Rivières vs Adirondack Thunder (Home) (2026-10-08 19:00:15)')
        self.assertEqual(event['league_id'],'echl')
        self.assertEqual(event['away_team_id'],'echl:trois-rivieres-lions')
        self.assertEqual(event['home_team_id'],'echl:adirondack-thunder')

    def test_sphl_and_fphl_resolve_without_abbreviation_and_support_old_brand(self):
        for text,league in [('hockey: Pensacola Ice Flyers vs Huntsville Havoc (2026-10-09 19:00:00)','sphl'),
                            ('hockey: Baton Rouge Zydeco vs Monroe Moccasins (2026-10-09 19:00:00)','fphl')]:
            with self.subTest(league=league):
                event=self.event(text);self.assertEqual(event['league_id'],league)
                self.assertTrue(event['away_team_id'].startswith(league+':'))
                self.assertTrue(event['home_team_id'].startswith(league+':'))

    def test_four_leagues_detect_explicit_labels(self):
        for text,league in [('AHL hockey','ahl'),('ECHL hockey','echl'),('Southern Professional Hockey League','sphl'),('Federal Prospects Hockey League','fphl'),('Federal Hockey League','fphl')]:
            with self.subTest(text=text):self.assertEqual(sports._detect_league(text),league)

    def test_unknown_cross_league_youth_and_other_sports_do_not_borrow_hockey_teams(self):
        for name in ['hockey: Toronto Marlies vs Orlando Solar Bears','hockey: Hartford Wolf Pack U18 vs Providence Bruins U18',
                     'football: Toronto Marlies vs Hamilton Hammers','hockey: Unknown Club vs Orlando Solar Bears',
                     'hockey: Bruins vs Penguins']:
            with self.subTest(name=name):
                event=self.event(name+' (2026-10-09 19:00:00)')
                self.assertNotIn(event.get('league_id'),{'ahl','echl','sphl','fphl'})
                self.assertEqual(event['away_team_id'],'');self.assertEqual(event['home_team_id'],'')

    def test_placeholder_and_college_labels_keep_existing_semantics(self):
        self.assertIsNone(self.event('No EVENT Today'))
        event=self.event('NCAA hockey: Providence vs Hartford (2026-10-09 19:00:00)')
        self.assertEqual(event['league_id'],'ncaa-hockey')

    def test_bundled_logo_loader_uses_no_http_and_rejects_path_traversal(self):
        url='/static/icons/hockey/ahl/toronto-marlies.png'
        with patch.object(event_logos.net_safety,'open_safely') as remote:
            data,kind=event_logos._fetch_logo(url)
        remote.assert_not_called();self.assertEqual(kind,'image/png')
        with Image.open(io.BytesIO(data)) as image:self.assertIsNotNone(image.getbbox())
        for bad in ['/static/icons/hockey/../../secret.png','/static/icons/hockey/%2e%2e/secret.png','/static/icons/hockey/ahl/toronto-marlies.png?x=1']:
            self.assertIsNone(event_logos._bundled_sports_logo(bad))

    def test_bundled_league_marks_load_without_network_and_reject_unsafe_paths(self):
        with patch.object(event_logos.net_safety, 'open_safely') as remote:
            for url in ['/static/icons/leagues/nhl.png', '/static/icons/leagues/ncaa.png']:
                self.assertEqual(event_logos._clean_http_url(url), url)
                data, kind = event_logos._fetch_logo(url)
                self.assertEqual(kind, 'image/png')
                with Image.open(io.BytesIO(data)) as image:
                    self.assertIsNotNone(image.getbbox())
        remote.assert_not_called()
        for bad in ['/static/icons/leagues/../../secret.png',
                    '/static/icons/leagues/%2e%2e/secret.png',
                    '/static/icons/leagues/nhl.png?x=1',
                    '/static/icons/leagues/missing.png',
                    '/static/icons/other/nhl.png']:
            self.assertEqual(event_logos._clean_http_url(bad), '')

    def test_minor_league_logo_lookup_does_not_query_espn_nhl_catalog(self):
        with patch('sports.feeds.espn_team_logos.espn_full_default_url') as espn:
            self.assertEqual(feeds._espn_team_logo({'league_id':'ahl'},team_name='Toronto Marlies'),'')
        espn.assert_not_called()

    def test_selected_team_and_league_rules_match_canonical_minor_hockey_event(self):
        event=self.event('hockey: Orlando Solar Bears vs Florida Everblades (2026-10-09 19:30:00)')
        team=sports.add_rule(self.db,{'scope_type':'team','scope_id':'echl:orlando-solar-bears','feed_preference':'favorite'})
        league=sports.add_rule(self.db,{'scope_type':'league','scope_id':'echl','feed_preference':'best'})
        self.assertEqual({rule['id'] for rule in sports._matching_rules(event,[team,league])},{team['id'],league['id']})

    def test_flosports_broadcast_markers_preserve_favorite_team_feed_selection(self):
        event=self.event('hockey: Toronto Marlies vs Hamilton Hammers (2026-10-09 19:00:00)')
        event['source_channels']=[{'name':'Toronto Marlies vs Hamilton Hammers (Home)','url':'http://provider.test/home'},
                                  {'name':'Toronto Marlies vs Hamilton Hammers (Away)','url':'http://provider.test/away'}]
        candidates=sports._build_feeds(event,{}, {'scope_type':'team','scope_id':'ahl:toronto-marlies','feed_preference':'favorite'}, {})
        self.assertEqual(candidates[0]['feed_type'],'away')
        self.assertEqual(candidates[0]['team_id'],'ahl:toronto-marlies')
        self.assertEqual(candidates[0]['channel']['url'],'http://provider.test/away')

    def test_bundled_marks_render_and_cache_a_real_matchup_without_network(self):
        cache=Path(self.temp.name)/'logos';events=cache/'events';events.mkdir(parents=True)
        with patch.object(event_logos,'_paths',return_value=(cache,events,self.db)), \
             patch.object(event_logos,'_public_event_logo_url',side_effect=lambda digest:f'http://picker.test/api/event-logo/{digest}.png'), \
             patch.object(event_logos.net_safety,'open_safely') as remote:
            url=event_logos.register_matchup_logo(event_key='test-minor-game',away_team_id='ahl:toronto-marlies',
                away_team_name='Toronto Marlies',away_logo_url='/static/icons/hockey/ahl/toronto-marlies.png',
                home_team_id='ahl:hamilton-hammers',home_team_name='Hamilton Hammers',home_logo_url='/static/icons/hockey/ahl/hamilton-hammers.png')
            digest=url.rsplit('/',1)[-1].removesuffix('.png')
            first=event_logos.render_event_logo(digest);second=event_logos.render_event_logo(digest)
        remote.assert_not_called();self.assertEqual(first[1],'generated');self.assertEqual(second[1],'hit')
        self.assertEqual(first[0],second[0])
        with Image.open(io.BytesIO(first[0])) as image:self.assertIsNotNone(image.getbbox())


if __name__=='__main__':unittest.main()
