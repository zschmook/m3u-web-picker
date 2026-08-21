from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core


class _Response:
    def __init__(self):
        self.fp = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return b"still downloading"


class PublicEpgTimeoutTests(unittest.TestCase):
    def test_public_epg_download_has_overall_deadline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "epg-us.xml.gz"
            clock = iter([0.0, 1.0, 181.0])

            with patch.object(core, "public_epg_cache_path", return_value=destination), patch.object(
                core, "public_epg_url", return_value="https://example.test/epg.xml.gz"
            ), patch.object(core.urllib.request, "urlopen", return_value=_Response()) as open_url, patch.object(
                core.time, "monotonic", side_effect=lambda: next(clock)
            ), patch.object(core, "save_config"):
                ok, message = core.refresh_public_epg_source("US", force=True)

            self.assertFalse(ok)
            self.assertIn("180-second time limit", message)
            self.assertEqual(
                open_url.call_args.kwargs["timeout"],
                core.PUBLIC_EPG_SOCKET_TIMEOUT_SECONDS,
            )
            self.assertFalse(destination.with_name(destination.name + ".tmp").exists())
