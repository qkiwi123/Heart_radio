"""Publishing boundaries: never publish private history/data or dirty source."""
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import export_public_repo as public


class PublicExportTests(unittest.TestCase):
    def test_all_dashboard_scripts_and_styles_are_in_the_public_package(self):
        class Assets(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'script' and 'src' in attrs:
                    paths.append(attrs['src'])
                if tag == 'link' and attrs.get('rel') == 'stylesheet':
                    paths.append(attrs['href'])
        manifest = json.loads((public.ROOT / 'publish/manifest.json').read_text(encoding='utf-8'))
        exported = set(manifest['sources']) | manifest['overrides'].keys()
        paths = []
        Assets().feed((public.ROOT / 'dashboard/static/index.html').read_text(encoding='utf-8'))
        self.assertTrue(paths)
        for path in paths:
            self.assertIn('dashboard/static/' + path.lstrip('/'), exported)

    def test_manifest_includes_watch_production_sources(self):
        manifest = json.loads((public.ROOT / 'publish/manifest.json').read_text(encoding='utf-8'))
        exported = set(manifest['sources']) | manifest['overrides'].keys()
        production = {path.relative_to(public.ROOT).as_posix()
                      for path in (public.ROOT / 'app/src/main').rglob('*.kt')}
        self.assertEqual(production - exported, set())

    def test_protected_source_hashes_accept_checkout_line_endings(self):
        source = b'first line\nsecond line\n'
        expected = hashlib.sha256(source).hexdigest()
        self.assertEqual(public.source_digest(source), expected)
        self.assertEqual(public.source_digest(source.replace(b'\n', b'\r\n')), expected)
        self.assertNotEqual(public.source_digest(source + b'changed'), expected)

    def test_private_and_generated_paths_are_always_rejected(self):
        for name in ['runs/ledger.json','evidence/personal.txt','.local-wifi/config.json','.idea/misc.xml',
                     'app/build/app.apk','dist/mcp/runtime.json','local.properties','private.key','app/src/test/real.csv',
                     '../escape.py','/absolute.py','a\\b.py']:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):public.scan({name:b'content'})

    def test_generated_fixture_and_normal_source_are_allowed(self):
        public.scan({'app/src/test/resources/synthetic-session.csv':b'# synthetic\n', 'dashboard/server.py':b'print(1)\n'})

    def test_credential_check_does_not_print_secret(self):
        secret=b'gh'+b'p_'+b'A'*40
        with self.assertRaises(ValueError) as error:public.scan({'source.py':secret})
        self.assertNotIn(secret.decode(),str(error.exception))

    def test_only_private_worktree_changes_can_remain_uncommitted(self):
        rows=b' M .idea/misc.xml\0?? .idea/vcs.xml\0 M TEST-REPORT.md\0'
        with patch.object(public,'git',return_value=rows):
            self.assertEqual(public.private_worktree_changes(),['.idea/misc.xml','.idea/vcs.xml','TEST-REPORT.md'])
        for rows in [b' M dashboard/server.py\0',b'?? scripts/new.py\0',b' M publish/manifest.json\0']:
            with patch.object(public,'git',return_value=rows):
                with self.assertRaises(ValueError):public.private_worktree_changes()

    def test_rename_checks_original_path_as_well_as_destination(self):
        with patch.object(public,'git',return_value=b'R  .idea/saved.xml\0dashboard/server.py\0'):
            with self.assertRaises(ValueError):public.private_worktree_changes()

    def test_link_outside_source_root_cannot_be_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'root';root.mkdir();outside=Path(folder)/'outside.py';outside.write_text('private')
            with patch.object(public,'ROOT',root):
                with self.assertRaises(ValueError):public.checked_bytes('../outside.py')


if __name__=='__main__':unittest.main()
