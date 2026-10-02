import datetime as dt
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

from core import Store, clean_text, timestamp
from server import create_server


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'data', self.root/'home')
        self.c = self.store.connect()
        self.sid = self.store.session(self.c,'OpenAI','test-session',title='Projeto teste',cwd='/Code/Jogo')

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def counter(self, key, ts, total, last, source='a'):
        self.store.record(self.c,self.sid,ts,'gpt-6.1-sol','low','counter',{'total_token_usage':total,'last_token_usage':last},source,key)

    def test_counter_baseline_resume_and_duplicates(self):
        a={'input_tokens':100,'cached_input_tokens':60,'output_tokens':10,'reasoning_output_tokens':4}
        b={'input_tokens':250,'cached_input_tokens':130,'output_tokens':30,'reasoning_output_tokens':9}
        last={'input_tokens':150,'cached_input_tokens':70,'output_tokens':20,'reasoning_output_tokens':5}
        self.counter('a1',1000,a,a)
        self.counter('duplicate',1000,a,a,'backup')
        self.counter('a2',2000,b,last)
        self.store.reconcile(self.c,self.sid)
        row=self.c.execute('SELECT SUM(tokens),SUM(cache),SUM(reasoning),COUNT(*) FROM events').fetchone()
        self.assertEqual(tuple(row),(280,130,9,2))
        self.store.reconcile(self.c,self.sid)
        self.assertEqual(self.c.execute('SELECT SUM(tokens) FROM events').fetchone()[0],280)

    def test_missing_baseline_and_reset_use_only_last_call(self):
        self.counter('a',1000,{'input_tokens':10000,'output_tokens':1000},{'input_tokens':100,'output_tokens':10})
        self.counter('b',2000,{'input_tokens':900,'output_tokens':90},{'input_tokens':50,'output_tokens':5})
        self.store.reconcile(self.c,self.sid)
        self.assertEqual(self.c.execute('SELECT SUM(tokens) FROM events').fetchone()[0],165)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM events WHERE gap!=''").fetchone()[0],2)

    def test_equal_timestamp_keeps_record_order(self):
        self.counter('z-first',1000,{'input_tokens':1000,'output_tokens':0},{})
        self.counter('b-next',1000,{'input_tokens':1100,'output_tokens':10},{'input_tokens':100,'output_tokens':10})
        self.counter('a-last',1000,{'input_tokens':1220,'output_tokens':25},{'input_tokens':120,'output_tokens':15})
        self.store.reconcile(self.c,self.sid)
        self.assertEqual(self.c.execute('SELECT SUM(tokens) FROM events').fetchone()[0],245)

    def test_zero_usage_receipt_keeps_session_evidence(self):
        sid=self.store.session(self.c,'xAI','zero')
        u=dict.fromkeys(('input','cache','write','write1h','output','reasoning'),0)
        self.store.record(self.c,sid,1000,'unknown','','usage',u,'grok','zero')
        self.store.reconcile(self.c,sid)
        self.assertEqual(tuple(self.c.execute('SELECT tokens,usd FROM events WHERE sid=?',(sid,)).fetchone()),(0,None))

    def test_aggregates_obey_same_filters(self):
        self.store.save_settings(dict(self.store.settings, subscriptions=[{'id':p,'provider':p,'label':p,'accountId':'','monthlyUsd':v,'quantity':1,'startDate':'2000-01-01'} for p,v in {'OpenAI':600,'Anthropic':600,'xAI':300}.items()]))
        self.store.meta(self.c, 'audit_window', [timestamp('2026-09-23T03:00:00Z'), timestamp('2026-09-30T03:00:00Z')])
        self.counter('one',timestamp('2026-09-24T04:00:00Z'),{'input_tokens':100,'output_tokens':10},{'input_tokens':100,'output_tokens':10})
        self.store.reconcile(self.c,self.sid);self.c.commit()
        result=self.store.overview({'period':'audit','provider':'OpenAI'})
        self.assertEqual(result['totals']['tokens'],110)
        for dimension in ('providers','models','projects','days'):
            self.assertEqual(sum(r['tokens'] for r in result[dimension]),110)
        self.assertEqual(result['totals']['allocated'],350)
        self.assertEqual(self.store.overview({'period':'audit','provider':'xAI'})['totals']['tokens'],0)

    def test_claude_streamed_message_max_and_earliest_timestamp(self):
        sid=self.store.session(self.c,'Anthropic','claude')
        for i,out in enumerate((10,25,20)):
            u={'input':50,'cache':200,'write':30,'write1h':10,'output':out,'reasoning':3,'event_id':'Anthropic:msg-test','requests':1}
            self.store.record(self.c,sid,1000+i,'claude-sonnet-5','', 'usage',u,'a','c'+str(i))
        self.store.reconcile(self.c,sid)
        row=self.c.execute('SELECT tokens,ts,reasoning FROM events WHERE sid=?',(sid,)).fetchone()
        self.assertEqual(tuple(row),(305,1000,3))

    def test_receipt_does_not_duplicate_native_usage(self):
        u={'input':10,'cache':0,'write':0,'write1h':0,'output':5,'reasoning':0,'requests':None}
        self.store.record(self.c,self.sid,1000,'gpt-6.1-sol','','receipt',u,'audit','r')
        self.store.record(self.c,self.sid,1000,'gpt-6.1-sol','','usage',dict(u,event_id='native'),'log','n')
        self.store.reconcile(self.c,self.sid)
        self.assertEqual(self.c.execute('SELECT SUM(tokens),COUNT(*) FROM events').fetchone()[:],(15,1))

    def test_grok_recorded_cost_and_missing_rate(self):
        u={'input':10,'cache':2,'write':0,'write1h':0,'output':4,'reasoning':1,'reported_usd':0.042}
        self.assertEqual(self.store.cost('xAI','unknown-model',u),0.042)
        self.assertIsNone(self.store.cost('OpenAI','unknown-model',u))
        self.assertIsNone(self.store.cost('OpenAI','gpt-6.1-sol',dict(u,request_input=300000)))

    def test_invalid_categories_are_reported(self):
        self.counter('bad',1000,{'input_tokens':10,'cached_input_tokens':20},{'input_tokens':10,'cached_input_tokens':20})
        self.store.reconcile(self.c,self.sid)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM skipped_usage').fetchone()[0],1)

    def test_append_only_index_and_partial_tail(self):
        p=self.root/'rollout-test-session.jsonl'
        meta={'type':'session_meta','timestamp':'2026-09-30T00:00:00Z','payload':{'id':'append','cwd':'/Code/Jogo'}}
        counter={'type':'event_msg','timestamp':'2026-09-30T00:00:01Z','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':10,'output_tokens':2},'last_token_usage':{'input_tokens':10,'output_tokens':2}}}}
        p.write_text(json.dumps(meta)+'\n'+json.dumps(counter)+'\n{"type":')
        affected=self.store.index_jsonl(self.c,p,'codex')
        for sid in affected:self.store.reconcile(self.c,sid)
        first=self.c.execute('SELECT SUM(tokens) FROM events').fetchone()[0]
        self.assertEqual(first,12)
        self.assertEqual(self.store.index_jsonl(self.c,p,'codex'),set())
        with p.open('a') as f:f.write('"event_msg","timestamp":"2026-09-30T00:00:02Z","payload":{"type":"task_complete"}}\n')
        self.store.index_jsonl(self.c,p,'codex')
        self.assertEqual(self.c.execute("SELECT status FROM sessions WHERE external_id='append'").fetchone()[0],'completed')
        self.assertEqual(self.c.execute('SELECT SUM(tokens) FROM events').fetchone()[0],first)

    def test_custom_dates_and_half_open_window(self):
        start,end=self.store.bounds({'period':'custom','start':'2026-09-30','end':'2026-09-30'})
        self.assertEqual(start,timestamp('2026-09-30T03:00:00Z'))
        self.assertEqual(end-start,86400)
        with self.assertRaises(ValueError):self.store.bounds({'period':'custom','start':'2026-10-02','end':'2026-09-30'})

    def test_project_override_survives_source_updates(self):
        self.c.commit()
        self.store.assign_project(self.sid,'Meu projeto')
        self.store.session(self.c,'OpenAI','test-session',cwd='/Code/Outro')
        row=self.c.execute('SELECT project,confidence FROM sessions WHERE id=?',(self.sid,)).fetchone()
        self.assertEqual(tuple(row),('Meu projeto','manual'))

    def test_redaction(self):
        self.assertNotIn('sk-abcdefghijklmnopqrstuv',clean_text('Chave sk-abcdefghijklmnopqrstuv'))
        self.assertIn('[ocultado]',clean_text('API_KEY=abcdefghijklmnopqrst'))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.store=Store(Path(cls.temp.name)/'data',Path(cls.temp.name)/'home')
        cls.server=create_server(cls.store,0)
        cls.port=cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()

    def request(self,path,method='GET',data=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        c.request(method,path,body=json.dumps(data) if data is not None else None,headers=headers or {})
        response=c.getresponse();body=response.read();status=response.status;hs=dict(response.getheaders());c.close()
        return status,body,hs

    def test_page_and_assets(self):
        for path in ['/','/app.js','/style.css','/favicon.svg']:
            status,body,headers=self.request(path);self.assertEqual(status,200);self.assertGreater(len(body),20)
        self.assertIn("default-src 'self'",self.request('/')[2]['Content-Security-Policy'])

    def test_host_and_csrf_guards(self):
        self.assertEqual(self.request('/api/state',headers={'Host':'malicious.example'})[0],403)
        self.assertEqual(self.request('/api/refresh','POST',{})[0],403)
        token=json.loads(self.request('/api/state')[1])['csrf']
        headers={'X-Local-Token':token,'Content-Type':'application/json','Origin':'https://malicious.example'}
        self.assertEqual(self.request('/api/refresh','POST',{},headers)[0],403)
        headers.pop('Origin');self.assertEqual(self.request('/api/refresh','POST',{},headers)[0],202)

    def test_settings_persist_and_reject_invalid_values(self):
        token=json.loads(self.request('/api/state')[1])['csrf'];headers={'X-Local-Token':token}
        subscription={'id':'account-test','provider':'OpenAI','label':'Conta de teste','monthlyUsd':-1,'quantity':1}
        self.assertEqual(self.request('/api/settings','POST',{'subscriptions':[subscription]},headers)[0],400)
        subscription['monthlyUsd']=700
        self.assertEqual(self.request('/api/settings','POST',{'subscriptions':[subscription],'refreshSeconds':300},headers)[0],200)
        self.assertEqual(json.loads(self.store.settings_path.read_text())['monthly']['OpenAI'],700)

    def test_no_path_traversal_and_invalid_date(self):
        self.assertEqual(self.request('/../../settings.json')[0],404)
        self.assertEqual(self.request('/api/overview?period=custom&start=no&end=no')[0],400)


if __name__=='__main__':
    unittest.main(verbosity=2)
