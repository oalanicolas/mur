import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core import Store, process_present
from limits import read_codex
from windows.main import ExportAPI


class WindowsSupportTests(unittest.TestCase):
    def test_process_observation_never_terminates_an_agent(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            for _ in range(3):
                self.assertTrue(process_present(process.pid))
                self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(5)
        self.assertFalse(process_present(process.pid))

    def test_codex_protocol_works_with_pipe_reader_on_both_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'rpc.py'
            script.write_text("import sys,json\nfor line in sys.stdin:\n request=json.loads(line)\n if 'id' in request:\n  print(json.dumps({'id':request['id'],'result':{'rateLimits':{'primary':{'usedPercent':27}}}}),flush=True)\n", encoding='utf-8')
            launch = subprocess.Popen
            def rpc_process(args, **kwargs):
                return launch([sys.executable, str(script)], **kwargs)
            with patch('limits.codex_binary', return_value=str(sys.executable)), patch('limits.subprocess.Popen', side_effect=rpc_process):
                payload = read_codex(root, root / '.codex')
            self.assertEqual(payload['rateLimits']['primary']['usedPercent'], 27)

    def test_empty_profile_and_unicode_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / 'profile', root / 'home')
            store.save_settings(dict(store.settings, machineLabel='Computador de João 日本語'))
            reopened = Store(root / 'profile', root / 'home')
            self.assertEqual(reopened.settings['machineLabel'], 'Computador de João 日本語')
            self.assertFalse(reopened.settings['liveLimitsEnabled'])
            self.assertEqual(reopened.state()['counts']['events'], 0)

    def test_export_bridge_rejects_foreign_pages_paths_and_tokens(self):
        from unittest.mock import Mock
        view = Mock(token='SYNTHETIC-TOKEN')
        api = ExportAPI('http://127.0.0.1:4317')
        api._window = Mock()
        api._window.get_current_url.return_value = 'http://127.0.0.1:4317/#sessions'
        with patch.dict(sys.modules, {'webview': view}):
            for path, token in [('https://example.test/api/export', view.token), ('//example.test/api/export', view.token),
                                ('/api/state', view.token), ('/api/export#fragment', view.token), ('/api/export', 'WRONG')]:
                with self.assertRaises(ValueError):
                    api.save_export(path, token)
            api._window.get_current_url.return_value = 'https://example.test'
            with self.assertRaises(ValueError):
                api.save_export('/api/export', view.token)
        api._window.create_file_dialog.assert_not_called()


if __name__ == '__main__':
    unittest.main()
