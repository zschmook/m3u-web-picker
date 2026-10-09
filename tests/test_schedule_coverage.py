from contextlib import closing
from datetime import datetime, date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
import sports
from sports import schedule_coverage as coverage, schedule_api_requests


class Response:
    headers = {'x-ratelimit-requests-remaining': '98'}
    def __init__(self, payload): self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size): return self.payload


class ScheduleCoverageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Path(self.folder.name) / 'sports.db'
        sports.init_db(self.db)
        self.now = datetime(2026, 10, 8, 12, tzinfo=ZoneInfo('America/New_York'))

    def store(self, product, rows):
        with closing(sports._connect(self.db)) as conn:
            conn.execute('INSERT OR REPLACE INTO sports_schedule_reference_cache(source,cache_key,season,fetched_at,raw_json) VALUES (?,?,0,?,?)',
                ('api-sports-' + product, coverage.CATALOG_KEY, self.now.isoformat(), json.dumps(rows)))
            conn.commit()

    def add(self, scope_id, scope_type='league'):
        sports.add_rule(self.db, dict(scope_type=scope_type, scope_id=scope_id))

    def test_selected_nhl_and_flyers_share_dataset_and_ncaa_is_included(self):
        with closing(sports._connect(self.db)) as conn:
            for league, name in [('nhl','Philadelphia Flyers'),('nba','Philadelphia 76ers')]:
                sports._upsert_catalog_item(conn,scope_type='team',scope_id=f'{league}:{sports._slug(name)}',display_name=name,
                    subtitle='',league_id=league,aliases=[name],logo_url='',metadata={'sport_id':sports.LEAGUE_SPORTS[league]},source='provider')
            conn.commit()
        for key, kind in [('nhl','league'),('nhl:philadelphia-flyers','team'),('ncaaf-fbs','league'),('nba:philadelphia-76ers','team')]:
            self.add(key, kind)
        plan = sports.schedule_api_request_plan(self.db)
        self.assertEqual(plan['dataset_ids'], ['ncaa', 'nba', 'nhl'])
        self.assertEqual(plan['dataset_ids'].count('nhl'), 1)
        self.assertEqual(plan['legacy_rules'], [])
        state = sports.schedule_api_status_payload(self.db, now=self.now)
        self.assertEqual({row['id'] for row in state['apis']}, {'ncaa','nba','nhl'})

    def test_new_league_is_promoted_from_catalogue_without_static_dataset(self):
        self.add('ncaab-men')
        self.assertIn('NCAA Men’s Basketball', sports.schedule_api_request_plan(self.db)['legacy_rules'])
        self.store('basketball', [{'id':116,'name':'NCAA','seasons':[{'season':'2026-2027'}]}])
        plan = sports.schedule_api_request_plan(self.db)
        self.assertEqual(plan['dataset_ids'], ['ncaab-men'])
        self.assertEqual(plan['datasets'][0]['remote_league_id'], 116)
        self.assertEqual(plan['legacy_rules'], [])

    def test_old_or_ambiguous_coverage_does_not_replace_provider_matching(self):
        self.add('ahl')
        self.store('hockey', [{'id':58,'name':'AHL','seasons':[{'season':2017}]}])
        with patch.object(coverage, 'datetime') as clock:
            clock.now.return_value = self.now
            plan = sports.schedule_api_request_plan(self.db)
        self.assertEqual(plan['dataset_ids'], [])
        self.assertIn('AHL', plan['legacy_rules'])
        self.store('basketball', [{'id':116,'name':'NCAA','seasons':[{'season':'2026-2027'}]}, {'id':999,'name':'NCAA','seasons':[{'season':'2026-2027'}]}])
        self.add('ncaab-men')
        self.assertNotIn('ncaab-men', sports.schedule_api_request_plan(self.db)['dataset_ids'])

    def test_coverage_refresh_is_selected_only_cached_and_secret_errors_are_redacted(self):
        self.add('nhl')
        opener=MagicMock()
        opener.open.return_value=Response({'response':[{'id':57,'name':'NHL','seasons':[{'season':2026}]}]})
        with patch.object(coverage.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(coverage.refresh_coverage(self.db,api_key='test-secret',now=self.now), [])
            self.assertEqual(coverage.refresh_coverage(self.db,api_key='test-secret',now=self.now), [])
        self.assertEqual(opener.open.call_count,1)
        self.assertEqual(opener.open.call_args.args[0].full_url,'https://v1.hockey.api-sports.io/leagues')
        opener.open.side_effect=ValueError('test-secret leaked by server')
        with patch.object(coverage.urllib.request,'build_opener',return_value=opener):
            warnings=coverage.refresh_coverage(self.db,api_key='test-secret',now=self.now.replace(day=20))
        self.assertNotIn('test-secret',json.dumps(warnings))
        self.assertIsNotNone(coverage.catalogue(self.db,'hockey')[0])

    def test_hockey_fetch_populates_cache_and_canonical_events_with_league_filter(self):
        self.add('nhl')
        sports.update_schedule_api_config(self.db,enabled=True,api_key='test-secret')
        dataset=sports.SCHEDULE_API_DATASETS['nhl']
        game={'id':447052,'date':'2026-10-08T19:00:00-04:00','league':{'id':57},'status':{'short':'NS','long':'Not Started'},
              'teams':{'home':{'id':1,'name':'Philadelphia Flyers'},'away':{'id':2,'name':'Boston Bruins'}}}
        irrelevant=dict(game,id=123,league={'id':58})
        with patch.object(schedule_api_requests,'_open_api_nfl',return_value=Response({'response':[game,irrelevant]})) as open_request:
            result=sports._fetch_schedule_api_dataset_date(self.db,dataset=dataset,api_key='test-secret',schedule_date=date(2026,10,8),season=2026,timezone='America/New_York',fetched_on='2026-10-08')
        self.assertEqual(result['games'],1)
        self.assertNotIn('league=',open_request.call_args.args[0].full_url)
        self.assertNotIn('season=',open_request.call_args.args[0].full_url)
        events=sports.schedule_api_events_for_window(self.db,self.now)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['api_dataset'],'nhl')
        self.assertEqual(events[0]['home_name'],'Philadelphia Flyers')
        self.assertEqual(sports._schedule_api_dataset_season(dataset,self.now.replace(month=4)),2025)

        sports.update_settings(self.db, {'enabled':True})
        import core
        channels=core.parse_m3u_text('#EXTM3U\n#EXTINF:-1 tvg-id="" tvg-name="" group-title="NHL",(NHL 12) | Boston Bruins @ Philadelphia Flyers (2026-10-08 19:00:00)\nhttps://stream.example/game.ts\n')
        result=sports.scan_channels(self.db,channels,now=self.now)
        self.assertGreater(result['count'],0)
        self.assertEqual(result['scan_metrics']['schedule_api_events'],1)

    def test_refresh_endpoint_runs_forced_schedule_and_matching_cycle(self):
        from flask import Flask
        import core
        from api.sports_routes import register_sports_routes
        app=Flask(__name__)
        register_sports_routes(app)
        with patch.object(core,'run_sports_scan',return_value={'schedule_api':{'warning':''},'count':1}) as scan, \
             patch.object(core,'enrich_sports_status',return_value={}), patch.object(sports,'status_payload',return_value={}), \
             patch.object(sports,'schedule_api_status_payload',return_value={'apis':[{'id':'nhl'}]}), \
             patch.object(core,'combined_channels_for_api',return_value=[{'id':'matched'}]), patch.object(core,'selected_ids_payload',return_value=[]):
            response=app.test_client().post('/api/sports/schedule-api/refresh')
        self.assertEqual(response.status_code,200)
        scan.assert_called_once_with(trigger='manual',force_api_refresh=True)
        self.assertEqual(response.get_json()['channels'],[{'id':'matched'}])
        self.assertIn('sports',response.get_json())

    def test_standard_products_request_date_only_and_filter_separate_local_caches(self):
        from urllib.parse import urlparse,parse_qs
        from sports import schedule_api
        self.store('basketball',[{'id':12,'name':'NBA','seasons':[{'season':'2026-2027'},{'season':'2025-2026'}]}])
        dataset=coverage.available_datasets(self.db,self.now)['nba']
        arguments=dict(schedule_date=date(2026,10,8),season=2026,timezone='America/New_York')
        query=parse_qs(urlparse(schedule_api._schedule_api_dataset_games_url(dataset,**arguments)).query)
        self.assertEqual(query,{'date':['2026-10-08'],'timezone':['America/New_York']})
        self.assertEqual(json.loads(schedule_api._schedule_api_request_key(dataset,**arguments))['parameters']['season'],'2026')
        hockey=sports.SCHEDULE_API_DATASETS['nhl']
        self.assertEqual(parse_qs(urlparse(schedule_api._schedule_api_dataset_games_url(hockey,**arguments)).query),query)
        self.assertNotEqual(schedule_api._schedule_api_request_key(dataset,**arguments),schedule_api._schedule_api_request_key(hockey,**arguments))
        arguments['season']=2025
        self.assertEqual(parse_qs(urlparse(schedule_api._schedule_api_dataset_games_url(dataset,**arguments)).query),query)

    def test_standard_game_plan_errors_are_visible_without_disclosing_key(self):
        error={'plan':'Free plans do not have access to this season, try from 2022 to 2024. key=test-secret'}
        with patch.object(schedule_api_requests,'_open_api_nfl',return_value=Response({'errors':error,'response':[]})):
            with self.assertRaises(ValueError) as caught:
                sports._fetch_schedule_api_dataset_date(self.db,dataset=sports.SCHEDULE_API_DATASETS['nba'],api_key='test-secret',schedule_date=date(2026,10,8),season=2026,timezone='America/New_York',fetched_on='2026-10-08')
        self.assertIn('Free plans do not have access',str(caught.exception))
        self.assertNotIn('test-secret',str(caught.exception));self.assertIn('[redacted]',str(caught.exception))



if __name__ == '__main__': unittest.main()
