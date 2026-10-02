import argparse
import csv
import io
import json
import os
from pathlib import Path
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo
import zlib

from core import BASE, Store, clean_text

import replay
from export_machine import packet_bytes
from import_machine import decode_packet, import_packet
from accounts import validate_subscriptions


def create_server(store, port=4317):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, status, payload, content_type='application/json; charset=utf-8', download=None):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'SAMEORIGIN')
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            if content_type.startswith('text/html') and not self.path.startswith('/report'):
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def local(self):
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return host in allowed and self.headers.get('Sec-Fetch-Site') not in ('cross-site',)

        def do_GET(self):
            if not self.local():
                return self.send(403, {'error': 'Acesso permitido somente pela aplicação local.'})
            url = urlsplit(self.path)
            params = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == '/api/health':
                    return self.send(200, {'ok': True, 'application': 'mur'})
                if url.path == '/api/state':
                    return self.send(200, dict(store.state(), csrf=token))
                if url.path == '/api/limits':
                    return self.send(200, store.limits.snapshot())
                if url.path == '/api/overview':
                    return self.send(200, store.overview(params))
                if url.path == '/api/sessions':
                    return self.send(200, store.sessions_page(params))
                if url.path == '/api/session':
                    detail = store.session_detail(params.get('id', ''), max(0, int(params.get('offset', 0))))
                    return self.send(200 if detail else 404, detail or {'error': 'Sessão não encontrada.'})
                if url.path == '/api/replay/sessions':
                    rows = replay.library_sessions(store)
                    return self.send(200, {'rows': rows, 'default': rows[0]['id'] if rows else ''})
                if url.path == '/api/replay/compare':
                    return self.send(200, {'rows': replay.compare(store)})
                if url.path == '/api/replay':
                    external = params.get('id', '')
                    try:
                        data = replay.replay(external, store.data_dir / 'replay-cache', store.rates, Path(store.settings['sources']['claude']) / 'projects')
                    except FileNotFoundError:
                        return self.send(404, {'error': 'A transcrição desta sessão não está neste computador.'})
                    return self.send(200, dict(data, usage=replay.session_usage(store, external)))
                if url.path == '/api/live':
                    return self.send(200, {'rows': store.live()})
                if url.path == '/api/transfer':
                    if store.progress['running'] or not store.state()['metadata'].get('initial_scan_complete'):
                        raise ValueError('Conclua a leitura dos registros antes de exportar.')
                    body, packet = packet_bytes(store)
                    return self.send(200, body, 'application/octet-stream', 'MUR-7-dias.mur')
                if url.path == '/api/export':
                    result = store.sessions_page(dict(params, offset=0, limit=500))
                    rows = result['rows']
                    while len(rows) < result['total']:
                        rows.extend(store.sessions_page(dict(params, offset=len(rows), limit=500))['rows'])
                    out = io.StringIO()
                    fields = ['external_id', 'provider', 'title', 'project', 'models', 'machines', 'tokens', 'usd', 'events', 'last_ts']
                    writer = csv.DictWriter(out, fields, extrasaction='ignore')
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for k, v in row.items()})
                    return self.send(200, ('\ufeff' + out.getvalue()).encode(), 'text/csv; charset=utf-8', 'consumo-agentes.csv')
                if url.path in ('/report', '/report/'):
                    report = store.data_dir / 'report.html'
                    if not report.is_file():
                        return self.send(404, {'error': 'Nenhum relatório salvo neste perfil.'})
                    return self.send(200, report.read_bytes(), 'text/html; charset=utf-8')
                files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/theme.js': ('theme.js', 'text/javascript; charset=utf-8'), '/flow.js': ('flow.js', 'text/javascript; charset=utf-8'), '/flow.css': ('flow.css', 'text/css; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml'), '/fonts/source-serif-4.woff2': ('fonts/source-serif-4-latin-opsz-normal.woff2', 'font/woff2'), '/fonts/geist.woff2': ('fonts/geist-latin-wght-normal.woff2', 'font/woff2'), '/fonts/geist-mono.woff2': ('fonts/geist-mono-latin-wght-normal.woff2', 'font/woff2')}
                if url.path in files:
                    name, kind = files[url.path]
                    return self.send(200, (BASE / 'web' / name).read_bytes(), kind)
                self.send(404, {'error': 'Página não encontrada.'})
            except (ValueError, KeyError) as error:
                self.send(400, {'error': clean_text(str(error), 300)})
            except Exception as error:
                self.send(500, {'error': 'Não foi possível concluir a consulta. Tente novamente.', 'detail': clean_text(str(error), 200)})

        def do_POST(self):
            if not self.local() or self.headers.get('X-Local-Token') != token:
                return self.send(403, {'error': 'Reabra o painel local para confirmar esta ação.'})
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return self.send(403, {'error': 'Origem não autorizada.'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if self.path == '/api/import':
                    if not 0 < length <= 32 * 1024 * 1024:
                        return self.send(413, {'error': 'O pacote deve ter até 32 MB.'})
                    packet = decode_packet(self.rfile.read(length))
                    if not isinstance(packet, dict):
                        raise ValueError('Pacote inválido.')
                    result = import_packet(store, packet)
                    return self.send(200, {'ok': True, 'totals': result['totals']})
                if not 0 <= length <= 64000:
                    return self.send(413, {'error': 'Solicitação muito grande.'})
                data = json.loads(self.rfile.read(length) or '{}')
                if not isinstance(data, dict):
                    raise ValueError('Dados inválidos.')
                if self.path == '/api/refresh':
                    store.save_settings(dict(store.settings, setupComplete=True))
                    store.wake.set()
                    return self.send(202, {'ok': True, 'running': store.progress['running']})
                if self.path == '/api/project':
                    store.assign_project(str(data.get('id', '')), str(data.get('project', '')))
                    return self.send(200, {'ok': True})
                if self.path == '/api/limits/refresh':
                    if not store.settings['liveLimitsEnabled']:
                        raise ValueError('Ative a consulta de saldo para atualizar os limites.')
                    connect = data.get('connectClaude', False)
                    if not isinstance(connect, bool):
                        raise ValueError('Preferência de conexão inválida.')
                    store.limits.request_refresh(connect)
                    return self.send(202, {'ok': True})
                if self.path == '/api/settings':
                    settings = json.loads(json.dumps(store.settings))
                    if 'liveLimitsEnabled' in data:
                        if not isinstance(data['liveLimitsEnabled'], bool):
                            raise ValueError('Preferência de consulta inválida.')
                        settings['liveLimitsEnabled'] = data['liveLimitsEnabled']
                    if 'monthly' in data:
                        raise ValueError('Atualize o aplicativo e informe os valores na lista de assinaturas.')
                    if 'subscriptions' in data:
                        rows = validate_subscriptions(data['subscriptions'], store.accounts.snapshot(store.home,settings['sources']))
                        settings['subscriptions'] = [dict(row,label=clean_text(row['label'],120)) for row in rows]
                        settings['billingReviewed'] = True
                    interval = int(data.get('refreshSeconds', settings['refreshSeconds']))
                    if not 30 <= interval <= 3600:
                        raise ValueError('A atualização deve ocorrer entre 30 e 3.600 segundos.')
                    settings['refreshSeconds'] = interval
                    if 'machineLabel' in data:
                        label = clean_text(data['machineLabel'], 120).strip()
                        if not label:
                            raise ValueError('Informe um nome para este computador.')
                        settings['machineLabel'] = label
                    if 'timezone' in data:
                        zone = str(data['timezone'])
                        ZoneInfo(zone)
                        settings['timezone'] = zone
                    for name in ('codex', 'claude', 'grok'):
                        if name in data.get('sources', {}):
                            path = Path(str(data['sources'][name])).expanduser()
                            if not path.is_absolute():
                                raise ValueError('Use o caminho completo da pasta de registros.')
                            settings['sources'][name] = str(path)
                    store.save_settings(settings)
                    store.limits.request_refresh()
                    return self.send(200, {'ok': True})
                self.send(404, {'error': 'Ação não encontrada.'})
            except (TypeError, ValueError, KeyError, zlib.error) as error:
                self.send(400, {'error': clean_text(str(error), 300)})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='Consulta local de históricos e consumo de agentes.')
    parser.add_argument('--port', type=int, default=4317)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--scan-once', action='store_true')
    args = parser.parse_args()
    store = Store(args.data_dir)
    if args.scan_once:
        ok = store.scan()
        print(json.dumps(store.state()['counts'] | {'ok': ok, 'error': store.progress['error']}))
        raise SystemExit(0 if ok else 1)
    server = create_server(store, args.port)
    store.start()
    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.ready_file.with_suffix('.tmp')
        temporary.write_text(json.dumps({'port': server.server_port, 'pid': os.getpid()}))
        temporary.chmod(0o600)
        temporary.replace(args.ready_file)
    def shutdown(*_):
        store.stop.set()
        store.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if args.parent_pid:
        def watch_parent():
            while not store.stop.wait(2):
                if os.getppid() != args.parent_pid:
                    shutdown()
                    return
        threading.Thread(target=watch_parent, daemon=True, name='app-lifecycle').start()
    print('MUR pronto.', flush=True)
    server.serve_forever(poll_interval=0.5)
    server.server_close()


if __name__ == '__main__':
    main()
