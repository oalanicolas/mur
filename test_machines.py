import copy
import tempfile
from pathlib import Path
import unittest

from core import Store, timestamp
from import_machine import import_packet


class MachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'data',self.root/'home')
        self.store.save_settings(dict(self.store.settings, subscriptions=[{'id':p,'provider':p,'label':p,'accountId':'','monthlyUsd':v,'quantity':1,'startDate':'2000-01-01'} for p,v in {'OpenAI':600,'Anthropic':600,'xAI':300}.items()]))
        self.end = timestamp('2026-10-01T16:00:00Z')
        with self.store.connect() as c:
            self.sid = self.store.session(c,'OpenAI','same-session',title='Trabalho compartilhado',cwd='/Code/Game')
            self.store.record(c,self.sid,self.end-1000,'gpt-6.1-sol','','usage',{'input':100,'cache':50,'write':0,'write1h':0,'output':10,'reasoning':2,'requests':1,'event_id':'shared'},'local','record')
            self.store.reconcile(c,self.sid)
            self.session = dict(c.execute('SELECT * FROM sessions WHERE id=?',(self.sid,)).fetchone())
            self.event = dict(c.execute('SELECT * FROM events WHERE id=?',('shared',)).fetchone())
        remote=copy.deepcopy(self.event);remote['id']='remote-only';remote['ts']+=1
        self.packet={'version':1,'machine':'macbook','label':'MacBook Pro','hostname':'test-macbook','start':self.end-7*86400,'end':self.end,'collected_at':self.end,'coverage':{'initial_scan_complete':True},'sessions':[self.session],'events':[self.event,remote]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_dedup_idempotence_and_monthly_once(self):
        result=import_packet(self.store,self.packet)
        self.assertEqual(result['totals']['tokens'],320)
        self.assertEqual(result['totals']['sessions'],1)
        self.assertEqual(result['totals']['allocated'],350)
        self.assertEqual(result['duplicateEvents'],1)
        self.assertEqual(sum(r['tokens'] for r in result['machines']),320)
        self.assertEqual(import_packet(self.store,self.packet)['totals'],result['totals'])

    def test_filter_and_exports_use_same_machine(self):
        import_packet(self.store,self.packet)
        for machine in ['local','macbook']:
            overview=self.store.overview({'period':'combined','machine':machine})
            rows=self.store.sessions_page({'period':'combined','machine':machine})
            self.assertEqual(overview['totals']['tokens'],160)
            self.assertEqual(sum(r['tokens'] for r in rows['rows']),160)
            self.assertTrue(all(r['machines']==machine for r in rows['rows']))

    def test_local_reindex_keeps_imported_events(self):
        import_packet(self.store,self.packet)
        with self.store.connect() as c:self.store.reconcile(c,self.sid)
        self.assertEqual(self.store.overview({'period':'combined'})['totals']['tokens'],320)
        rebuilt=Store(self.root/'data',self.root/'home')
        self.assertEqual(rebuilt.overview({'period':'combined'})['totals']['tokens'],320)

    def test_remote_snapshot_not_reported_as_live(self):
        p=copy.deepcopy(self.packet)
        s=p['sessions'][0];s['id']='OpenAI:remote';s['external_id']='remote';s['status']='running';s['last_ts']=self.end
        for e in p['events']:e['sid']=s['id'];e['id']='remote-'+e['id']
        import_packet(self.store,p)
        self.assertNotIn('OpenAI:remote',{r.get('sid') for r in self.store.live()})
        rebuilt=Store(self.root/'data',self.root/'home')
        self.assertNotIn('OpenAI:remote',{r.get('sid') for r in rebuilt.live()})

    def test_invalid_or_incomplete_packet_does_not_change_totals(self):
        for key,value in [('ts',self.end),('tokens',-10)]:
            p=copy.deepcopy(self.packet);p['events'][0][key]=value
            with self.assertRaises(ValueError):import_packet(self.store,p)
        p=copy.deepcopy(self.packet);p['coverage']['source_issues']=['unreadable']
        with self.assertRaises(ValueError):import_packet(self.store,p)
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM imported_events').fetchone()[0],0)

    def test_manual_classification_preserved(self):
        self.store.assign_project(self.sid,'Projeto revisado')
        import_packet(self.store,self.packet)
        detail=self.store.session_detail(self.sid)
        self.assertEqual(detail['session']['project'],'Projeto revisado')
        self.assertEqual(set(detail['session']['machines']),{'local','macbook'})


if __name__=='__main__':unittest.main(verbosity=2)
