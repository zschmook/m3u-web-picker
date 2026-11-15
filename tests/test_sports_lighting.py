from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sports_lighting


class SportsLightingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'lighting.db'
        database = patch.object(sports_lighting.core, 'DB_PATH', self.path)
        database.start(); self.addCleanup(database.stop)
        self.team = {'id': 'ncaaf-fbs:penn-state', 'name': 'Penn State', 'aliases': ['Penn State Nittany Lions'], 'league_id': 'ncaaf-fbs'}
        catalog = patch.object(sports_lighting, 'teams', return_value=[self.team])
        catalog.start(); self.addCleanup(catalog.stop)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('CREATE TABLE sports_generated (assigned_number INTEGER, event_title TEXT, display_name TEXT, league_id TEXT)')
            connection.execute('INSERT INTO sports_generated VALUES (3000, ?, ?, ?)', ('USC at Penn State', 'USC at Penn State', 'ncaaf-fbs'))
        self.settings = {'id': 'living-room', 'enabled': True, 'brightness': 10, 'paused_brightness': 100}

    def test_selected_team_matches_and_supplies_color(self):
        sports_lighting.save('living-room', [{'id': self.team['id'], 'color': '#0000ff'}])
        result = sports_lighting.apply(self.settings, '/guide/play/sports/3000')
        self.assertTrue(result['enabled'])
        self.assertEqual(result['sports_hsv'], {'hue': 240, 'saturation': 100})
        self.assertEqual(result['paused_brightness'], 100)

    def test_unmatched_game_disables_lighting(self):
        sports_lighting.save('living-room', [{'id': self.team['id'], 'color': '#ffffff'}])
        self.assertFalse(sports_lighting.apply(self.settings, '/guide/play/sports/4000')['enabled'])

    def test_movies_and_no_selection_keep_existing_rules(self):
        self.assertEqual(sports_lighting.apply(self.settings, '/guide/play/sports/3000'), self.settings)
        sports_lighting.save('living-room', [{'id': self.team['id'], 'color': '#ffffff'}])
        self.assertEqual(sports_lighting.apply(self.settings, '/guide/play/movies/3000'), self.settings)

    def test_wrong_league_does_not_match_same_name(self):
        sports_lighting.save('living-room', [{'id': self.team['id'], 'color': '#ffffff'}])
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE sports_generated SET league_id='ncaam'")
        self.assertFalse(sports_lighting.apply(self.settings, '/guide/play/sports/3000')['enabled'])

    def test_regular_nbc_channel_matches_current_epg_game(self):
        sports_lighting.save('living-room', [{'id': self.team['id'], 'color': '#041e42'}])
        result = sports_lighting.apply(self.settings, '/guide/play/manual/wgal', True,
                                      {'title': 'College Football : USC at Penn State'})
        self.assertTrue(result['enabled'])
        self.assertEqual(result['sports_team'], 'Penn State')
        unmatched = sports_lighting.apply(self.settings, '/guide/play/manual/wgal', True,
                                          {'title': 'College Football : Texas at Oklahoma'})
        self.assertFalse(unmatched['enabled'])

    def test_invalid_colors_and_unknown_teams_rejected(self):
        for value in ({'id': self.team['id'], 'color': 'blue'}, {'id': 'unknown', 'color': '#ffffff'}):
            with self.assertRaises(ValueError): sports_lighting.save('living-room', [value])
