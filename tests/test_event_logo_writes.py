"""Concurrent logo requests publish whole files without sharing staging paths."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import event_logos


class EventLogoWriteTests(unittest.TestCase):
    def test_concurrent_writers_publish_complete_payloads_without_leftovers(self):
        for text_mode in (False, True):
            with self.subTest(text_mode=text_mode), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / ('event.json' if text_mode else 'event.png')
                payloads = [(str(index) + '—' * 1000) if text_mode else bytes([index]) * 65536
                            for index in range(8)]
                barrier = threading.Barrier(len(payloads))
                replace = Path.replace
                publishing = set()
                guard = threading.Lock()

                def publish(source, destination):
                    if destination == target:
                        with guard:
                            first_attempt = source not in publishing
                            publishing.add(source)
                        if first_attempt:
                            barrier.wait(timeout=10)
                    return replace(source, destination)

                write = event_logos._atomic_write_text if text_mode else event_logos._atomic_write_bytes
                with patch.object(Path, 'replace', publish), ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(write, target, payload) for payload in payloads]
                    for future in futures:
                        future.result(timeout=15)
                expected = [payload.encode('utf-8') for payload in payloads] if text_mode else payloads
                self.assertIn(target.read_bytes(), expected)
                self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_failed_publish_preserves_existing_cache_and_removes_its_temp_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'event.png'
            target.write_bytes(b'previous logo')
            with patch.object(Path, 'replace', side_effect=PermissionError('publish failed')):
                with self.assertRaises(PermissionError):
                    event_logos._atomic_write_bytes(target, b'replacement logo')
            self.assertEqual(target.read_bytes(), b'previous logo')
            self.assertEqual(list(Path(folder).iterdir()), [target])


if __name__ == '__main__':
    unittest.main()
