import datetime as dt
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from accounts import detect_accounts, identity, read_metadata


PROVIDER_NAMES = {'OpenAI': 'Codex', 'Anthropic': 'Claude', 'xAI': 'Grok'}


class LimitError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def instant(value):
    number = numeric(value)
    if number is not None:
        return number / 1000 if number > 1e11 else number
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def window(identifier, label, used, resets=None, minutes=None):
    used = numeric(used)
    if used is None or used < 0:
        return None
    used = min(100, used)
    return {'id': identifier, 'label': label[:100], 'usedPercent': used, 'remainingPercent': 100 - used,
            'resetsAt': instant(resets), 'windowDurationMins': numeric(minutes)}


def duration_label(minutes):
    minutes = numeric(minutes)
    if minutes == 10080:
        return 'Semana'
    if minutes and minutes >= 1440:
        return f'{minutes / 1440:g} dias'
    if minutes and minutes >= 60:
        return f'{minutes / 60:g} horas'
    return f'{minutes:g} minutos' if minutes else 'Período de uso'


def codex_limits(payload):
    buckets = payload.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict) or not buckets:
        single = payload.get('rateLimits')
        buckets = {single.get('limitId') or 'codex': single} if isinstance(single, dict) else {}
    windows, credits = [], []
    for identifier, bucket in list(buckets.items())[:20]:
        if not isinstance(bucket, dict):
            continue
        name = bucket.get('limitName')
        for key in ('primary', 'secondary'):
            value = bucket.get(key)
            if isinstance(value, dict):
                label = duration_label(value.get('windowDurationMins'))
                if isinstance(name, str) and name:
                    label = name[:50] + ' · ' + label
                item = window(str(identifier)[:80] + ':' + key, label, value.get('usedPercent'), value.get('resetsAt'), value.get('windowDurationMins'))
                if item:
                    windows.append(item)
        balance = bucket.get('credits')
        if isinstance(balance, dict):
            amount = numeric(balance.get('balance'))
            if amount is not None and amount >= 0:
                credits.append({'label': 'Créditos informados pelo Codex', 'balance': amount, 'unit': 'credits'})
    return windows, credits[:1]


def claude_limits(payload):
    windows = []
    labels = {'five_hour': ('5 horas', 300), 'seven_day': ('Semana', 10080),
              'seven_day_sonnet': ('Sonnet · semana', 10080), 'seven_day_opus': ('Opus · semana', 10080),
              'seven_day_oauth_apps': ('Aplicativos OAuth · semana', 10080)}
    for key, (label, minutes) in labels.items():
        value = payload.get(key)
        if isinstance(value, dict):
            item = window(key, label, value.get('utilization'), value.get('resets_at'), minutes)
            if item:
                windows.append(item)
    return windows, []


def grok_limits(payload):
    config = payload.get('config')
    if not isinstance(config, dict):
        return [], []
    period = config.get('currentPeriod') or {}
    reset = instant(period.get('end')) if isinstance(period, dict) else None
    reset = reset or instant(config.get('billingPeriodEnd'))
    used = numeric(config.get('creditUsagePercent'))
    if used is None:
        cap, spent = config.get('onDemandCap') or {}, config.get('onDemandUsed') or {}
        cap = numeric(cap.get('val')) if isinstance(cap, dict) else None
        spent = numeric(spent.get('val')) if isinstance(spent, dict) else None
        if cap is not None and cap > 0 and spent is not None and spent >= 0:
            used = spent / cap * 100
    item = window('billing', 'Período de créditos', used, reset)
    return [item] if item else [], []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(url, token, headers=None):
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                                                  'User-Agent': 'MUR/3.0.0-beta.5', **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise LimitError('unavailable', 'O serviço retornou uma resposta maior que o esperado.')
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
    except urllib.error.HTTPError as error:
        error.close()
        if error.code in (401, 403):
            raise LimitError('loginRequired', 'Entre novamente no aplicativo deste provedor para consultar o saldo.') from None
        if error.code == 429:
            raise LimitError('rateLimited', 'O provedor limitou as consultas. O MUR tentará novamente em cinco minutos.') from None
        raise LimitError('unavailable', 'O serviço não conseguiu informar os limites agora.') from None
    except (OSError, ValueError):
        raise LimitError('unavailable', 'Não foi possível consultar o serviço agora. Confira sua conexão.') from None


def codex_binary(home):
    candidates = [Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex'),
                  Path('/Applications/Codex.app/Contents/Resources/codex'),
                  Path('/Applications/Codex.app/Contents/Resources/codex-cli/bin/codex'),
                  home / 'Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex',
                  home / 'Applications/Codex.app/Contents/Resources/codex',
                  home / 'Applications/Codex.app/Contents/Resources/codex-cli/bin/codex',
                  home / '.local/bin/codex', Path('/opt/homebrew/bin/codex'), Path('/usr/local/bin/codex')]
    located = shutil.which('codex')
    if located:
        candidates.append(Path(located))
    return next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def read_codex(home, source):
    binary = codex_binary(home)
    if not binary:
        raise LimitError('unavailable', 'Instale ou abra o Codex neste Mac para consultar os limites.')
    environment = {'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'CODEX_HOME': str(source)}
    process = subprocess.Popen([binary, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=environment)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b''

    def request(identifier, method, params=None):
        nonlocal buffer
        process.stdin.write((json.dumps({'id': identifier, 'method': method, **({'params': params} if params is not None else {})}) + '\n').encode())
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if b'\n' not in buffer:
                if not selector.select(timeout=.5):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 2 * 1024 * 1024:
                    break
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if value.get('id') == identifier:
                    if 'error' in value:
                        raise LimitError('unavailable', 'O Codex não conseguiu consultar os limites desta conta.')
                    return value.get('result') or {}
        raise LimitError('unavailable', 'A consulta ao Codex demorou mais que o esperado.')

    try:
        request(1, 'initialize', {'clientInfo': {'name': 'mur_usage', 'version': '3.0.0'}, 'capabilities': {}})
        process.stdin.write(b'{"method":"initialized"}\n')
        process.stdin.flush()
        return request(2, 'account/rateLimits/read')
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()


class LimitMonitor:
    def __init__(self, store):
        self.store = store
        self.rows = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.pending = False
        self.allow_claude_prompt = False
        self.claude_keychain_connected = False

    def request_refresh(self, connect_claude=False):
        with self.lock:
            self.pending = True
            self.allow_claude_prompt |= connect_claude
            if connect_claude:
                for row in self.rows.values():
                    if row['provider'] == 'Anthropic' and row['status'] in ('connectionRequired', 'loginRequired'):
                        row['nextAttempt'] = 0
        self.wake.set()

    def snapshot(self):
        now = time.time()
        with self.lock:
            rows = json.loads(json.dumps(list(self.rows.values())))
            pending = self.pending
        for row in rows:
            row['stale'] = bool(row.get('lastSuccess') and (now - row['lastSuccess'] > 600 or row['status'] != 'ready'
                                 or any(w.get('resetsAt') and w['resetsAt'] <= now for w in row['windows'])))
        enabled = self.store.settings['liveLimitsEnabled']
        return {'enabled': enabled, 'refreshing': pending, 'pollSeconds': 300, 'rows': rows if enabled else []}

    def claude_token(self, source, interactive):
        credentials = read_metadata(source / '.credentials.json')
        if not credentials and source == self.store.home / '.claude':
            executable = os.environ.get('MUR_NATIVE_EXECUTABLE')
            if executable and (interactive or self.claude_keychain_connected):
                self.claude_keychain_connected = False
                try:
                    result = subprocess.run([executable, '--claude-credential', 'interactive' if interactive else 'silent'],
                                            capture_output=True, timeout=45 if interactive else 5)
                    if result.returncode == 0:
                        credentials = json.loads(result.stdout)
                        self.claude_keychain_connected = True
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
        oauth = credentials.get('claudeAiOauth') if isinstance(credentials, dict) else None
        if not isinstance(oauth, dict) or not isinstance(oauth.get('accessToken'), str):
            raise LimitError('connectionRequired', 'Saldo do Claude opcional. Conecte para autorizar o acesso ao login no Chaves; histórico, tokens e custos funcionam sem essa conexão.')
        expiry = instant(oauth.get('expiresAt'))
        if expiry is not None and expiry <= time.time():
            raise LimitError('loginRequired', 'O login do Claude expirou. Abra o Claude Code e entre novamente.')
        return oauth['accessToken']

    def fetch(self, account, interactive):
        provider = account['provider']
        sources, home = self.store.settings['sources'], self.store.home
        if provider == 'OpenAI':
            return codex_limits(read_codex(home, Path(sources['codex']).expanduser()))
        if provider == 'Anthropic':
            token = self.claude_token(Path(sources['claude']).expanduser(), interactive)
            profile = fetch_json('https://api.anthropic.com/api/oauth/profile', token, {'anthropic-beta': 'oauth-2025-04-20'})
            person, organization = profile.get('account') or {}, profile.get('organization') or {}
            person = person.get('uuid') if isinstance(person, dict) else None
            organization = organization.get('uuid') if isinstance(organization, dict) else None
            person = person or profile.get('account_uuid') or profile.get('accountUuid')
            organization = organization or profile.get('organization_uuid') or profile.get('organizationUuid') or ''
            actual = identity(provider, str(person) + ':' + str(organization), None, '', '') if person else None
            if not actual or actual['id'] != account['id']:
                raise LimitError('connectionRequired', 'O login do Claude não corresponde ao perfil local. Abra o Claude Code e atualize os registros.')
            return claude_limits(fetch_json('https://api.anthropic.com/api/oauth/usage', token, {'anthropic-beta': 'oauth-2025-04-20'}))
        entries = read_metadata(Path(sources['grok']).expanduser() / 'auth.json')
        ordered = sorted(entries.items(), key=lambda item: not item[0].startswith('https://auth.x.ai::'))
        for _, entry in ordered:
            if not isinstance(entry, dict) or not isinstance(entry.get('key'), str):
                continue
            user = entry.get('user_id')
            actual = identity('xAI', str(user) + ':' + str(entry.get('team_id') or ''), None, '', '') if user else None
            if not actual or actual['id'] != account['id']:
                continue
            expiry = instant(entry.get('expires_at'))
            if expiry is not None and expiry <= time.time():
                raise LimitError('loginRequired', 'O login do Grok expirou. Abra o Grok e entre novamente.')
            return grok_limits(fetch_json('https://cli-chat-proxy.grok.com/v1/billing?format=credits', entry['key'], {'x-xai-token-auth': 'xai-grok-cli'}))
        raise LimitError('connectionRequired', 'Entre na sua conta pelo Grok neste Mac para consultar o saldo.')

    def refresh(self, interactive=False):
        if not self.store.settings['liveLimitsEnabled']:
            with self.lock:
                self.pending = False
            return
        accounts = detect_accounts(self.store.home, self.store.settings['sources'])
        current = {row['id'] for row in accounts}
        with self.lock:
            self.rows = {key: value for key, value in self.rows.items() if key in current}
            self.pending = True
        for account in accounts:
            if self.store.stop.is_set() or not self.store.settings['liveLimitsEnabled']:
                break
            now = time.time()
            with self.lock:
                previous = self.rows.get(account['id'], {})
                if now < previous.get('nextAttempt', 0):
                    continue
            row = {'accountId': account['id'], 'provider': account['provider'], 'name': PROVIDER_NAMES[account['provider']],
                   'label': account['label'], 'plan': account['plan'], 'checkedAt': now, 'lastSuccess': previous.get('lastSuccess'),
                   'windows': previous.get('windows', []), 'credits': previous.get('credits', []), 'nextAttempt': now + 300}
            try:
                windows, credits = self.fetch(account, interactive)
                row.update(windows=windows, credits=credits, lastSuccess=time.time(), status='ready' if windows or credits else 'unavailable',
                           message='Saldo informado pelo serviço.' if windows or credits else 'O serviço não informou um saldo nesta consulta.')
            except LimitError as error:
                row.update(status=error.status, message=str(error))
                if error.status in ('loginRequired', 'connectionRequired'):
                    row.update(windows=[], credits=[], lastSuccess=None)
            except Exception:
                row.update(status='unavailable', message='Não foi possível consultar este provedor agora.')
            fresh = {a['id'] for a in detect_accounts(self.store.home, self.store.settings['sources'])}
            with self.lock:
                self.rows = {key: value for key, value in self.rows.items() if key in fresh}
                if account['id'] in fresh and self.store.settings['liveLimitsEnabled']:
                    self.rows[account['id']] = row
        with self.lock:
            self.pending = False

    def run(self):
        while not self.store.stop.is_set():
            with self.lock:
                interactive = self.allow_claude_prompt
                self.allow_claude_prompt = False
            self.refresh(interactive)
            self.wake.wait(5)
            self.wake.clear()

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True, name='account-limits')
        thread.start()
