import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import time
import zlib

from core import Store, clean_text


def decode_packet(body):
    decoder = zlib.decompressobj()
    raw = decoder.decompress(body, 256 * 1024 * 1024 + 1)
    if len(raw) > 256 * 1024 * 1024 or not decoder.eof or decoder.unused_data:
        raise ValueError('Pacote inválido ou maior que 256 MB descompactados.')
    return json.loads(raw)


def import_packet(store, packet):
    machine = packet.get('machine', '')
    if packet.get('version') != 1 or not isinstance(machine, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', machine):
        raise ValueError('Formato ou computador não reconhecido.')
    if machine in ('local', store.settings['deviceId']):
        raise ValueError('Este pacote pertence ao próprio computador. Importe-o no outro Mac.')
    start, end = float(packet['start']), float(packet['end'])
    if not math.isfinite(start+end) or start <= 0 or abs(end-start-7*86400) > 1:
        raise ValueError('O pacote precisa abranger exatamente sete dias.')
    coverage = packet.get('coverage', {})
    if not coverage.get('initial_scan_complete') or coverage.get('scan_error') or coverage.get('source_issues') or coverage.get('catalog_error'):
        raise ValueError('A coleta remota não terminou sem erros; conferir a cobertura antes de importar.')
    sessions = {s['id']: s for s in packet['sessions']}
    if len(sessions) != len(packet['sessions']):
        raise ValueError('Sessões repetidas no pacote.')
    ids = set()
    for e in packet['events']:
        if e['sid'] not in sessions or e['id'] in ids or not start <= e['ts'] < end:
            raise ValueError('Evento repetido, sem sessão ou fora do período.')
        ids.add(e['id'])
        for key in ('input','cache','write','write1h','output','reasoning','tokens'):
            if not isinstance(e[key], int) or e[key] < 0:
                raise ValueError('Tokens inválidos.')
        if e['tokens'] != e['input']+e['cache']+e['write']+e['output'] or e['write1h'] > e['write']:
            raise ValueError('Categorias de tokens inconsistentes.')
        if e['usd'] is not None and (not isinstance(e['usd'],(int,float)) or not math.isfinite(e['usd']) or e['usd'] < 0):
            raise ValueError('Custo inválido.')
    with store.connect() as c:
        session_columns = [r['name'] for r in c.execute('PRAGMA table_info(sessions)')]
        event_columns = [r['name'] for r in c.execute('PRAGMA table_info(events)')]
        for sid, s in sessions.items():
            if s['provider'] not in ('OpenAI','Anthropic','xAI') or sid != s['provider']+':'+s['external_id']:
                raise ValueError('Identidade de sessão inválida.')
            values = {k: clean_text(s[k], 800) if isinstance(s[k], str) else s[k] for k in session_columns}
            known = store.classifications.get(s['external_id'])
            if known:
                values.update(project=known.get('projectName',''),category=known.get('category',''),confidence=known.get('confidence',''),basis=known.get('basis',''))
            c.execute('INSERT OR IGNORE INTO sessions ('+','.join(session_columns)+') VALUES ('+','.join('?' for _ in session_columns)+')', [values[k] for k in session_columns])
            c.execute('INSERT OR IGNORE INTO session_origins VALUES (?,?)',(sid,machine))
        c.execute('DELETE FROM imported_events WHERE machine=? AND ts>=? AND ts<?',(machine,start,end))
        columns = event_columns+['machine']
        sql = 'INSERT INTO imported_events ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+') ON CONFLICT(id) DO UPDATE SET '+','.join(f'{k}=excluded.{k}' for k in columns if k!='id')
        c.executemany(sql, [[e[k] for k in event_columns]+[machine] for e in packet['events']])
        c.execute('INSERT OR REPLACE INTO machine_imports VALUES (?,?,?,?,?,?,?,?,?,?)',(machine,clean_text(packet['label'],120),clean_text(packet['hostname'],120),start,end,packet['collected_at'],time.time(),len(ids),len(sessions),json.dumps(coverage)))
        store.meta(c,'combined_window',[start,end])
        store.meta(c,'combined_imported_at',time.time())
    return store.overview({'period':'combined'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('packet',type=Path)
    parser.add_argument('--sha256',required=True)
    args = parser.parse_args()
    body=args.packet.read_bytes()
    if hashlib.sha256(body).hexdigest()!=args.sha256:
        raise SystemExit('O arquivo transferido não confere com a origem.')
    result=import_packet(Store(),decode_packet(body))
    print(json.dumps({'totals':result['totals'],'machines':result['machines'],'duplicateEvents':result['duplicateEvents']},ensure_ascii=False))
