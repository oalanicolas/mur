import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


def main(executable):
    root = Path(tempfile.mkdtemp(prefix='mur-windows-qa-'))
    home, profile, output = root / 'home', root / 'profile', root / 'result'
    home.mkdir()
    output.mkdir()
    log = home / '.codex/sessions/fixture.jsonl'
    log.parent.mkdir(parents=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = [{'timestamp': now, 'type': 'session_meta', 'payload': {'id': 'windows-fixture', 'cwd': str(home / 'Code/MeuProjeto')}},
            {'timestamp': now, 'type': 'turn_context', 'payload': {'model': 'gpt-6.1-sol', 'effort': 'low'}},
            {'timestamp': now, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'total_token_usage': {'input_tokens': 100, 'output_tokens': 10}, 'last_token_usage': {'input_tokens': 100, 'output_tokens': 10}}}}]
    log.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    before = hashlib.sha256(log.read_bytes()).digest()
    environment = dict(os.environ, MUR_HOME=str(home), MUR_DATA_DIR=str(profile))
    process = subprocess.Popen([str(executable), '--validation', str(output)], env=environment)
    try:
        code = process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(10)
        raise RuntimeError('O executável Windows não concluiu o teste em 120 segundos.')
    report = json.loads((output / 'native-windows.json').read_text(encoding='utf-8'))
    assert code == 0 and report['ok'], json.dumps(report, ensure_ascii=False)
    assert hashlib.sha256(log.read_bytes()).digest() == before
    with socket.socket() as connection:
        connection.settimeout(2)
        assert connection.connect_ex(('127.0.0.1', report['port'])) != 0, 'Servidor continuou ativo após sair.'
    assert json.loads((profile / 'settings.json').read_text(encoding='utf-8'))['liveLimitsEnabled'] is False
    print(json.dumps(report | {'historyPreserved': True, 'backendStopped': True, 'executable': executable.name}, ensure_ascii=False))


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
