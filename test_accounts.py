import base64
import json
from pathlib import Path
import tempfile
import unittest

from accounts import AccountRegistry, detect_accounts, validate_subscriptions
from billing import monthly_totals
from core import Store


def encoded_claims(claims):
    return 'header.' + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=') + '.signature'


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root/'home'
        self.sources = {name:str(self.home/('.'+name)) for name in ('codex','claude','grok')}
        self.profile = self.root/'profile'
        self.profile.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))

    def codex(self, account='account-one', plan='pro'):
        token = encoded_claims({'email':'person@example.test','https://api.openai.com/auth':{'chatgpt_account_id':account,'chatgpt_user_id':'user-one','chatgpt_plan_type':plan}})
        self.write(self.home/'.codex/auth.json',{'auth_mode':'chatgpt','tokens':{'id_token':token,'refresh_token':'PRIVATE_REFRESH_TOKEN_CANARY','access_token':'PRIVATE_ACCESS_TOKEN_CANARY'}})
        return token

    def test_detects_all_providers_without_persisting_secrets(self):
        token = self.codex()
        self.write(self.home/'.claude.json',{'oauthAccount':{'accountUuid':'claude-one','organizationUuid':'org-one','emailAddress':'claude@example.test','organizationType':'claude_max','organizationRateLimitTier':'default_claude_max_20x'}})
        self.write(self.home/'.grok/auth.json',{'issuer::client':{'user_id':'grok-one','email':'grok@example.test','key':'PRIVATE_GROK_KEY_CANARY','refresh_token':'PRIVATE_GROK_REFRESH_CANARY'}})
        registry = AccountRegistry(self.profile)
        rows = registry.snapshot(self.home,self.sources,1000)
        self.assertEqual({r['provider'] for r in rows},{'OpenAI','Anthropic','xAI'})
        self.assertEqual(next(r['plan'] for r in rows if r['provider']=='Anthropic'),'Max 20×')
        self.assertEqual(next(r['plan'] for r in rows if r['provider']=='xAI'),'')
        combined = json.dumps(rows)+registry.path.read_text()
        for private in [token,'PRIVATE_','person@example.test','claude@example.test','grok@example.test','account-one','org-one']:
            self.assertNotIn(private,combined)
        self.assertEqual(registry.path.stat().st_mode & 0o777,0o600)

    def test_switch_logout_and_plan_change_keep_observations(self):
        self.codex()
        registry = AccountRegistry(self.profile)
        first = registry.snapshot(self.home,self.sources,1000)[0]
        self.codex('account-two','plus')
        rows = registry.snapshot(self.home,self.sources,1010)
        self.assertEqual(len(rows),2)
        self.assertEqual(sum(r['present'] for r in rows),1)
        self.assertFalse(next(r['present'] for r in rows if r['id']==first['id']))
        self.codex('account-one','promax')
        rows = AccountRegistry(self.profile).snapshot(self.home,self.sources,1020)
        active = next(r for r in rows if r['present'])
        self.assertEqual(active['id'],first['id'])
        self.assertEqual([p['plan'] for p in active['planHistory']],['pro','promax'])
        self.write(self.home/'.codex/auth.json',{})
        self.assertFalse(any(r['present'] for r in registry.snapshot(self.home,self.sources,1030)))

    def test_custom_claude_source_never_uses_default_account(self):
        self.write(self.home/'.claude.json',{'oauthAccount':{'accountUuid':'default','emailAddress':'default@example.test'}})
        alternate = self.root/'alternate'
        sources = dict(self.sources,claude=str(alternate))
        self.assertEqual(detect_accounts(self.home,sources),[])
        self.write(alternate/'.claude.json',{'oauthAccount':{'accountUuid':'alternate','organizationType':'claude_pro'}})
        self.assertEqual(detect_accounts(self.home,sources)[0]['plan'],'Pro')

    def test_api_key_or_invalid_cache_does_not_become_a_subscription(self):
        self.write(self.home/'.codex/auth.json',{'auth_mode':'apikey','OPENAI_API_KEY':'PRIVATE_KEY_CANARY'})
        self.write(self.home/'.grok/auth.json',{'issuer::client':{'key':'PRIVATE_KEY_CANARY'}})
        self.assertEqual(detect_accounts(self.home,self.sources),[])
        self.write(self.home/'.codex/auth.json',{'tokens':{'id_token':'broken'}})
        self.assertEqual(detect_accounts(self.home,self.sources),[])

    def test_same_account_is_stable_between_machines(self):
        self.codex()
        a = detect_accounts(self.home,self.sources)[0]['id']
        second = self.root/'second'
        self.write(second/'.codex/auth.json',json.loads((self.home/'.codex/auth.json').read_text()))
        sources = {name:str(second/('.'+name)) for name in self.sources}
        self.assertEqual(detect_accounts(second,sources)[0]['id'],a)

    def test_multiple_subscriptions_unknown_amount_and_quantity(self):
        rows = [{'id':'openai-1','provider':'OpenAI','label':'Pessoal','monthlyUsd':500},
                {'id':'openai-2','provider':'OpenAI','label':'Outra','monthlyUsd':100},
                {'id':'claude','provider':'Anthropic','label':'Três contas','monthlyUsd':200,'quantity':3},
                {'id':'grok','provider':'xAI','label':'Grok','monthlyUsd':300},
                {'id':'unknown','provider':'OpenAI','label':'A confirmar','monthlyUsd':None}]
        rows = validate_subscriptions([dict(row,startDate='2000-01-01') for row in rows],[])
        self.assertEqual(monthly_totals(rows),{'OpenAI':600,'Anthropic':600,'xAI':300})
        store = Store(self.profile,self.home)
        store.save_settings(dict(store.settings,subscriptions=rows,billingReviewed=True))
        result = store.overview({'period':'7d'})['totals']
        self.assertEqual(result['allocated'],350)
        self.assertEqual(result['subscriptionsPending'],1)
        self.assertEqual(result['subscriptionsKnown'],6)
        self.codex('different-account')
        store.state()
        self.assertEqual(store.settings['monthly']['OpenAI'],600)

    def test_duplicate_account_negative_amount_and_invalid_quantity_rejected(self):
        self.codex()
        account = detect_accounts(self.home,self.sources)[0]
        row = {'id':'one','provider':'OpenAI','label':'Conta','accountId':account['id'],'monthlyUsd':100,'quantity':1}
        with self.assertRaises(ValueError):validate_subscriptions([row,dict(row,id='two')],[account])
        for change in [{'provider':'Anthropic'},{'monthlyUsd':-1},{'monthlyUsd':float('nan')},{'monthlyUsd':True},{'quantity':2},{'quantity':True},{'label':''}]:
            with self.assertRaises(ValueError):validate_subscriptions([dict(row,**change)],[account])

    def test_legacy_totals_are_preserved_but_not_assigned_to_current_account(self):
        self.write(self.profile/'settings.json',{'monthly':{'OpenAI':600,'Anthropic':600,'xAI':300}})
        self.codex()
        store = Store(self.profile,self.home)
        self.assertEqual(sum(store.settings['monthly'].values()),1500)
        self.assertTrue(store.settings['billingReviewed'])
        self.assertTrue(all(not row['accountId'] for row in store.settings['subscriptions']))
        totals = store.overview({'period':'7d'})['totals']
        self.assertEqual(totals['allocated'],0)
        self.assertEqual(totals['subscriptionsMissingDates'],3)

    def test_unknown_is_distinct_from_explicit_no_subscription(self):
        store = Store(self.profile,self.home)
        self.assertFalse(store.overview({'period':'7d'})['totals']['billingReviewed'])
        store.save_settings(dict(store.settings,subscriptions=[],billingReviewed=True))
        result = store.overview({'period':'7d'})['totals']
        self.assertTrue(result['billingReviewed'])
        self.assertEqual(result['monthly'],0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
