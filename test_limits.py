import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error

from accounts import identity
from core import Store
from limits import LimitError, LimitMonitor, claude_limits, codex_limits, fetch_json, grok_limits, instant, window
from server import create_server


class NormalizationTests(unittest.TestCase):
    def test_codex_prefers_all_current_buckets_and_keeps_credit_units(self):
        payload = {'rateLimits': {'primary': {'usedPercent': 99}}, 'rateLimitsByLimitId': {
            'codex': {'primary': {'usedPercent': 73, 'windowDurationMins': 300, 'resetsAt': 2000},
                      'secondary': {'usedPercent': 100, 'windowDurationMins': 10080},
                      'credits': {'balance': '47629.2792474999'}},
            'spark': {'limitName': 'Spark', 'primary': {'usedPercent': 20, 'windowDurationMins': 300}}}}
        windows, credits = codex_limits(payload)
        self.assertEqual([w['remainingPercent'] for w in windows], [27, 0, 80])
        self.assertEqual([w['label'] for w in windows], ['5 horas', 'Semana', 'Spark · 5 horas'])
        self.assertEqual(credits[0]['unit'], 'credits')
        self.assertNotIn('usd', credits[0])

    def test_absence_is_not_zero_or_full_allowance(self):
        for payload in ({}, {'rateLimits': {'primary': {'usedPercent': None}}}):
            self.assertEqual(codex_limits(payload), ([], []))
        for bad in (None, True, float('nan'), float('inf'), -1, 'unknown'):
            self.assertIsNone(window('test', 'Uso', bad))
        self.assertEqual(window('test', 'Uso', 150)['remainingPercent'], 0)
        self.assertEqual(window('test', 'Uso', 0)['remainingPercent'], 100)

    def test_claude_missing_model_limits_remain_absent(self):
        rows, _ = claude_limits({'five_hour': {'utilization': 40, 'resets_at': '2026-10-02T18:00:00Z'},
                                  'seven_day': None, 'seven_day_opus': {'utilization': None}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['remainingPercent'], 60)
        self.assertIsNotNone(rows[0]['resetsAt'])
        self.assertIsNone(instant('2026-10-02T18:00:00'))

    def test_grok_only_computes_percent_with_known_cap_and_usage(self):
        rows, _ = grok_limits({'config': {'onDemandCap': {'val': 1000}, 'onDemandUsed': {'val': 300}}})
        self.assertEqual(rows[0]['remainingPercent'], 70)
        for config in ({}, {'onDemandCap': {'val': 0}, 'onDemandUsed': {'val': 1}},
                       {'onDemandCap': {'val': 1000}}, {'creditUsagePercent': -3}):
            self.assertEqual(grok_limits({'config': config}), ([], []))

    def test_http_errors_never_echo_credentials_or_provider_body(self):
        opener = Mock()
        for code, status in ((401, 'loginRequired'), (403, 'loginRequired'), (429, 'rateLimited'), (500, 'unavailable')):
            opener.open.side_effect = urllib.error.HTTPError('https://example.test', code, 'secret', {}, io.BytesIO(b'SECRET'))
            with patch('limits.urllib.request.build_opener', return_value=opener), self.assertRaises(LimitError) as caught:
                fetch_json('https://example.test', 'PRIVATE-TOKEN')
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertNotIn('PRIVATE-TOKEN', str(caught.exception))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'profile', self.root / 'home')
        self.monitor = self.store.limits
        self.account = identity('OpenAI', 'test:person', None, 'pro', '')
        self.claude = identity('Anthropic', 'person:organization', None, 'Max', '')
        self.clock = patch('limits.time.time', return_value=1000).start()
        self.detect = patch('limits.detect_accounts', return_value=[self.account]).start()
        self.fetch = patch.object(self.monitor, 'fetch', return_value=([window('week', 'Semana', 25, 10000)], [])).start()

    def tearDown(self):
        patch.stopall()
        self.temp.cleanup()

    def enable(self):
        self.store.settings['liveLimitsEnabled'] = True

    def test_default_off_does_not_read_credentials_or_call_providers(self):
        self.monitor.request_refresh()
        self.monitor.refresh()
        self.fetch.assert_not_called()
        self.detect.assert_not_called()
        self.assertEqual(self.monitor.snapshot()['rows'], [])
        self.assertFalse(self.monitor.snapshot()['refreshing'])

    def test_manual_refresh_and_connect_do_not_bypass_rate_limit(self):
        self.enable()
        self.monitor.refresh()
        self.clock.return_value = 1301
        self.fetch.side_effect = LimitError('rateLimited', 'Aguarde')
        self.monitor.refresh()
        self.monitor.request_refresh(connect_claude=True)
        self.monitor.refresh()
        self.assertEqual(self.fetch.call_count, 2)
        row = self.monitor.snapshot()['rows'][0]
        self.assertTrue(row['stale'])
        self.assertEqual(row['windows'][0]['remainingPercent'], 75)
        self.assertEqual(row['lastSuccess'], 1000)

    def test_elapsed_reset_marks_stale_without_inventing_new_allowance(self):
        self.enable()
        self.fetch.return_value = ([window('week', 'Semana', 100, 1050)], [])
        self.monitor.refresh()
        self.clock.return_value = 1051
        row = self.monitor.snapshot()['rows'][0]
        self.assertTrue(row['stale'])
        self.assertEqual(row['windows'][0]['remainingPercent'], 0)

    def test_toggle_does_not_bypass_cooldown_or_expose_disabled_balances(self):
        self.enable()
        self.fetch.side_effect = LimitError('rateLimited', 'Aguarde')
        self.monitor.refresh()
        self.store.settings['liveLimitsEnabled'] = False
        self.monitor.refresh()
        self.assertEqual(self.monitor.snapshot()['rows'], [])
        self.enable()
        self.monitor.request_refresh()
        self.monitor.refresh()
        self.assertEqual(self.fetch.call_count, 1)

    def test_account_rotation_discards_old_results_even_when_fetch_fails(self):
        self.enable()
        self.monitor.refresh()
        self.clock.return_value = 1301
        self.detect.side_effect = [[self.account], []]
        self.fetch.side_effect = LimitError('unavailable', 'Falha')
        self.monitor.refresh()
        self.assertEqual(self.monitor.snapshot()['rows'], [])

    def test_disabling_during_a_request_drops_its_result(self):
        self.enable()
        def stop_query(*args):
            self.store.settings['liveLimitsEnabled'] = False
            return [window('week', 'Semana', 10)], []
        self.fetch.side_effect = stop_query
        self.monitor.refresh()
        self.assertEqual(self.monitor.snapshot()['rows'], [])

    def test_expired_login_clears_cached_balances(self):
        self.enable()
        self.monitor.refresh()
        self.clock.return_value = 1301
        self.fetch.side_effect = LimitError('loginRequired', 'Entre novamente')
        self.monitor.refresh()
        row = self.monitor.snapshot()['rows'][0]
        self.assertEqual(row['windows'], [])
        self.assertIsNone(row['lastSuccess'])

    def test_custom_claude_profile_never_borrows_default_keychain(self):
        with patch.dict('os.environ', {'MUR_NATIVE_EXECUTABLE': '/fake/MUR'}), patch('limits.subprocess.run') as native:
            with self.assertRaises(LimitError):
                self.monitor.claude_token(self.root / 'other-claude', True)
            native.assert_not_called()

    def test_denied_keychain_access_waits_for_explicit_connection(self):
        source = self.store.home / '.claude'
        credential = {'claudeAiOauth': {'accessToken': 'SYNTHETIC-TOKEN', 'expiresAt': 100000}}
        with patch.dict('os.environ', {'MUR_NATIVE_EXECUTABLE': '/fake/MUR'}), patch('limits.subprocess.run') as native:
            native.return_value = Mock(returncode=1, stdout=b'')
            for _ in range(3):
                with self.assertRaises(LimitError) as caught:
                    self.monitor.claude_token(source, False)
                self.assertEqual(caught.exception.status, 'connectionRequired')
            native.assert_not_called()
            with self.assertRaises(LimitError):
                self.monitor.claude_token(source, True)
            self.assertEqual(native.call_args.args[0][-1], 'interactive')
            with self.assertRaises(LimitError):
                self.monitor.claude_token(source, False)
            self.assertEqual(native.call_count, 1)
            native.return_value = Mock(returncode=0, stdout=json.dumps(credential).encode())
            self.assertEqual(self.monitor.claude_token(source, True), 'SYNTHETIC-TOKEN')
            self.assertEqual(self.monitor.claude_token(source, False), 'SYNTHETIC-TOKEN')
            self.assertEqual(native.call_args.args[0][-1], 'silent')

    def test_keychain_timeout_waits_for_explicit_connection(self):
        source = self.store.home / '.claude'
        self.monitor.claude_keychain_connected = True
        with patch.dict('os.environ', {'MUR_NATIVE_EXECUTABLE': '/fake/MUR'}), patch('limits.subprocess.run') as native:
            import subprocess
            native.side_effect = subprocess.TimeoutExpired('/fake/MUR', 5)
            for _ in range(2):
                with self.assertRaises(LimitError):
                    self.monitor.claude_token(source, False)
            self.assertEqual(native.call_count, 1)

    def test_claude_identity_must_match_before_usage_is_requested(self):
        with patch.object(self.monitor, 'claude_token', return_value='PRIVATE-TOKEN'), patch('limits.fetch_json') as request:
            request.return_value = {'account': {'uuid': 'someone-else'}, 'organization': {'uuid': 'organization'}}
            with self.assertRaises(LimitError):
                LimitMonitor.fetch(self.monitor, self.claude, False)
            self.assertEqual(request.call_count, 1)
            request.side_effect = [{'account': {'uuid': 'person'}, 'organization': {'uuid': 'organization'}},
                                   {'seven_day': {'utilization': 80}}]
            rows, _ = LimitMonitor.fetch(self.monitor, self.claude, False)
            self.assertEqual(rows[0]['remainingPercent'], 20)
        self.assertNotIn('PRIVATE-TOKEN', json.dumps(self.monitor.snapshot()))
        self.assertNotIn('PRIVATE-TOKEN', self.store.settings_path.read_text())


class LimitHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'profile', root / 'home')
        self.server = create_server(self.store, 0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.token = json.loads(self.request('/api/state')[1])['csrf']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        connection.request('POST' if data is not None else 'GET', path,
                           json.dumps(data) if data is not None else None, headers or {})
        response = connection.getresponse()
        status, body = response.status, response.read()
        connection.close()
        return status, body

    def test_opt_in_bool_and_origin_are_required(self):
        headers = {'X-Local-Token': self.token, 'Content-Type': 'application/json'}
        self.assertEqual(self.request('/api/limits/refresh', {})[0], 403)
        self.assertEqual(self.request('/api/limits/refresh', {}, headers)[0], 400)
        self.assertEqual(self.request('/api/settings', {'liveLimitsEnabled': 'true'}, headers)[0], 400)
        self.assertEqual(self.request('/api/settings', {'liveLimitsEnabled': True}, headers | {'Origin': 'https://other.test'})[0], 403)
        self.assertEqual(self.request('/api/settings', {'liveLimitsEnabled': True}, headers)[0], 200)
        self.assertEqual(self.request('/api/limits/refresh', {'connectClaude': 'yes'}, headers)[0], 400)
        self.assertEqual(self.request('/api/limits/refresh', {}, headers)[0], 202)
        self.assertEqual(json.loads(self.request('/api/limits')[1])['rows'], [])


if __name__ == '__main__':
    unittest.main()
