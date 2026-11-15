from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

import database
import schema_cache
import sports


class SchemaReadTests(unittest.TestCase):
    def test_reads_work_while_another_connection_holds_write_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.db'
            database.connect(path).close()
            sports.init_db(path)
            with sqlite3.connect(path) as writer:
                writer.execute('BEGIN IMMEDIATE')
                writer.execute("UPDATE sports_settings SET value=value WHERE key='enabled'")
                # Neither established read path should try schema writes again.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    def read():
                        conn = database.connect(path)
                        conn.close()
                        return sports.get_settings(path), sports.generated_rows(path)
                    settings, rows = pool.submit(read).result(timeout=3)
                self.assertIn('enabled', settings)
                self.assertEqual(rows, [])

    def test_replaced_database_gets_its_schema_and_failed_setup_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.db'
            database.connect(path).close()
            replacement = Path(directory) / 'new.db'
            sqlite3.connect(replacement).close()
            replacement.replace(path)
            conn = database.connect(path)
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='selections'").fetchone())
            callback = Mock(side_effect=[RuntimeError('failed'), None])
            with self.assertRaises(RuntimeError):
                schema_cache.ensure_schema(conn, path, 'retry', callback)
            schema_cache.ensure_schema(conn, path, 'retry', callback)
            schema_cache.ensure_schema(conn, path, 'retry', callback)
            self.assertEqual(callback.call_count, 2)
            conn.close()
