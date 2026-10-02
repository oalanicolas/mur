import datetime as dt
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zlib

from core import Store
from export_machine import packet_bytes
from import_machine import decode_packet, import_packet
import replay
from server import create_server


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'profile', self.root/'new-user')
        self.server = create_server(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path, data=None, raw=False):
        client = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers = {}
        if data is not None:
            client.request('GET','/api/state')
            state = json.loads(client.getresponse().read())
            headers['X-Local-Token'] = state['csrf']
        client.request('GET' if data is None else 'POST',path,body=data if raw else json.dumps(data) if data is not None else None,headers=headers)
        response = client.getresponse()
        result = response.status,response.read()
        client.close()
        return result

    def test_fresh_profile_is_private_empty_and_unconfigured(self):
        self.assertFalse(self.store.settings['setupComplete'])
        self.assertEqual(sum(self.store.settings['monthly'].values()),0)
        self.assertFalse(self.store.state()['hasReport'])
        self.assertEqual(self.store.state()['counts']['events'],0)
        self.assertTrue(all(str(self.root/'new-user') in p for p in self.store.settings['sources'].values()))
        self.assertEqual(self.request('/report')[0],404)
        self.assertEqual(json.loads(self.request('/api/replay/sessions')[1]),{'rows':[],'default':''})
        self.assertEqual(self.request('/api/transfer')[0],400)
        restarted = Store(self.root/'profile', self.root/'new-user')
        self.assertFalse(restarted.settings['setupComplete'])
        self.assertEqual(restarted.settings['deviceId'],self.store.settings['deviceId'])

    def test_worker_waits_for_start(self):
        worker = self.store.start()
        try:
            time.sleep(.05)
            self.assertNotIn('last_scan',self.store.state()['metadata'])
            self.assertEqual(self.request('/api/refresh',{})[0],202)
            for _ in range(50):
                if self.store.state()['metadata'].get('initial_scan_complete'):break
                time.sleep(.02)
            self.assertTrue(self.store.state()['metadata']['initial_scan_complete'])
        finally:
            self.store.stop.set();self.store.wake.set();worker.join(2)

    def test_sources_and_timezone_persist_without_other_user_defaults(self):
        source = self.root/'custom-claude'
        settings = {'machineLabel':'Mac de teste','timezone':'Asia/Tokyo','sources':{'claude':str(source)}}
        self.assertEqual(self.request('/api/settings',settings)[0],200)
        restarted = Store(self.root/'profile',self.root/'new-user')
        self.assertEqual(restarted.settings['machineLabel'],'Mac de teste')
        self.assertEqual(restarted.settings['sources']['claude'],str(source))
        start,end = restarted.bounds({'period':'custom','start':'2026-10-01','end':'2026-10-01'})
        self.assertEqual(dt.datetime.fromtimestamp(start,dt.timezone.utc).isoformat(),'2026-09-30T15:00:00+00:00')
        self.assertEqual(end-start,86400)
        self.assertEqual(self.request('/api/settings',{'timezone':'Invalid/Timezone'})[0],400)
        self.assertEqual(self.request('/api/settings',{'sources':{'claude':'relative/path'}})[0],400)

    def test_arbitrary_machine_roundtrip_without_messages(self):
        now = time.time()
        with self.store.connect() as c:
            sid = self.store.session(c,'OpenAI','portable',title='Meu projeto')
            self.store.record(c,sid,now-10,'gpt-6.1-sol','','usage',{'input':20,'cache':0,'write':0,'write1h':0,'output':5,'reasoning':0,'event_id':'portable-use','requests':1},'fixture','use')
            self.store.reconcile(c,sid)
            self.store.message(c,sid,now-10,'user','PRIVATE MESSAGE CANARY')
            self.store.meta(c,'initial_scan_complete',True)
        body,packet = packet_bytes(self.store,now)
        self.assertNotIn(b'PRIVATE MESSAGE CANARY',zlib.decompress(body))
        self.assertNotEqual(packet['machine'],'macbook')
        self.assertEqual(self.request('/api/import',body,True)[0],400)
        other = Store(self.root/'second-profile',self.root/'second-user')
        result = import_packet(other,decode_packet(body))
        self.assertEqual(result['totals']['tokens'],25)
        self.assertEqual(result['totals']['monthly'],0)
        self.assertEqual(import_packet(other,decode_packet(body))['totals']['tokens'],25)
        self.assertEqual(other.state()['counts']['messages'],0)

    def test_invalid_packet_has_no_database_effect(self):
        before = self.store.state()['counts']
        for body in (b'not-zlib',zlib.compress(b'[]'),zlib.compress(b'{"machine":"../../escape","version":1}')):
            self.assertEqual(self.request('/api/import',body,True)[0],400)
        self.assertEqual(self.store.state()['counts'],before)

    def test_replay_uses_configured_source_and_generic_session(self):
        external = '12345678-1234-1234-1234-123456789012'
        root = self.root/'custom-claude'
        path = root/'projects/any-project'/f'{external}.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'type':'user','timestamp':'2026-10-01T12:00:00Z','message':{'content':'Analise meu aplicativo'}})+'\n')
        self.store.save_settings(dict(self.store.settings,sources=dict(self.store.settings['sources'],claude=str(root))))
        with self.store.connect() as c:
            self.store.session(c,'Anthropic',external)
        rows = replay.library_sessions(self.store)
        self.assertEqual(rows[0]['id'],external)
        self.assertEqual(rows[0]['ask'],'Analise meu aplicativo')
        self.assertEqual(self.request('/api/replay?id='+external)[0],200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
