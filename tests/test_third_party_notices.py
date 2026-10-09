import importlib.metadata as metadata
import json
from pathlib import Path
import unittest
import zipfile
import importlib.util
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ThirdPartyNoticesTests(unittest.TestCase):
    def test_built_inventory_covers_installed_dependencies_and_local_documents(self):
        folder = ROOT / 'static/licenses/generated'
        if not (folder / 'index.json').exists():
            self.skipTest('Generated inventory is verified inside the built Docker image')
        data = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
        python = {row['name'].lower(): row for row in data['entries'] if row['group'] == 'Python libraries'}
        for dist in metadata.distributions():
            row = python[dist.metadata['Name'].lower()]
            self.assertEqual(row['version'], dist.version)
            self.assertTrue(row['notices'])
        with zipfile.ZipFile(folder / 'third-party-notices.zip') as bundle:
            self.assertIsNone(bundle.testzip())
            for row in data['entries']:
                self.assertTrue(row['notices'], row['name'])
                for url in row['notices']:
                    name = url.removeprefix('/static/licenses/generated/')
                    self.assertEqual(bundle.read(name), (folder / name).read_bytes())
                    self.assertGreater(len(bundle.read(name)), 10)
            for name in ('upstream/bootstrap-5.3.3.txt', 'upstream/popper-2.11.8.txt',
                         'runtime/python-LICENSE.txt', 'python/http-ece-LICENSE.txt',
                         'common/GPL-2.txt', 'artwork/leagues-sources.json', 'build/ffmpeg-build.txt'):
                self.assertIn(name, bundle.namelist())
            self.assertNotIn('config.json', bundle.namelist())
            self.assertFalse(any(name.endswith(('.conf', '.db')) for name in bundle.namelist()))
        binaries = {row['name'].split(':')[0]: row for row in data['entries'] if row['group'] == 'Debian packages'}
        for name in ('ffmpeg', 'comskip'):
            self.assertIn('https://sources.debian.org/src/', binaries[name]['source'])
            self.assertTrue(binaries[name]['notices'][0].endswith('-copyright.txt'))

    def test_settings_has_local_notices_and_distinguishes_artwork_rights(self):
        main = (ROOT / 'templates/index.html').read_text(encoding='utf-8')
        panel = (ROOT / 'templates/_credits.html').read_text(encoding='utf-8')
        self.assertIn("{% include '_credits.html' %}", main)
        self.assertLess(main.index("{% include '_credits.html' %}"), main.index('/static/js/ui_sidebar.js'))
        self.assertIn('third-party-notices.zip', panel)
        self.assertIn('attribution does not grant a reuse license', panel)
        self.assertIn('/static/icons/hockey/sources.json', panel)

    def test_installer_credits_exit_without_installing(self):
        path = ROOT / 'installer/docker/install.py'
        spec = importlib.util.spec_from_file_location('credits_installer', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch('sys.argv', ['installer', '--credits']), patch.object(module, 'install') as install, patch('builtins.print') as output:
            self.assertEqual(module.main(), 0)
        install.assert_not_called()
        self.assertTrue(output.called)

    def test_installer_notice_packaging_preserves_text_and_rejects_missing_license(self):
        spec = importlib.util.spec_from_file_location('installer_notices', ROOT / 'installer/docker/notices.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'COPYING.txt').write_text('Exact bootloader notice and exception', encoding='utf-8')
            (base / 'LICENSE.txt').write_text('Exact Python license', encoding='utf-8')
            dist = SimpleNamespace(version='6.16.0', files=[Path('COPYING.txt')], locate_file=lambda path: base / path)
            with patch.object(module.metadata, 'distribution', return_value=dist), patch.object(module.sys, 'base_prefix', folder):
                target = base / 'notices.txt'
                module.write_notices(target)
                self.assertIn('Exact bootloader notice and exception', target.read_text(encoding='utf-8'))
                self.assertIn('Exact Python license', target.read_text(encoding='utf-8'))
                dist.files = []
                with self.assertRaisesRegex(RuntimeError, 'PyInstaller license'):
                    module.write_notices(target)


if __name__ == '__main__':
    unittest.main()
