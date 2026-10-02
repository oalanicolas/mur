import argparse
import base64
import hashlib
import json
from pathlib import Path
import socket
import time
import zlib

from core import Store


def packet_bytes(store, end=None):
    end = end or time.time()
    start = end - 7 * 86400
    with store.connect() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM events WHERE ts>=? AND ts<? ORDER BY ts,id', (start, end))]
        sids = {r['sid'] for r in rows}
        sessions = [dict(r) for r in c.execute('SELECT * FROM sessions') if r['id'] in sids]
    packet = {'version': 1, 'machine': store.settings['deviceId'], 'label': store.settings['machineLabel'], 'hostname': socket.gethostname(),
              'start': start, 'end': end, 'collected_at': time.time(), 'sessions': sessions, 'events': rows,
              'coverage': store.state()['metadata'], 'counts': store.state()['counts'],
              'messagesIncluded': False}
    body = json.dumps(packet, ensure_ascii=False, separators=(',', ':')).encode()
    return zlib.compress(body, 9), packet


def export(end, output):
    store = Store()
    if not store.scan():
        raise RuntimeError(store.progress['error'])
    zipped, packet = packet_bytes(store, end)
    rows, sessions, start = packet['events'], packet['sessions'], packet['start']
    output.mkdir(parents=True, exist_ok=True)
    (output/'packet.json.zlib').write_bytes(zipped)
    encoded = base64.b64encode(zipped).decode()
    chunk_size = 48000
    for i, offset in enumerate(range(0, len(encoded), chunk_size)):
        (output/f'chunk-{i:04}.txt').write_text(encoded[offset:offset+chunk_size])
    summary = {'sha256': hashlib.sha256(zipped).hexdigest(), 'bytes': len(zipped), 'chunks': (len(encoded)+chunk_size-1)//chunk_size,
               'start': start, 'end': end, 'sessions': len(sessions), 'events': len(rows),
               'tokens': sum(r['tokens'] for r in rows), 'usd': sum(r['usd'] or 0 for r in rows),
               'unpriced_tokens': sum(r['tokens'] for r in rows if r['usd'] is None), 'coverage': packet['coverage']}
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--end', type=float, default=time.time())
    parser.add_argument('--output', type=Path, default=Path.home()/'Downloads/MUR-export')
    args = parser.parse_args()
    export(args.end, args.output)
