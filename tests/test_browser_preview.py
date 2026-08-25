import unittest
from unittest.mock import Mock, patch

from media import browser


class BrowserPreviewTests(unittest.TestCase):
    def setUp(self):
        browser._PREVIEW_PROCESSES.clear()

    def tearDown(self):
        browser._PREVIEW_PROCESSES.clear()

    def test_stop_preview_terminates_registered_process(self):
        process = Mock()
        browser._PREVIEW_PROCESSES["preview-one"] = process

        with patch("media.browser.terminate") as terminate:
            self.assertTrue(browser.stop_preview("preview-one"))

        terminate.assert_called_once_with(process)
        self.assertNotIn("preview-one", browser._PREVIEW_PROCESSES)

    def test_stop_preview_is_idempotent(self):
        with patch("media.browser.terminate") as terminate:
            self.assertFalse(browser.stop_preview("missing"))
            self.assertFalse(browser.stop_preview(""))

        terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
