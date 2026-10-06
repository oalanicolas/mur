import collections
import datetime as dt
import hashlib
import json
import os
import socket
import sys
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo
from accounts import AccountRegistry
from billing import monthly_totals, summary as billing_summary
from limits import LimitMonitor

BASE = Path(__file__).resolve().parent
def system_timezone():
    configured = os.environ.get('MUR_TIMEZONE')
    if not configured and sys.platform == 'win32':
        from tzlocal import get_localzone_name
        configured = get_localzone_name()
    local = str(Path('/etc/localtime').resolve())
    name = configured or (local.split('/zoneinfo/', 1)[1] if '/zoneinfo/' in local else 'UTC')
    try:
        return ZoneInfo(name)
    except (ValueError, KeyError):
        return ZoneInfo('UTC')


TZ = system_timezone()
PROVIDERS = ('OpenAI', 'Anthropic', 'xAI')
UNITS = ('input', 'cache', 'write', 'write1h', 'output', 'reasoning')
COUNTERS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens')


def process_present(pid):
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return default


def clean_text(value, limit=24000):
    text = str(value or '')
    text = re.sub(r'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|xai-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,})\b', '[credencial ocultada]', text)
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/-]{16,}', r'\1[ocultado]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*[\"\x27]?)[^\s\"\x27,;]{8,}', r'\1[ocultado]', text)
    return text[:limit] + (('\n[Trecho longo: exibição limitada a 24 mil caracteres.]' if limit >= 24000 else '…') if len(text) > limit else '')


def content_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(str(x.get('text', '')) for x in content if isinstance(x, dict) and x.get('type') in ('text', 'input_text', 'output_text'))


SCHEMA = '''
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, external_id TEXT, provider TEXT, title TEXT DEFAULT '', cwd TEXT DEFAULT '',
 parent TEXT DEFAULT '', model TEXT DEFAULT '', effort TEXT DEFAULT '', channel TEXT DEFAULT '',
 source TEXT DEFAULT '', first_ts REAL DEFAULT 0, last_ts REAL DEFAULT 0,
 status TEXT DEFAULT 'unknown', status_ts REAL DEFAULT 0, project TEXT DEFAULT '',
 category TEXT DEFAULT '', confidence TEXT DEFAULT '', basis TEXT DEFAULT '', override INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records (
 id TEXT PRIMARY KEY, sid TEXT, ts REAL, model TEXT, effort TEXT, kind TEXT, data TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS records_session ON records(sid,ts);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, sid TEXT, ts REAL, model TEXT, effort TEXT,
 input INTEGER, cache INTEGER, write INTEGER, write1h INTEGER, output INTEGER, reasoning INTEGER,
 tokens INTEGER, usd REAL, recorded_usd REAL, calls INTEGER, method TEXT, source TEXT, gap TEXT
);
CREATE INDEX IF NOT EXISTS events_session ON events(sid,ts);
CREATE INDEX IF NOT EXISTS events_time ON events(ts);
CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, sid TEXT, ts REAL, role TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS messages_session ON messages(sid,ts);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, kind TEXT, inode INTEGER, size INTEGER, mtime INTEGER, offset INTEGER, context TEXT);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, sid TEXT, provider TEXT, title TEXT, project TEXT, status TEXT, updated REAL, pid INTEGER, model TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS skipped_usage (id TEXT PRIMARY KEY,sid TEXT,source TEXT,ts REAL,reason TEXT);
CREATE TABLE IF NOT EXISTS imported_events AS SELECT events.*,'' AS machine FROM events WHERE 0;
CREATE UNIQUE INDEX IF NOT EXISTS imported_events_id ON imported_events(id);
CREATE INDEX IF NOT EXISTS imported_events_time ON imported_events(ts);
CREATE INDEX IF NOT EXISTS imported_events_session ON imported_events(sid,ts);
CREATE TABLE IF NOT EXISTS session_origins (sid TEXT,machine TEXT,PRIMARY KEY(sid,machine));
CREATE TABLE IF NOT EXISTS machine_imports (machine TEXT PRIMARY KEY,label TEXT,hostname TEXT,start REAL,end REAL,collected_at REAL,imported_at REAL,events INTEGER,sessions INTEGER,coverage TEXT);
CREATE VIEW IF NOT EXISTS usage_events AS
 SELECT events.*,'local' AS machine FROM events
 UNION ALL
 SELECT imported_events.* FROM imported_events
 WHERE NOT EXISTS (SELECT 1 FROM events WHERE events.id=imported_events.id);
'''


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, data_dir=None, home=None):
        self.home = Path(home or os.environ.get('MUR_HOME') or Path.home())
        default_profile = (Path(os.environ.get('LOCALAPPDATA', str(self.home / 'AppData/Local'))) / 'MUR'
                           if sys.platform == 'win32' else self.home / 'Library/Application Support/MUR')
        self.data_dir = Path(data_dir or os.environ.get('MUR_DATA_DIR') or default_profile)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        self.db_path = self.data_dir / 'agents.sqlite3'
        self.lock = threading.Lock()
        self.progress = {'running': False, 'phase': 'Pronto', 'processed': 0, 'total': 0, 'error': None}
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.settings_path = self.data_dir / 'settings.json'
        self.accounts = AccountRegistry(self.data_dir)
        pricing = read_json(BASE / 'pricing.json', {})
        defaults = {'monthly': {p: 0 for p in PROVIDERS}, 'subscriptions': [], 'billingReviewed': False, 'setupComplete': self.db_path.exists(),
                    'rates': pricing.get('rates', []), 'ratesDate': pricing.get('date', ''), 'refreshSeconds': 60, 'liveLimitsEnabled': False,
                    'timezone': str(TZ), 'deviceId': uuid.uuid4().hex, 'machineLabel': socket.gethostname().removesuffix('.local'),
                    'sources': {'codex': str(self.home / '.codex'), 'claude': str(self.home / '.claude'), 'grok': str(self.home / '.grok')}}
        previous = read_json(self.settings_path, {})
        self.settings = defaults | previous
        for key in ('monthly', 'sources'):
            self.settings[key] = defaults[key] | previous.get(key, {})
        if 'subscriptions' not in previous:
            self.settings['subscriptions'] = [{'id':'legacy-'+provider.lower(),'provider':provider,'label':provider+' · total informado','accountId':'','monthlyUsd':value,'quantity':1} for provider,value in self.settings['monthly'].items() if value > 0]
            self.settings['billingReviewed'] = bool(self.settings['subscriptions'])
        self.settings['monthly'] = monthly_totals(self.settings['subscriptions'],dt.datetime.now(ZoneInfo(self.settings['timezone'])).date().isoformat())
        self.timezone = ZoneInfo(self.settings['timezone'])
        self.rates = {r['model']: r for r in self.settings['rates']}
        self.classifications = {r['session']: r for r in read_json(self.data_dir / 'project-classifications.json', [])}
        with self.connect() as c:
            c.executescript(SCHEMA)
            view = c.execute("SELECT sql FROM sqlite_master WHERE name='usage_events'").fetchone()[0]
            if "'studio'" in view:
                c.execute('DROP VIEW usage_events')
                c.execute(view.replace("'studio'", "'local'"))
                c.execute("INSERT OR IGNORE INTO session_origins SELECT sid,'local' FROM session_origins WHERE machine='studio'")
                c.execute("DELETE FROM session_origins WHERE machine='studio'")
            c.execute("INSERT OR IGNORE INTO session_origins SELECT id,'local' FROM sessions WHERE NOT EXISTS (SELECT 1 FROM session_origins o WHERE o.sid=sessions.id)")
        if self.settings != previous:
            self.save_settings(self.settings)
        self.limits = LimitMonitor(self)

    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=30, factory=Connection)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA busy_timeout=30000')
        c.create_function('local_day', 1, lambda ts: dt.datetime.fromtimestamp(ts, self.timezone).date().isoformat(), deterministic=True)
        return c

    def save_settings(self, settings):
        settings['monthly'] = monthly_totals(settings['subscriptions'],dt.datetime.now(ZoneInfo(settings['timezone'])).date().isoformat())
        temp = self.settings_path.with_suffix('.tmp')
        temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2))
        os.chmod(temp, 0o600)
        os.replace(temp, self.settings_path)
        self.settings = settings
        self.timezone = ZoneInfo(settings['timezone'])
        self.rates = {r['model']: r for r in settings['rates']}

    def meta(self, c, key, value):
        c.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))

    def session(self, c, provider, external, **values):
        sid = provider + ':' + external
        c.execute('INSERT OR IGNORE INTO sessions(id,external_id,provider) VALUES (?,?,?)', (sid, external, provider))
        c.execute("INSERT OR IGNORE INTO session_origins VALUES (?,'local')", (sid,))
        allowed = {'title', 'cwd', 'parent', 'model', 'effort', 'channel', 'source'}
        for key, value in values.items():
            if key in allowed and value:
                c.execute(f'UPDATE sessions SET {key}=? WHERE id=?', (clean_text(value, 800), sid))
        row = c.execute('SELECT override,project,cwd FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row['override']:
            known = self.classifications.get(external)
            if known:
                p, cat, confidence, basis = (known.get(k, '') for k in ('projectName', 'category', 'confidence', 'basis'))
            else:
                name = Path(row['cwd']).name if row['cwd'] else ''
                generic = {'Games', 'Code', 'Codex', 'Documents', 'tmp', self.home.name}
                p = f'{name} · não classificado' if name in generic else name or 'Sem projeto identificado'
                cat, confidence, basis = 'Pasta de trabalho', 'baixa', 'Identificado pela pasta registrada; atribuição não revisada.'
            c.execute('UPDATE sessions SET project=?,category=?,confidence=?,basis=? WHERE id=?', (p, cat, confidence, basis, sid))
        return sid

    def activity(self, c, sid, ts, status=None):
        if not ts:
            return
        c.execute('UPDATE sessions SET first_ts=CASE WHEN first_ts=0 THEN ? ELSE MIN(first_ts,?) END,last_ts=MAX(last_ts,?) WHERE id=?', (ts, ts, ts, sid))
        if status:
            c.execute('UPDATE sessions SET status=?,status_ts=? WHERE id=? AND status_ts<=?', (status, ts, sid, ts))

    def message(self, c, sid, ts, role, text, key=None):
        if role not in ('user', 'assistant') or not text.strip():
            return
        text = clean_text(text)
        key = key or digest((sid, ts, role, text))
        c.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?)', (key, sid, ts, role, text))

    def record(self, c, sid, ts, model, effort, kind, data, source, key):
        if not ts:
            return
        c.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                  (key, sid, ts, model or 'não identificado', effort or '', kind, json.dumps(data), source))
        self.activity(c, sid, ts)

    def cost(self, provider, model, u):
        if provider == 'xAI' and u.get('reported_usd') is not None:
            return float(u['reported_usd'])
        r = self.rates.get(model)
        if not r or (provider == 'OpenAI' and (u.get('request_input') or 0) > 272000):
            return None
        return (u['input'] * r['input'] + u['cache'] * r['cache'] + (u['write'] - u['write1h']) * (r.get('write5m') or 0) + u['write1h'] * (r.get('write1h') or 0) + u['output'] * r['output']) / 1e6

    def reconcile(self, c, sid):
        provider = sid.split(':', 1)[0]
        records = c.execute('SELECT * FROM records WHERE sid=? ORDER BY ts,rowid', (sid,)).fetchall()
        previous, seen, merged = None, set(), {}
        has_native = any(r['kind'] != 'receipt' for r in records)
        for r in records:
            if r['kind'] == 'receipt' and has_native:
                continue
            data = json.loads(r['data'])
            gap = ''
            if r['kind'] == 'counter':
                total = data.get('total_token_usage') or {}
                dedup = (r['ts'], tuple(total.get(k, 0) for k in COUNTERS))
                if dedup in seen:
                    continue
                seen.add(dedup)
                delta = {k: total.get(k, 0) - (previous or {}).get(k, 0) for k in COUNTERS}
                if previous is None or delta['input_tokens'] < 0 or delta['output_tokens'] < 0:
                    delta = {k: (data.get('last_token_usage') or {}).get(k, 0) for k in COUNTERS}
                    if total.get('input_tokens', 0) != delta['input_tokens']:
                        gap = 'Contador inicial ou reiniciado: somente a última chamada tem data atribuível.'
                previous = total
                u = {'input': delta['input_tokens'] - delta['cached_input_tokens'] - delta['cache_write_input_tokens'],
                     'cache': delta['cached_input_tokens'], 'write': delta['cache_write_input_tokens'], 'write1h': 0,
                     'output': delta['output_tokens'], 'reasoning': delta['reasoning_output_tokens'], 'requests': 1,
                     'request_input': (data.get('last_token_usage') or {}).get('input_tokens'), 'method': 'Contadores reconciliados por sessão'}
                eid = digest((sid, dedup))
            else:
                u = data
                eid = u.get('event_id') or r['id']
            if any(not isinstance(u.get(k, 0), (int, float)) or u.get(k, 0) < 0 for k in UNITS) or u.get('write1h', 0) > u.get('write', 0):
                c.execute('INSERT OR REPLACE INTO skipped_usage VALUES (?,?,?,?,?)', (r['id'],sid,r['source'],r['ts'],'Categorias de tokens inconsistentes; não somadas.'))
                continue
            total_tokens = sum(u.get(k, 0) for k in ('input', 'cache', 'write', 'output'))
            if not total_tokens and u.get('reported_usd') is None and r['kind'] == 'counter':
                if gap:
                    c.execute('INSERT OR REPLACE INTO skipped_usage VALUES (?,?,?,?,?)', (r['id'],sid,r['source'],r['ts'],gap))
                continue
            if eid in merged:
                prior_row, prior, prior_gap = merged[eid]
                for k in UNITS:
                    u[k] = max(u.get(k, 0), prior.get(k, 0))
                total_tokens = sum(u.get(k, 0) for k in ('input', 'cache', 'write', 'output'))
                if prior_row['ts'] < r['ts']:
                    r = prior_row
                gap = gap or prior_gap
            u['tokens'] = total_tokens
            merged[eid] = (r, u, gap)
        c.execute('DELETE FROM events WHERE sid=?', (sid,))
        for eid, (r, u, gap) in merged.items():
            c.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                      (eid, sid, r['ts'], r['model'], r['effort'], *(int(u.get(k, 0)) for k in UNITS), u['tokens'], self.cost(provider, r['model'], u), u.get('reported_usd'), u.get('requests'), u.get('method', ''), r['source'], gap))

    def codex_row(self, c, row, ctx, path):
        p = row.get('payload') or {}
        kind, ts = row.get('type'), timestamp(row.get('timestamp'))
        if kind == 'session_meta':
            ctx['external'] = p.get('id', ctx['external'])
            ctx['cwd'] = p.get('cwd', ctx.get('cwd', ''))
            source = p.get('source') or {}
            parent = source.get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id', '') if isinstance(source, dict) and isinstance(source.get('subagent'), dict) else ''
            ctx['parent'] = parent or ctx.get('parent', '')
        if kind == 'turn_context':
            ctx['model'] = p.get('model', ctx.get('model', ''))
            ctx['effort'] = p.get('effort') or p.get('reasoning_effort') or ctx.get('effort', '')
        if not ctx.get('sid') or kind in ('session_meta', 'turn_context'):
            ctx['sid'] = self.session(c, 'OpenAI', ctx['external'], cwd=ctx.get('cwd'), parent=ctx.get('parent'), model=ctx.get('model'), effort=ctx.get('effort'), source=str(path), channel='Subagente Codex' if ctx.get('parent') else 'Conversa Codex')
        sid = ctx['sid']
        if kind == 'event_msg':
            event = p.get('type')
            if event in ('task_started', 'turn_started'):
                self.activity(c, sid, ts, 'running')
            elif event in ('task_complete', 'task_completed', 'turn_completed'):
                self.activity(c, sid, ts, 'completed')
            elif event in ('turn_aborted', 'task_aborted'):
                self.activity(c, sid, ts, 'cancelled')
            if event == 'token_count' and p.get('info'):
                info = {k: p['info'].get(k) for k in ('total_token_usage', 'last_token_usage')}
                self.record(c, sid, ts, ctx.get('model'), ctx.get('effort'), 'counter', info, str(path), digest((str(path), sid, ts, info)))
                return sid
        if kind == 'response_item' and p.get('type') == 'message' and p.get('channel') != 'analysis':
            self.message(c, sid, ts, p.get('role'), content_text(p.get('content')))
        return None

    def claude_row(self, c, row, ctx, path):
        external = row.get('sessionId') or ctx['external']
        ctx['external'] = external
        msg = row.get('message') or {}
        model = msg.get('model', ctx.get('model', ''))
        if model and model != '<synthetic>':
            ctx['model'] = model
        if ctx.get('sid') != 'Anthropic:' + external or ctx.get('saved_model') != model:
            ctx['sid'] = self.session(c, 'Anthropic', external, cwd=row.get('cwd'), model=ctx.get('model'), source=str(path), channel='Claude Code / subagentes')
            ctx['saved_model'] = model
        sid = ctx['sid']
        ts = timestamp(row.get('timestamp'))
        if row.get('type') == 'custom-title':
            self.session(c, 'Anthropic', external, title=row.get('customTitle'))
        if row.get('type') in ('user', 'assistant'):
            text = content_text(msg.get('content'))
            self.message(c, sid, ts, row['type'], text, digest((sid, row.get('uuid') or msg.get('id') or ts, row['type'])))
            if row['type'] == 'user' and text and not text.startswith(('<', '[Request')):
                c.execute("UPDATE sessions SET title=? WHERE id=? AND title=''", (clean_text(' '.join(text.split()), 180), sid))
            self.activity(c, sid, ts)
        usage = msg.get('usage')
        if row.get('type') != 'assistant' or not isinstance(usage, dict) or not msg.get('id') or model == '<synthetic>':
            return None
        u = {'input': usage.get('input_tokens', 0), 'cache': usage.get('cache_read_input_tokens', 0), 'write': usage.get('cache_creation_input_tokens', 0),
             'write1h': (usage.get('cache_creation') or {}).get('ephemeral_1h_input_tokens', 0), 'output': usage.get('output_tokens', 0),
             'reasoning': (usage.get('output_tokens_details') or {}).get('thinking_tokens', 0), 'requests': 1, 'event_id': 'Anthropic:' + msg['id'], 'method': 'Usage Claude, deduplicado por message.id'}
        self.record(c, sid, ts, model, row.get('effort'), 'usage', u, str(path), digest((str(path), msg['id'], usage)))
        return sid

    def index_jsonl(self, c, path, kind):
        stat = path.stat()
        old = c.execute('SELECT * FROM files WHERE path=?', (str(path),)).fetchone()
        if old and old['mtime'] == stat.st_mtime_ns and old['size'] == stat.st_size:
            return set()
        restart = not old or old['inode'] != stat.st_ino or stat.st_size < old['offset'] or (stat.st_size == old['size'] and old['mtime'] != stat.st_mtime_ns)
        offset = 0 if restart else old['offset']
        ctx = {'external': path.stem[-36:]} if restart else json.loads(old['context'])
        affected = set()
        with path.open('rb') as f:
            f.seek(offset)
            while True:
                start = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b'\n') and len(line) <= 4 * 1024 * 1024:
                    try:
                        json.loads(line)
                    except ValueError:
                        f.seek(start)
                        break
                prefix = line[:700]
                wanted = (b'"event_msg"', b'"turn_context"', b'"session_meta"', b'"message"') if kind == 'codex' else (b'"assistant"', b'"user"', b'"custom-title"')
                if not any(token in prefix for token in wanted) or len(line) > 4 * 1024 * 1024:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    if not line.endswith(b'\n'):
                        f.seek(start)
                        break
                    continue
                if not isinstance(row, dict):
                    continue
                sid = self.codex_row(c, row, ctx, path) if kind == 'codex' else self.claude_row(c, row, ctx, path)
                if sid:
                    affected.add(sid)
            offset = f.tell()
        c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)', (str(path), kind, stat.st_ino, stat.st_size, stat.st_mtime_ns, offset, json.dumps(ctx)))
        return affected

    def discover(self, c):
        paths = {}
        codex = Path(self.settings['sources']['codex']).expanduser()
        databases = sorted(codex.glob('state_*.sqlite'), key=lambda p: p.stat().st_mtime, reverse=True)
        catalog_error = None
        if databases:
            try:
                with sqlite3.connect(f'file:{databases[0]}?mode=ro', uri=True, factory=Connection) as catalog:
                    catalog.row_factory = sqlite3.Row
                    tables = {r[0] for r in catalog.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    edges = {r[0]: r[1] for r in catalog.execute('SELECT child_thread_id,parent_thread_id FROM thread_spawn_edges')} if 'thread_spawn_edges' in tables else {}
                    for r in catalog.execute('SELECT * FROM threads'):
                        row = dict(r)
                        sid = self.session(c, 'OpenAI', row['id'], title=row.get('title'), cwd=row.get('cwd'), model=row.get('model'), effort=row.get('reasoning_effort'), parent=edges.get(row['id']), channel='Subagente Codex' if row['id'] in edges else 'Conversa Codex')
                        self.activity(c, sid, timestamp(row.get('created_at')))
                        p = Path(row.get('rollout_path') or '')
                        if p.is_file() and p.suffix == '.jsonl' and p.is_relative_to(codex):
                            paths[p] = 'codex'
            except sqlite3.Error as error:
                catalog_error = str(error)
        for root, pattern, kind in [(codex / 'sessions', '**/*.jsonl', 'codex'), (codex / 'archived_sessions', '**/*.jsonl', 'codex'), (Path(self.settings['sources']['claude']).expanduser() / 'projects', '**/*.jsonl', 'claude')]:
            for p in root.glob(pattern):
                if p.is_file():
                    paths[p] = kind
        self.meta(c, 'catalog_error', catalog_error)
        return sorted(paths.items(), key=lambda item: item[0].stat().st_mtime, reverse=True)

    def grok(self, c):
        root = Path(self.settings['sources']['grok']).expanduser()
        affected = set()
        for p in (root / 'sessions').glob('*/*/summary.json'):
            summary = read_json(p, {})
            if not summary:
                continue
            info = summary.get('info') or {}
            external = info.get('id') or p.parent.name
            sid = self.session(c, 'xAI', external, title=summary.get('generated_title'), cwd=info.get('cwd'), model=summary.get('current_model_id'), effort=summary.get('reasoning_effort'), parent=info.get('parent_session_id'), source=str(p), channel='Grok CLI / Companion')
            self.activity(c, sid, timestamp(summary.get('created_at')))
            self.activity(c, sid, timestamp(summary.get('updated_at')))
            usage_file = p.parent / 'usage.json'
            usage = read_json(usage_file, {})
            stat = usage_file.stat() if usage_file.exists() else None
            previous = c.execute('SELECT * FROM files WHERE path=?', (str(usage_file),)).fetchone()
            changed = stat and (not previous or previous['mtime'] != stat.st_mtime_ns or previous['size'] != stat.st_size)
            if changed:
                for turn in usage.get('turns', []):
                    ts = timestamp(turn.get('endedAt'))
                    if ts < timestamp(summary.get('created_at')):
                        continue
                    fingerprint = digest({k: v for k, v in turn.items() if k != 'turnNumber'})
                    for model, u in (turn.get('modelUsage') or {turn.get('primaryModelId') or summary.get('current_model_id', 'não identificado'): turn}).items():
                        cache, write = u.get('cachedReadTokens', 0), u.get('cacheCreationTokens', 0)
                        rec = {'input': u.get('inputTokens', 0) - cache - write, 'cache': cache, 'write': write, 'write1h': 0, 'output': u.get('outputTokens', 0), 'reasoning': u.get('reasoningTokens', 0), 'requests': u.get('modelCalls'), 'reported_usd': u['costUsdTicks'] / 1e10 if u.get('costUsdTicks') is not None else None, 'event_id': 'xAI:' + digest((fingerprint, model)), 'method': 'Turno Grok; custo informado pelo CLI'}
                        self.record(c, sid, ts, model, summary.get('reasoning_effort'), 'usage', rec, str(usage_file), digest((str(usage_file), fingerprint, model)))
                c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)', (str(usage_file), 'grok', stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_size, '{}'))
                affected.add(sid)
            chat = p.parent / 'chat_history.jsonl'
            if chat.exists():
                st = chat.stat()
                old = c.execute('SELECT * FROM files WHERE path=?', (str(chat),)).fetchone()
                if not old or old['mtime'] != st.st_mtime_ns:
                    offset = old['offset'] if old and old['inode'] == st.st_ino and old['offset'] <= st.st_size else 0
                    with chat.open('rb') as f:
                        f.seek(offset)
                        while line := f.readline():
                            start = offset
                            offset = f.tell()
                            if len(line) > 4 * 1024 * 1024:
                                continue
                            try:
                                row = json.loads(line)
                            except ValueError:
                                if not line.endswith(b'\n'):
                                    offset = start
                                    break
                                continue
                            if isinstance(row, dict):
                                role = row.get('type') or row.get('role')
                                self.message(c, sid, timestamp(row.get('timestamp')), role, content_text(row.get('content')), digest((sid, offset, role)))
                    c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)', (str(chat), 'grok-chat', st.st_ino, st.st_size, st.st_mtime_ns, offset, '{}'))
        return affected

    def companion_jobs(self, c=None):
        codex = Path(self.settings['sources']['codex']).expanduser()
        claude = read_json(codex / 'plugins/data/cc-plugin-codex-cc/jobs.json', {}).get('jobs', [])
        grok_root = Path(self.settings['sources']['grok']).expanduser()
        jobs = [('Anthropic', j, codex / 'plugins/data/cc-plugin-codex-cc/jobs.json') for j in claude]
        for p in (grok_root / 'codex-plugin/state').glob('*/jobs/*.json'):
            if not p.name.endswith(('.result.json', '.progress.json')):
                j = read_json(p)
                if isinstance(j, dict):
                    jobs.append(('xAI', j, p))
        rows = []
        for provider, j, source in jobs:
            if not j.get('id'):
                continue
            result = read_json(j.get('resultFile'), {}) if provider == 'xAI' else {}
            external = j.get('claudeSessionId') or result.get('sessionId')
            sid = f'{provider}:{external}' if external else ''
            title = j.get('title') or (j.get('args') or {}).get('task') or j['id']
            values = (provider + ':' + j['id'], sid, provider, clean_text(title, 200), j.get('repoRoot') or j.get('workspaceRoot') or '', j.get('status', 'unknown'), timestamp(j.get('updatedAt')), j.get('pid'), j.get('model'), str(source))
            rows.append(dict(zip(('id','sid','provider','title','project','status','updated','pid','model','source'),values)))
            if c:
                c.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)', values)
            if c and external and provider == 'Anthropic':
                self.session(c, provider, external, channel='Claude Companion')
        return rows

    def archived_receipts(self, c):
        if c.execute("SELECT 1 FROM meta WHERE key='receipts_imported'").fetchone():
            return set()
        raw = read_json(self.data_dir / 'receipts.json', {})
        sessions = {s['id']: s for s in raw.get('sessions', [])}
        affected = set()
        for i, e in enumerate(raw.get('events', [])):
            if e.get('requests') is not None:
                continue
            s = sessions.get(e['session'], {})
            sid = self.session(c, e['provider'], e['session'], title=s.get('title'), cwd=s.get('project'), parent=s.get('parent'), model=e.get('model'), channel=s.get('channel'), source=s.get('source'))
            self.record(c, sid, timestamp(e['timestamp']), e['model'], e.get('effort'), 'receipt', dict(e, method='Recibo preservado na auditoria local: ' + e.get('method', '')), s.get('source', ''), 'audit:' + digest((i, e)))
            affected.add(sid)
        self.meta(c, 'receipts_imported', True)
        return affected

    def scan(self):
        if not self.lock.acquire(blocking=False):
            return False
        start = time.time()
        self.progress = {'running': True, 'phase': 'Localizando históricos', 'processed': 0, 'total': 0, 'error': None}
        issues = []
        try:
            with self.connect() as c:
                paths = self.discover(c)
                c.commit()
                self.progress.update(total=len(paths), phase='Lendo novidades dos registros')
                for i, (p, kind) in enumerate(paths):
                    if self.stop.is_set():
                        break
                    try:
                        affected = self.index_jsonl(c, p, kind)
                        for sid in affected:
                            self.reconcile(c, sid)
                        c.commit()
                    except (OSError, ValueError) as error:
                        issues.append({'source': str(p), 'error': str(error)[:200]})
                    self.progress.update(processed=i + 1)
                self.progress['phase'] = 'Conferindo Grok e companions'
                affected = self.grok(c) | self.archived_receipts(c)
                for sid in affected:
                    self.reconcile(c, sid)
                self.companion_jobs(c)
                self.meta(c, 'last_scan', time.time())
                self.meta(c, 'scan_seconds', round(time.time() - start, 2))
                self.meta(c, 'source_issues', issues)
                self.meta(c, 'initial_scan_complete', not self.stop.is_set())
                self.meta(c, 'scan_error', None)
        except Exception as error:
            self.progress['error'] = clean_text(str(error), 500)
            with self.connect() as c:
                self.meta(c, 'scan_error', self.progress['error'])
        finally:
            self.progress.update(running=False, phase='Atualizado' if not self.progress['error'] else 'Atualização interrompida')
            self.lock.release()
        return not self.progress['error']

    def run(self):
        while not self.stop.is_set():
            self.accounts.snapshot(self.home, self.settings['sources'])
            if self.settings['setupComplete']:
                self.scan()
            self.wake.wait(self.settings['refreshSeconds'])
            self.wake.clear()

    def start(self):
        self.limits.start()
        thread = threading.Thread(target=self.run, daemon=True, name='local-indexer')
        thread.start()
        return thread

    def state(self):
        with self.connect() as c:
            metadata = {r['key']: json.loads(r['value']) for r in c.execute('SELECT * FROM meta')}
            counts = {k: c.execute(f'SELECT COUNT(*) FROM {"usage_events" if k=="events" else k}').fetchone()[0] for k in ('sessions', 'events', 'messages', 'files', 'skipped_usage')}
            bounds = dict(c.execute('SELECT MIN(ts) first,MAX(ts) last FROM usage_events').fetchone())
            imports = [dict(r) for r in c.execute('SELECT machine,label,hostname,start,end,collected_at,imported_at,events,sessions FROM machine_imports')]
        machines = [{'id': 'local', 'label': self.settings['machineLabel'], 'local': True}]
        machines.extend({'id': r['machine'], 'label': r['label'], 'local': False} for r in imports)
        detected = {k: Path(v).expanduser().is_dir() for k, v in self.settings['sources'].items()}
        return {'progress': self.progress, 'metadata': metadata, 'counts': counts, 'bounds': bounds,
                'accounts': self.accounts.snapshot(self.home,self.settings['sources']), 'limits': self.limits.snapshot(),
                'settings': self.settings, 'imports': imports, 'timezone': str(self.timezone), 'machine': self.settings['machineLabel'],
                'machines': machines, 'detectedSources': detected, 'hasReport': (self.data_dir / 'report.html').is_file(), 'version': '3.0.0-beta.6',
                'platform': 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else 'other'}

    def live(self):
        now = time.time()
        with self.connect() as c:
            jobs = [j for j in self.companion_jobs() if j['status'] in ('running','pending','queued','starting') or j['updated'] > now - 3600]
            rows = [dict(r) for r in c.execute("SELECT * FROM sessions WHERE (status='running' OR last_ts>?) AND EXISTS (SELECT 1 FROM session_origins o WHERE o.sid=sessions.id AND o.machine='local') ORDER BY last_ts DESC LIMIT 150", (now - 900,))]
        linked = set()
        result = []
        for j in jobs:
            live = False
            if isinstance(j['pid'], int) and j['pid'] > 1:
                live = process_present(j['pid'])
            declared = j['status'] in ('running', 'pending', 'queued', 'starting')
            status = 'running' if declared and live else 'unconfirmed' if declared else j['status']
            result.append(dict(j, state=status, evidence='Job em execução e processo presente' if status == 'running' else 'Estado gravado pelo companion; processo ausente ou sem confirmação' if declared else 'Estado gravado pelo companion', last_ts=j['updated']))
            if j['sid']:
                linked.add(j['sid'])
        for r in rows:
            if r['id'] in linked:
                continue
            recent = now - r['last_ts'] < 300
            state = 'log_running' if r['status'] == 'running' and recent else 'unconfirmed' if r['status'] == 'running' else 'recent' if recent else r['status']
            result.append(dict(r, sid=r['id'], state=state, evidence='Início de execução sem encerramento, com atividade nos últimos 5 minutos' if state == 'log_running' else 'Sinal de execução antigo; não é possível confirmar que continua rodando' if state == 'unconfirmed' else 'Atividade registrada; não comprova execução atual'))
        order = {'running': 0, 'log_running': 1, 'recent': 2, 'unconfirmed': 3}
        return sorted(result, key=lambda r: (order.get(r['state'], 4), -r['last_ts']))

    def bounds(self, params):
        now = time.time()
        period = params.get('period', '7d')
        if period == 'custom':
            start = dt.datetime.combine(dt.date.fromisoformat(params['start']), dt.time(), self.timezone).timestamp()
            end = dt.datetime.combine(dt.date.fromisoformat(params['end']) + dt.timedelta(days=1), dt.time(), self.timezone).timestamp()
        elif period == 'today':
            start = dt.datetime.now(self.timezone).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            end = now
        elif period == 'all':
            with self.connect() as c:
                start = c.execute('SELECT MIN(ts) FROM usage_events').fetchone()[0] or now
            end = now
        elif period == 'combined':
            with self.connect() as c:
                row = c.execute("SELECT value FROM meta WHERE key='combined_window'").fetchone()
            if not row:
                raise ValueError('Ainda não há uma coleta importada.')
            start, end = json.loads(row[0])
        elif period == 'audit':
            with self.connect() as c:
                row = c.execute("SELECT value FROM meta WHERE key='audit_window'").fetchone()
            if not row:
                raise ValueError('Nenhum período de relatório foi salvo neste perfil.')
            start, end = json.loads(row[0])
        elif period in ('7d', '30d', '90d'):
            start, end = now - int(period[:-1]) * 86400, now
        else:
            raise ValueError('Período inválido.')
        if not 0 <= start < end or end - start > 100 * 365 * 86400:
            raise ValueError('Escolha uma data inicial anterior à final.')
        return start, end

    def filters(self, params):
        start, end = self.bounds(params)
        where, values = ['e.ts>=?', 'e.ts<?'], [start, end]
        for key, column in [('provider', 's.provider'), ('model', 'e.model'), ('project', 's.project'), ('machine', 'e.machine')]:
            if params.get(key):
                where.append(column + '=?')
                values.append(params[key])
        if params.get('q'):
            where.append("(s.title LIKE ? ESCAPE '\\' OR s.project LIKE ? ESCAPE '\\' OR s.external_id LIKE ? ESCAPE '\\')")
            value = '%' + params['q'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')[:200] + '%'
            values.extend([value] * 3)
        return ' AND '.join(where), values, start, end

    def overview(self, params):
        where, values, start, end = self.filters(params)
        join = ' FROM usage_events e JOIN sessions s ON e.sid=s.id WHERE ' + where
        aggregate = 'SUM(e.tokens) tokens,SUM(e.usd) usd,COUNT(*) events,SUM(e.cache) cache,SUM(e.input) input,SUM(e.write) write,SUM(e.output) output,SUM(e.reasoning) reasoning,SUM(e.calls) calls,SUM(CASE WHEN e.usd IS NULL THEN e.tokens ELSE 0 END) unpriced_tokens'
        keys = ('tokens','events','cache','input','write','output','reasoning','calls','unpriced_tokens')
        def blank():
            return dict.fromkeys(keys, 0) | {'usd': None, 'members': set()}
        def accumulate(target, row):
            for key in keys:
                target[key] += row[key] or 0
            if row['usd'] is not None:
                target['usd'] = (target['usd'] or 0) + row['usd']
            target['members'].add(row['sid'])
        def finish(target):
            target['sessions'] = len(target.pop('members'))
            return target
        totals = blank()
        groups = {k: {} for k in ('providers','models','projects','days','machines')}
        with self.connect() as c:
            rows = c.execute("SELECT e.sid,s.provider,s.project,e.model,e.machine,local_day(e.ts) day," + aggregate + join + ' GROUP BY e.sid,e.model,day,e.machine', values)
            for row in rows:
                accumulate(totals, row)
                for name, field in [('providers','provider'),('models','model'),('projects','project'),('days','day'),('machines','machine')]:
                    label = row[field]
                    if label not in groups[name]:
                        groups[name][label] = blank() | {'label': label}
                    accumulate(groups[name][label],row)
                split = groups['days'][row['day']].setdefault('byProvider', {}).setdefault(row['provider'], {'tokens': 0, 'usd': None})
                split['tokens'] += row['tokens'] or 0
                if row['usd'] is not None:
                    split['usd'] = (split['usd'] or 0) + row['usd']
            projects = [r[0] for r in c.execute("SELECT DISTINCT project FROM sessions WHERE project!='' ORDER BY project")]
            models = [r[0] for r in c.execute('SELECT DISTINCT model FROM usage_events ORDER BY model')]
            duplicate_count = c.execute('SELECT COUNT(*) FROM imported_events r JOIN events e ON e.id=r.id WHERE r.ts>=? AND r.ts<?', (start,end)).fetchone()[0]
        totals = finish(totals)
        series = {name: sorted((finish(v) for v in group.values()),key=(lambda r:r['label']) if name=='days' else (lambda r:-r['tokens'])) for name,group in groups.items()}
        totals.update(billing_summary(self.settings['subscriptions'],start,end,self.timezone))
        totals['billingReviewed'] = self.settings['billingReviewed']
        return {'totals': totals, **series, 'duplicateEvents': duplicate_count, 'options': {'projects': projects, 'models': models}, 'start': start, 'end': end, 'daysCount': (end - start) / 86400}

    def sessions_page(self, params):
        where, values, start, end = self.filters(params)
        offset = max(0, min(1000000, int(params.get('offset', 0))))
        limit = max(1, min(500, int(params.get('limit', 40))))
        order = {'tokens': 'tokens DESC', 'usd': 'usd DESC', 'recent': 'last_ts DESC', 'title': 'title COLLATE NOCASE'}.get(params.get('sort'), 'tokens DESC')
        base = ' FROM usage_events e JOIN sessions s ON e.sid=s.id WHERE ' + where
        with self.connect() as c:
            total = c.execute('SELECT COUNT(DISTINCT s.id)' + base, values).fetchone()[0]
            rows = c.execute('SELECT s.id,s.external_id,s.provider,s.title,s.project,s.channel,s.confidence,MAX(e.ts) last_ts,SUM(e.tokens) tokens,SUM(e.usd) usd,GROUP_CONCAT(DISTINCT e.model) models,GROUP_CONCAT(DISTINCT e.machine) machines,COUNT(*) events' + base + ' GROUP BY s.id ORDER BY ' + order + ' LIMIT ? OFFSET ?', [*values, limit, offset])
            return {'rows': [dict(r) for r in rows], 'total': total, 'offset': offset, 'limit': limit}

    def session_detail(self, sid, offset=0):
        with self.connect() as c:
            row = c.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
            if not row:
                return None
            row = dict(row)
            row['machines'] = [r[0] for r in c.execute('SELECT machine FROM session_origins WHERE sid=? ORDER BY machine', (sid,))]
            stats = dict(c.execute('SELECT COUNT(*) events,SUM(tokens) tokens,SUM(usd) usd,MIN(ts) first,MAX(ts) last,SUM(CASE WHEN usd IS NULL THEN tokens ELSE 0 END) unpriced_tokens FROM usage_events WHERE sid=?', (sid,)).fetchone())
            models = [dict(r) for r in c.execute('SELECT model,SUM(tokens) tokens,SUM(usd) usd FROM usage_events WHERE sid=? GROUP BY model ORDER BY tokens DESC', (sid,))]
            messages = [dict(r) for r in c.execute('SELECT ts,role,text FROM messages WHERE sid=? ORDER BY ts,rowid LIMIT 60 OFFSET ?', (sid, offset))]
            count = c.execute('SELECT COUNT(*) FROM messages WHERE sid=?', (sid,)).fetchone()[0]
            children = [dict(r) for r in c.execute('SELECT id,title,provider,project FROM sessions WHERE parent=?', (row['external_id'],))]
        return {'session': row, 'stats': stats, 'models': models, 'messages': messages, 'messageCount': count, 'offset': offset, 'children': children}

    def assign_project(self, sid, project):
        project = project.strip()
        if not project or len(project) > 160:
            raise ValueError('Informe um nome de projeto entre 1 e 160 caracteres.')
        with self.connect() as c:
            count = c.execute("UPDATE sessions SET project=?,override=1,confidence='manual',basis='Classificação ajustada por você no painel local.' WHERE id=?", (project, sid)).rowcount
        if not count:
            raise ValueError('Sessão não encontrada.')

    def reprice(self):
        with self.connect() as c:
            for r in c.execute('SELECT e.*,s.provider FROM events e JOIN sessions s ON s.id=e.sid').fetchall():
                value = dict(r)
                value['reported_usd'] = value['recorded_usd']
                # A ausência do tamanho da chamada não autoriza precificar lacunas de contexto longo.
                if value['usd'] is None and value['model'] in self.rates and value['provider'] == 'OpenAI' and value['input'] + value['cache'] > 272000:
                    continue
                c.execute('UPDATE events SET usd=? WHERE id=?', (self.cost(value['provider'], value['model'], value), value['id']))
