import argparse
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from urllib.parse import urlsplit

from core import Store
from server import create_server


class ExportAPI:
    def __init__(self, origin, validation=None):
        self._origin = origin
        self._window = None
        self._validation = validation

    def save_export(self, path, token):
        import webview
        current = urlsplit(self._window.get_current_url() or '')
        expected = urlsplit(self._origin)
        destination = urlsplit(path)
        if (not isinstance(token, str) or not hmac.compare_digest(token, webview.token)
                or (current.scheme, current.netloc) != (expected.scheme, expected.netloc)
                or destination.scheme or destination.netloc or destination.fragment
                or destination.path not in ('/api/export', '/api/transfer')):
            raise ValueError('Origem ou exportação não autorizada.')
        filename = 'MUR-7-dias.mur' if destination.path == '/api/transfer' else 'MUR-consumo.csv'
        if self._validation:
            target = self._validation / filename
        else:
            selection = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename)
            if not selection:
                return {'saved': False}
            target = Path(selection[0])
        temporary = None
        try:
            with urllib.request.urlopen(self._origin + path, timeout=60) as response:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.mur-export-', delete=False) as output:
                    temporary = Path(output.name)
                    while chunk := response.read(65536):
                        output.write(chunk)
            temporary.replace(target)
            return {'saved': True}
        finally:
            if temporary and temporary.exists():
                temporary.unlink()


def validate_window(window, store, output, origin):
    checks = []

    def wait(script):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if window.evaluate_js(script):
                return
            time.sleep(.15)
        raise RuntimeError('Tempo esgotado: ' + script)

    def check(value, label):
        if not value:
            raise RuntimeError(label)
        checks.append(label)

    try:
        window.events.loaded.wait(45)
        wait("typeof appState!=='undefined' && !!appState && !document.querySelector('#welcome').hidden && !!window.pywebview?.api?.save_export")
        check(window.title == 'MUR', 'Janela nativa MUR sem barra de endereço')
        check(window.evaluate_js("appState.counts.events===0 && !appState.limits.enabled && !appState.settings.setupComplete"), 'Primeira abertura vazia e saldos desativados')
        check(window.evaluate_js("!JSON.stringify(appState).includes('SYNTHETIC-PRIVATE-TOKEN')"), 'Credenciais ausentes da interface')
        window.evaluate_js("document.querySelector('#start-analysis').click()")
        wait("appState.metadata.initial_scan_complete && document.querySelector('#metric-tokens').textContent==='110'")
        check(True, 'Histórico sintético analisado pelo executável')
        window.evaluate_js("document.querySelector('[name=provider]').value='Anthropic';document.querySelector('[name=provider]').dispatchEvent(new Event('change',{bubbles:true}))")
        wait("!loadingJobs.has('view') && document.querySelector('#metric-tokens').textContent==='0'")
        check(True, 'Filtro de provedor e carregamento concluem no WebView2')
        window.evaluate_js("document.querySelector('[name=provider]').value='';document.querySelector('[name=provider]').dispatchEvent(new Event('change',{bubbles:true}));location.hash='sessions'")
        wait("!loadingJobs.has('view') && !document.querySelector('#sessions').hidden && !document.querySelector('#export').disabled")
        window.evaluate_js("document.querySelector('#export').click()")
        wait("!document.querySelector('#export').disabled && !loadingJobs.has(document.querySelector('#export'))")
        deadline = time.monotonic() + 10
        while not (output / 'MUR-consumo.csv').exists() and time.monotonic() < deadline:
            time.sleep(.1)
        check('windows-fixture' in (output / 'MUR-consumo.csv').read_text(encoding='utf-8-sig'), 'CSV salvo pela ponte nativa de exportação')
        window.evaluate_js("globalThis.exportDenied=false;window.pywebview.api.save_export('https://example.test/api/export',window.pywebview.token).catch(()=>{exportDenied=true})")
        wait('exportDenied===true')
        check(True, 'Ponte de exportação rejeita endereço externo')
        window.evaluate_js("location.hash='settings';document.querySelector('#export-machine').click()")
        deadline = time.monotonic() + 10
        while not (output / 'MUR-7-dias.mur').exists() and time.monotonic() < deadline:
            time.sleep(.1)
        check((output / 'MUR-7-dias.mur').is_file(), 'Coleta de sete dias exportada pelo aplicativo')
        check(not store.settings['liveLimitsEnabled'], 'Nenhum provedor consultado durante o teste')
        (output / 'native-windows.json').write_text(json.dumps({'ok': True, 'checks': checks, 'port': urlsplit(origin).port}, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as error:
        (output / 'native-windows.json').write_text(json.dumps({'ok': False, 'checks': checks, 'error': str(error)}, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        window.destroy()


def main():
    parser = argparse.ArgumentParser(description='MUR — Model Usage Reports')
    parser.add_argument('--validation', type=Path)
    args = parser.parse_args()
    import webview
    store = Store()
    shell_path = store.data_dir / 'desktop.json'
    try:
        previous_port = int(json.loads(shell_path.read_text(encoding='utf-8')).get('port', 0))
    except (OSError, ValueError, TypeError):
        previous_port = 0
    try:
        server = create_server(store, previous_port if 1024 <= previous_port <= 65535 else 0, native_windows=True)
    except OSError:
        server = create_server(store, 0, native_windows=True)
    origin = 'http://127.0.0.1:' + str(server.server_port)
    shell_path.write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    threading.Thread(target=server.serve_forever, daemon=True, name='mur-http').start()
    store.start()
    api = ExportAPI(origin, args.validation)
    webview.settings['ALLOW_FILE_URLS'] = False
    window = webview.create_window('MUR', origin, js_api=api, width=1320, height=900,
                                   min_size=(800, 600), background_color='#171714', text_select=True)
    api._window = window
    def restrict_navigation():
        def starting(_sender, event):
            destination = urlsplit(str(event.Uri))
            expected = urlsplit(origin)
            if str(event.Uri) == 'about:blank' or (destination.scheme, destination.netloc) == (expected.scheme, expected.netloc):
                return
            event.Cancel = True
            if event.IsUserInitiated and destination.scheme in ('https', 'http', 'codex'):
                webbrowser.open(str(event.Uri))
        window.native.browser.webview.NavigationStarting += starting
    window.events.before_show += restrict_navigation
    if args.validation:
        args.validation.mkdir(parents=True, exist_ok=True)
        background = lambda: validate_window(window, store, args.validation, origin)
    else:
        background = None
    try:
        webview.start(background, gui='edgechromium', private_mode=False,
                      storage_path=str(store.data_dir / 'WebView2'),
                      localization={'global.quit': 'Sair do MUR', 'global.cancel': 'Cancelar', 'global.saveFile': 'Salvar arquivo'})
    finally:
        store.stop.set()
        store.wake.set()
        store.limits.wake.set()
        server.shutdown()
        server.server_close()
    if args.validation:
        report = json.loads((args.validation / 'native-windows.json').read_text(encoding='utf-8'))
        return 0 if report['ok'] else 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        if '--validation' in sys.argv:
            import traceback
            output = Path(sys.argv[sys.argv.index('--validation') + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / 'native-windows.json').write_text(json.dumps({'ok': False, 'error': traceback.format_exc()}), encoding='utf-8')
            raise SystemExit(1)
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, 'Não foi possível abrir o MUR. Reinstale pelo instalador para conferir o WebView2 e tente novamente. Seu histórico foi preservado.', 'MUR', 0x10)
        raise
