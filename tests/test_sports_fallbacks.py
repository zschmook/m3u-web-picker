import unittest
from contextlib import ExitStack
from unittest.mock import patch
from channel_availability import AvailabilityCache
import core
import sports
from sports import generated

class SportsFallbackTests(unittest.TestCase):
    def test_primary_outage_reaches_matcher_with_cached_and_fallback_data(self):
        primary = {'role':'primary','id':'p','priority':0,'last_error':'offline'}
        backup = {'role':'fallback','id':'b','priority':1}
        with ExitStack() as stack:
            stack.enter_context(patch.multiple(core, channels=[{'url':'old'}], source_mode='url', provider_sources=[primary,backup], epg_sources=[], public_epg_enabled_codes=[]))
            stack.enter_context(patch('core.sports.get_settings', return_value={'enabled':True}))
            stack.enter_context(patch('core.refresh_master_from_url', return_value=(False,'offline')))
            stack.enter_context(patch('core.refresh_provider_source', return_value=(True,'ok',[{'url':'backup'}])))
            stack.enter_context(patch('core._load_provider_cache', return_value=[{'url':'backup'}]))
            stack.enter_context(patch('core.provider_xmltv_url', return_value=''))
            stack.enter_context(patch('core._valid_xmltv_file', return_value=False))
            stack.enter_context(patch('core.active_base_epg_path', return_value=None))
            stack.enter_context(patch('core.active_public_epg_paths', return_value=[]))
            stack.enter_context(patch('core.configured_epg_fallback_paths', return_value=[]))
            stack.enter_context(patch('core.selected_xmltv_ids', return_value=set()))
            stack.enter_context(patch('core.selected_channels_from_selected_ids_in_order', return_value=[]))
            for name in ['begin_scan_state','update_scan_stage','record_scan_failure','finish_scan_state','refresh_schedule_api_if_due','discover_catalog_from_channels']:
                stack.enter_context(patch('core.sports.'+name, return_value={}))
            scan=stack.enter_context(patch('core.sports.scan_channels', side_effect=RuntimeError('matcher reached')))
            with self.assertRaises(core.SportsScanError):
                core.run_sports_scan()
            self.assertTrue(scan.call_args.kwargs['preserve_existing'])
            rows=scan.call_args.args[1]
            self.assertEqual({x['url'] for x in rows},{'old','backup'})
            self.assertGreater(rows[0]['_provider_priority'],rows[1]['_provider_priority'])

    def test_feed_selection_keeps_other_provider_as_playback_alternative(self):
        event={'source_channels':[{'url':'dead','name':'Event','_provider_priority':1000},{'url':'live','name':'Event','_provider_priority':1}]}
        feeds=sports._build_feeds(event, {}, {'feed_preference':'best'}, {})
        self.assertEqual(len(feeds),1)
        self.assertEqual(feeds[0]['stream_candidates'],['live','dead'])

    def test_playback_tries_alternatives_and_recovers(self):
        calls=[]
        def probe(url):
            calls.append(url)
            return url=='backup'
        cache=AvailabilityCache(probe=probe,ttl=0)
        self.addCleanup(cache.worker.shutdown)
        self.assertEqual(cache.resolve(['primary','backup']),'backup')
        self.assertEqual(calls,['primary','backup'])
        cache.probe=lambda url: False
        self.assertEqual(cache.resolve(['primary','backup']),'')
        cache.probe=lambda url: True
        self.assertEqual(cache.resolve(['primary','backup']),'primary')

    def test_partial_publish_keeps_missing_rows_and_numbers(self):
        old={'channel_key':'old','event_key':'event','assigned_number':1000,'url':'old','epg_programme':{}}
        new={'channel_key':'new','event_key':'event','assigned_number':1000,'url':'new','epg_programme':{},'tvg_id':'new-id','display_name':'Event','group_title':'Sports','feed_type':'event','subtitle':''}
        with patch('sports.generated.generated_rows',return_value=[old]):
            rows=generated.retain_partial_rows('unused',[new])
        self.assertEqual([r['assigned_number'] for r in rows],[1000,1001])
        self.assertEqual(generated.stream_candidates(rows[0]),['new','old'])

if __name__=='__main__': unittest.main()
