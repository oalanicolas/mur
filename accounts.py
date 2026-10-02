import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from billing import validate_period

PROVIDERS = ('OpenAI', 'Anthropic', 'xAI')


def read_metadata(path):
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def jwt_claims(value):
    if not isinstance(value, str) or len(value) > 64000:
        return {}
    try:
        body = value.split('.')[1]
        result = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        return result if isinstance(result, dict) else {}
    except (ValueError, IndexError, UnicodeError):
        return {}


def identity(provider, identifier, email, plan, source):
    if not isinstance(identifier, str) or not identifier:
        return None
    key = hashlib.sha256((provider + ':' + identifier).encode()).hexdigest()
    label = provider + ' · ' + key[:6]
    if isinstance(email, str) and re.fullmatch(r'[^\s@]{1,100}@[^\s@]{1,150}', email):
        local, domain = email.split('@')
        label = local[:2] + '…' + (local[-1] if len(local) > 3 else '') + '@' + domain
    return {'id':key,'provider':provider,'label':label,'plan':plan,'source':source}


def detect_accounts(home, sources):
    rows = []
    codex = read_metadata(Path(sources['codex']).expanduser() / 'auth.json')
    tokens = codex.get('tokens') or {}
    if isinstance(tokens, dict) and codex.get('auth_mode') != 'apikey':
        claims = jwt_claims(tokens.get('id_token')) or jwt_claims(tokens.get('access_token'))
        auth = claims.get('https://api.openai.com/auth') or {}
        if isinstance(auth, dict):
            plan = auth.get('chatgpt_plan_type', '')
            if not isinstance(plan, str) or not re.fullmatch(r'[a-zA-Z0-9 _-]{1,50}', plan):
                plan = ''
            account = auth.get('chatgpt_account_id') or tokens.get('account_id')
            person = auth.get('chatgpt_user_id') or auth.get('user_id')
            identifier = str(account) + ':' + str(person or '') if account else claims.get('sub')
            rows.append(identity('OpenAI', identifier, claims.get('email'), plan, 'Metadados locais do Codex'))
    claude = Path(sources['claude']).expanduser()
    profile_path = home / '.claude.json' if claude == home / '.claude' else claude / '.claude.json'
    profile = read_metadata(profile_path).get('oauthAccount') or {}
    if isinstance(profile, dict):
        tier = profile.get('organizationRateLimitTier') or profile.get('userRateLimitTier') or ''
        kind = profile.get('organizationType') or ''
        plan = {'default_claude_max_20x':'Max 20×','default_claude_max_5x':'Max 5×'}.get(tier)
        plan = plan or {'claude_max':'Max','claude_pro':'Pro','claude_team':'Team','claude_enterprise':'Enterprise'}.get(kind, '')
        account = profile.get('accountUuid')
        identifier = str(account) + ':' + str(profile.get('organizationUuid') or '') if account else None
        rows.append(identity('Anthropic', identifier, profile.get('emailAddress'), plan, 'Perfil local do Claude Code'))
    grok = read_metadata(Path(sources['grok']).expanduser() / 'auth.json')
    for entry in grok.values():
        if isinstance(entry, dict):
            identifier = entry.get('user_id')
            if isinstance(identifier, str):
                identifier += ':' + str(entry.get('team_id') or '')
            rows.append(identity('xAI', identifier, entry.get('email'), '', 'Metadados locais do Grok'))
    return list({row['id']:row for row in rows if row}.values())


class AccountRegistry:
    def __init__(self, directory):
        self.path = directory / 'account-observations.json'
        self.rows = read_metadata(self.path).get('accounts', {})
        self.lock = threading.Lock()

    def snapshot(self, home, sources, now=None):
        now = now if now is not None else time.time()
        detected = detect_accounts(home, sources)
        present = {row['id'] for row in detected}
        with self.lock:
            changed = False
            for row in detected:
                previous = self.rows.get(row['id'])
                if previous is None or now - previous['lastSeen'] >= 60 or previous['plan'] != row['plan'] or previous['label'] != row['label']:
                    history = list(previous.get('planHistory', [])) if previous else []
                    if not previous or previous['plan'] != row['plan']:
                        history.append({'plan':row['plan'],'observedAt':now})
                    self.rows[row['id']] = dict(row, firstSeen=previous['firstSeen'] if previous else now, lastSeen=now, planHistory=history)
                    changed = True
            if changed:
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'accounts':self.rows}, ensure_ascii=False, indent=2))
                temporary.chmod(0o600)
                os.replace(temporary,self.path)
            return [dict(row,present=row['id'] in present) for row in sorted(self.rows.values(),key=lambda row:(row['id'] not in present,row['provider'],row['label']))]


def validate_subscriptions(rows, accounts):
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError('Cadastre até 100 assinaturas.')
    known = {row['id']:row['provider'] for row in accounts}
    ids, linked, result, payment_ids = set(), {}, [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Assinatura inválida.')
        identifier, provider = row.get('id'), row.get('provider')
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',identifier) or identifier in ids or provider not in PROVIDERS:
            raise ValueError('Identificação de assinatura inválida ou repetida.')
        label = row.get('label', '')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 120:
            raise ValueError('Dê um nome à assinatura, com até 120 caracteres.')
        account = row.get('accountId') or ''
        if not isinstance(account,str) or (account and known.get(account) != provider):
            raise ValueError('Vincule uma conta detectada do mesmo provedor.')
        amount, quantity = row.get('monthlyUsd'), row.get('quantity',1)
        if amount is not None and (isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or not 0 <= amount <= 1000000):
            raise ValueError('Informe um valor mensal válido ou deixe em branco para confirmar depois.')
        if isinstance(quantity,bool) or not isinstance(quantity,int) or not 1 <= quantity <= 100 or (account and quantity != 1):
            raise ValueError('A quantidade deve ser de 1 a 100; uma conta vinculada representa uma assinatura.')
        period = validate_period(row,payment_ids)
        if account:
            for previous in linked.get(account,[]):
                separate = (previous['endDate'] and period['startDate'] and previous['endDate']<=period['startDate']) or (period['endDate'] and previous['startDate'] and period['endDate']<=previous['startDate'])
                if not separate:raise ValueError('Períodos da mesma conta não podem se sobrepor. Encerre o preço anterior na data da mudança.')
            linked.setdefault(account,[]).append(period)
        ids.add(identifier)
        result.append({'id':identifier,'provider':provider,'label':label.strip(),'accountId':account,'monthlyUsd':amount,'quantity':quantity,**period})
    return result
