import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('push_descriptions', Path(__file__).parents[1] / 'tools/push_descriptions.py')
pd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pd)


class PushTests(unittest.TestCase):
    def test_preview_default_and_content_never_in_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changes.json'
            path.write_text('[{"key":"x","description_vi":"$(touch bad)"}]', encoding='utf-8')
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                return '/home/ubuntu/ctv-bot/description-ops/batch-abc' if 'mktemp' in command[-1] else ''
            with patch.object(pd, 'run', side_effect=run):
                self.assertEqual(pd.main(['--changes', str(path)]), 0)
            remote = calls[-1][-1]
            self.assertNotIn('--execute', remote)
            self.assertNotIn('touch bad', remote)
            self.assertIn('bulk_descriptions.py', remote)
            self.assertIn('StrictHostKeyChecking=yes', calls[0])
            self.assertTrue(any(c[0] == 'scp' for c in calls))

    def test_execute_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'changes.json'
            path.write_text('[{"key":"x","description_en":null}]')
            calls=[]
            def run(command, **kwargs):
                calls.append(command)
                return '/home/ubuntu/ctv-bot/description-ops/batch-abc' if 'mktemp' in command[-1] else ''
            with patch.object(pd,'run',side_effect=run):
                self.assertEqual(pd.main(['--changes',str(path),'--execute']),0)
            self.assertIn('--execute',calls[-1][-1])
            self.assertIn('--backup',calls[-1][-1])
            self.assertIn('--receipt',calls[-1][-1])

    def test_invalid_payload_never_connects(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.json'
            path.write_text('[]')
            with patch.object(pd,'run') as run:
                self.assertEqual(pd.main(['--changes',str(path)]),1)
                run.assert_not_called()

    def test_reject_untrusted_batch_path(self):
        with self.assertRaises(ValueError):
            pd.validate_batch('/tmp/evil; touch /tmp/x')

    def test_invalid_host_and_controls_never_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changes.json'
            for content, extra in [
                ('[{"key":"x","description_vi":"ok"}]', ['--host', 'user@host;id']),
                ('[{"key":"x","description_vi":"bad\\u0000text"}]', []),
                ('[{"key":"x","description_vi":"a","description_vi":"b"}]', []),
            ]:
                path.write_text(content, encoding='utf-8')
                with self.subTest(content=content, extra=extra), patch.object(pd, 'run') as run:
                    self.assertEqual(pd.main(['--changes', str(path), *extra]), 1)
                    run.assert_not_called()

    def test_upload_uses_frozen_validated_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changes.json'
            path.write_text('[{"key":"x","description_en":"approved"}]')
            uploaded = []
            def run(command):
                if 'mktemp' in command[-1]:
                    path.write_text('[{"key":"x","description_en":"replaced"}]')
                    return pd.OPS + '/batch-abc'
                if command[0] == 'scp':
                    uploaded.append(pd.bp.read_json(Path(command[-2])))
                return ''
            with patch.object(pd, 'run', side_effect=run):
                self.assertEqual(pd.main(['--changes', str(path)]), 0)
            self.assertEqual(uploaded, [[{'key': 'x', 'description_en': 'approved'}]])

if __name__ == '__main__':
    unittest.main()
