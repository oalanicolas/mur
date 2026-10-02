"""Reprodução de uma sessão real do Claude Code: lê a transcrição e os subagentes e devolve uma linha do tempo.

Só leitura. Caminhos da máquina saem relativos (workspace, ~, scratchpad) antes de chegar ao navegador.
"""
import bisect
import hashlib
import json
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

HOME = Path.home()
PROJECTS = HOME / '.claude' / 'projects'
GAP_CAP = 90.0  # pausa real maior que isso vira 90 s na reprodução
CACHE_VERSION = 12

GATE = re.compile(r'validate\.py|gls\.py check|cerebro\.py check|unittest|npm (run )?test|pytest|gameops\.py (preflight|gates|audit)|game\.py verify|node --check|verify_ui|rom-extract/validate')
CONTEXT = re.compile(r'^\s*(cd [^;&]+(&&|;)\s*)?(cat|sed -n|head|tail|ls|grep|rg|find|wc|git (log|status|diff|show|ls-files)|jq|python3 -c "import json)\b|game\.py context|cerebro\.py buscar|game\.py sfx search')
DEPLOY = re.compile(r'gameops\.py deploy')
PUSH = re.compile(r'\bgit (-[Cc] \S+ )*push\b')
COMMIT = re.compile(r'\bgit (-[Cc] \S+ )*commit\b')
MODULE = re.compile(r'(?:libraries|games|prototypes|demos|apps)/[a-z0-9][a-z0-9._-]*')  # squads/ é ferramenta do hub
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2(?=\s|$)", re.S)
FAILURE = re.compile(r'\b(FAIL(ED)?|Traceback|ERRO[R]?:|AssertionError|[1-9]\d* (failed|falhas?))\b|^# fail [1-9]|^not ok \d|\bexit(?: code)?[=: ]\s*[1-9]', re.M)
NOISE = ('<local-command', '<command-name>/model', '<command-name>/reload', '<command-name>/clear', 'Caveat:', '<system-reminder>')


def stamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        return None


def tidy(text, limit=180):
    """Normaliza caminhos de máquina e encurta."""
    text = str(text or '')
    text = re.sub(r'/(?:private/)?tmp/claude-\d+/[^/\s]+/[0-9a-f-]{36}/scratchpad', 'scratchpad', text)
    text = re.sub(r'/(?:private/)?tmp/claude-\d+/[^\s"\']*', 'tmp', text)
    text = re.sub(r'/(?:private/)?var/folders/[^\s"\']*', 'tmp', text)
    text = re.sub(r'/Users/[^/\s]+/Code/Games/?', '', text)
    text = re.sub(r'/Users/[^/\s]+', '~', text)
    text = re.sub(r'\*\*|__|`|^#+\s*|(?<=\s)#+\s', '', text)
    text = re.sub(r'(^|\s)[-*]\s+', r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def model_name(raw):
    raw = str(raw or '')
    m = re.search(r'(opus|sonnet|haiku|fable)[-_ ]?(\d+)(?:[-.](\d+))?', raw)
    if not m:
        return raw.capitalize() if raw in ('opus', 'sonnet', 'haiku', 'fable') else raw
    return m.group(1).capitalize() + ' ' + m.group(2) + ('.' + m.group(3) if m.group(3) and len(m.group(3)) < 3 else '')


def transcript_path(external_id, projects=None):
    for path in Path(projects or PROJECTS).glob('*/' + external_id + '.jsonl'):
        return path
    return None


def texts(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
    return ''


def result_text(block):
    content = block.get('content')
    return content if isinstance(content, str) else texts(content)


def unheredoc(command):
    """O comando sem o corpo dos heredocs (texto que o shell não executa como comando)."""
    return HEREDOC.sub(lambda m: '<<heredoc' + m.group(3), command)


def executable(command):
    """O que o shell executa: sem corpo de heredoc e sem texto entre aspas."""
    return re.sub(r'"(?:\\.|[^"\\])*"|\'[^\']*\'', '""', unheredoc(command))


def cd_targets(command):
    return re.findall(r'(?:^|[;&|(\n]\s*)cd\s+("[^"]+"|\S+)', unheredoc(command))


def module_of(command):
    m = MODULE.search(executable(command))
    if m:
        return m.group(0)
    for target in cd_targets(command):
        m = MODULE.search(target)
        if m:
            return m.group(0)
    return 'hub'


def kinds(command):
    """Tudo o que um comando executa que conta como prova ou entrega, do mais pesado ao mais leve."""
    run = executable(command)
    found = [k for k, rule in (('deploy', DEPLOY), ('push', PUSH), ('commit', COMMIT)) if rule.search(run)]
    for part in re.split(r'&&|\|\||;|\||\n', run):
        part = part.strip()
        if GATE.search(part) and '--help' not in part and not re.match(r'(grep|sed|cat|head|tail|ls|wc|rg|git|find|echo|cp|mv|ps|pgrep|pkill|kill)\b', part):
            found.append('gate')
            break
    return found


def classify_bash(command):
    found = kinds(command)
    if found:
        return found[0]
    if CONTEXT.search(command):
        return 'context'
    return 'tool'


def module_here(command, cwd='', where='hub'):
    """Módulo de um comando: o que ele cita, senão o destino de um cd, senão o diretório da linha."""
    here = module_of(command)
    if here != 'hub':
        return here
    for target in cd_targets(command):
        m = MODULE.search(target)
        if m:
            return m.group(0)
    if cwd and MODULE.search(cwd):
        return module_of(cwd)
    return where


def read_target(command):
    """Arquivo ou pasta que um comando de leitura abre (cat, sed -n, head, ls...), ou ''."""
    first = re.split(r'&&|\|\||;|\||\n', executable(command))
    for part in first:
        words = part.split()
        if words and words[0] == 'cd':
            continue
        paths = [w for w in words[1:] if not w.startswith('-') and ('/' in w or re.search(r'\.\w{1,5}$', w)) and not w.startswith(('<', '>'))]
        return tidy(paths[-1], 90) if paths else ''
    return ''


def gate_label(command):
    for pattern, name in ((r'validate\.py', 'validate.py'), (r'gls\.py check', 'gls.py check'), (r'cerebro\.py check', 'cerebro check'),
                          (r'unittest', 'unittest'), (r'npm (run )?test', 'npm test'), (r'pytest', 'pytest'),
                          (r'gameops\.py preflight', 'preflight'), (r'gameops\.py gates', 'gates --run'), (r'gameops\.py audit', 'gameops audit'),
                          (r'game\.py verify', 'game.py verify'), (r'node --check', 'node --check'), (r'verify_ui', 'verify_ui')):
        if re.search(pattern, command):
            return name
    return 'gate'


def commit_message(command):
    m = re.search(r'-m\s+(["\'])(.+?)\1', command, re.S) or re.search(r'-m\s+"\$\(cat <<[\'"]?EOF[\'"]?\s*\n(.+?)\n', command, re.S)
    return tidy(m.group(2).split('\n')[0], 90) if m else ''


def failed(block, text):
    if block.get('is_error'):
        return True
    return bool(FAILURE.search(text[-1500:]))


TOOL_LABELS = {
    'ListAgents': 'Conferiu os agentes em andamento',
    'TaskStop': 'Parou um agente',
    'WebFetch': 'Leu uma página da web',
    'WebSearch': 'Pesquisou na web',
    'Monitor': 'Acompanhou um processo em segundo plano',
}


def tool_label(name, data):
    if name == 'SendMessage':
        to = str(data.get('to') or '')
        if to.startswith('uds:') or '/' in to:
            return 'Mensagem para outra sessão local'
        to = tidy(to, 40)
        return f'Mensagem para o agente {to}' if to else 'Mensagem para um agente'
    return TOOL_LABELS.get(name, name)


TOKEN_KEYS = ('input', 'cache', 'write', 'output')


def price(rates, model, u):
    """Mesma conta do índice (core.Store.cost) para um modelo da Anthropic."""
    r = (rates or {}).get(model)
    if not r:
        return 0.0
    return (u['input'] * r['input'] + u['cache'] * r['cache'] + (u['write'] - u['write1h']) * (r.get('write5m') or 0)
            + u['write1h'] * (r.get('write1h') or 0) + u['output'] * r['output']) / 1e6


class Meter:
    """Tokens e custo por modelo, deduplicados por message.id (vale a última linha de cada mensagem)."""

    def __init__(self, rates):
        self.rates = rates
        self.msgs = {}
        self.efforts = Counter()

    def add(self, d, ts):
        m = d.get('message') or {}
        if d.get('type') != 'assistant' or not isinstance(m.get('usage'), dict) or not m.get('id') or m.get('model') in (None, '<synthetic>'):
            return
        usage = m['usage']
        u = {'input': usage.get('input_tokens') or 0, 'cache': usage.get('cache_read_input_tokens') or 0,
             'write': usage.get('cache_creation_input_tokens') or 0,
             'write1h': (usage.get('cache_creation') or {}).get('ephemeral_1h_input_tokens') or 0, 'output': usage.get('output_tokens') or 0}
        first = self.msgs.get(m['id'], (ts,))[0]
        self.msgs[m['id']] = (first or ts, m['model'], u)
        if d.get('effort'):
            self.efforts[d['effort']] += 1

    def models(self):
        out = {}
        for _, raw, u in self.msgs.values():
            slot = out.setdefault(model_name(raw), {'raw': raw, 'calls': 0, 'usd': 0.0, **{k: 0 for k in TOKEN_KEYS}, 'usd_cache': 0.0})
            for k in TOKEN_KEYS:
                slot[k] += u[k]
            slot['calls'] += 1
            slot['usd'] += price(self.rates, raw, u)
            slot['usd_cache'] += u['cache'] * ((self.rates or {}).get(raw) or {}).get('cache', 0) / 1e6
        return out

    def samples(self):
        return [(ts, price(self.rates, raw, u)) for ts, raw, u in self.msgs.values() if ts]

    def effort(self):
        return self.efforts.most_common(1)[0][0] if self.efforts else ''


def summed(models):
    total = {k: sum(m[k] for m in models.values()) for k in TOKEN_KEYS}
    total['usd'] = round(sum(m['usd'] for m in models.values()), 4)
    return total


def notification(text):
    """(agentId, estado, motivo) de um <task-notification>."""
    agent_id = (re.search(r'<task-id>(\w+)</task-id>', text) or [None, None])[1]
    summary = (re.search(r'<summary>(.*?)</summary>', text, re.S) or [None, ''])[1]
    status = (re.search(r'<status>(\w+)</status>', text) or [None, 'completed'])[1]
    state = {'completed': 'ok', 'failed': 'bad'}.get(status, 'stop')
    reason = ''
    if state == 'ok' and re.search(r'turn limit|partial result', summary, re.I):
        return agent_id, 'stop', 'limite de turnos (resultado parcial)', summary
    if state != 'ok':
        if re.search(r'weekly limit', summary, re.I):
            reason = 'limite semanal'
        elif re.search(r'session limit', summary, re.I):
            reason = 'limite da sessão'
        elif re.search(r'API error', summary):
            reason = 'erro da API'
        else:
            reason = tidy(re.sub(r'^.*?(failed|stopped|killed):\s*', '', summary), 90)
    return agent_id, state, reason, summary


def read_subagent(folder, agent_id, rates=None):
    path = folder / ('agent-' + agent_id + '.jsonl')
    if not path.exists():
        return {}
    first = last = None
    tools = {'context': 0, 'edit': 0, 'gate': 0, 'tool': 0}
    meter = Meter(rates)
    children = {}
    found, pending = [], {}
    with path.open(encoding='utf-8', errors='replace') as handle:
        for line in handle:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = stamp(d.get('timestamp'))
            if ts:
                first = first or ts
                last = ts
            meter.add(d, ts)
            message = d.get('message') or {}
            content = message.get('content')
            if d.get('type') == 'user':
                text = content if isinstance(content, str) else texts(content)
                if '<task-notification>' in (text or ''):
                    child, state, reason, _ = notification(text)
                    if child:
                        children[child] = (state, reason, ts)
                info = d.get('toolUseResult') if isinstance(d.get('toolUseResult'), dict) else {}
                if info.get('agentId') and not info.get('isAsync'):
                    children.setdefault(info['agentId'], ('ok', '', ts))
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('tool_use_id') in pending:
                            ok = not failed(b, result_text(b))
                            for ev in pending.pop(b['tool_use_id']):
                                ev['ok'] = ok
                continue
            if not isinstance(content, list):
                continue
            for b in content:
                if b.get('type') != 'tool_use':
                    continue
                name, data = b.get('name'), b.get('input') or {}
                if name in ('Read', 'Grep', 'Glob'):
                    tools['context'] += 1
                elif name in ('Edit', 'Write', 'NotebookEdit'):
                    tools['edit'] += 1
                elif name == 'Bash':
                    command = data.get('command', '')
                    kind = classify_bash(command)
                    tools['gate' if kind == 'gate' else 'context' if kind == 'context' else 'tool'] += 1
                    group = []
                    for k in kinds(command) if ts else []:
                        ev = {'kind': k, 'module': module_here(command, d.get('cwd') or ''), 'by': agent_id,
                              'label': gate_label(command) if k == 'gate' else commit_message(command) or tidy(data.get('description') or command, 110)}
                        group.append(ev)
                        found.append((ts, ev))
                    if group:
                        pending[b.get('id')] = group
                else:
                    tools['tool'] += 1
    meta_path = folder / ('agent-' + agent_id + '.meta.json')
    try:
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    except ValueError:
        meta = {}
    return {'first': first, 'last': last, 'meter': meter, 'tools': tools, 'effort': meter.effort(),
            'children': children, 'meta': meta, 'events': found}


def parse(path, rates=None):
    path = Path(path)
    folder = path.with_suffix('') / 'subagents'
    raw = []          # (ts, evento)
    pending = {}      # tool_use id -> evento à espera do resultado
    agents = {}       # agentId -> evento
    by_description = {}
    request = ''
    title = ''
    model = ''
    where = 'hub'
    meter = Meter(rates)
    with path.open(encoding='utf-8', errors='replace') as handle:
        for line in handle:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get('isSidechain'):
                continue
            ts = stamp(d.get('timestamp'))
            if ts is None:
                continue
            meter.add(d, ts)
            message = d.get('message') or {}
            content = message.get('content')
            if d.get('type') == 'assistant':
                if message.get('model') and message['model'] != '<synthetic>':
                    model = model or message['model']
                if not isinstance(content, list):
                    continue
                for b in content:
                    if b.get('type') == 'text' and len(b.get('text', '')) > 400:
                        raw.append((ts, {'kind': 'say', 'label': tidy(b['text'], 220)}))
                    if b.get('type') != 'tool_use':
                        continue
                    name, data = b.get('name'), b.get('input') or {}
                    if name in ('Agent', 'Task'):
                        ev = {'kind': 'agent', 'type': data.get('subagent_type') or 'general-purpose', 'label': tidy(data.get('description'), 70),
                              'model': model_name(data.get('model')), 'state': 'run', 'depth': 1, 'parent': ''}
                        pending[b['id']] = ev
                        by_description.setdefault(data.get('description') or '', []).append(ev)
                        raw.append((ts, ev))
                    elif name == 'Skill':
                        raw.append((ts, {'kind': 'skill', 'label': data.get('skill', ''), 'detail': tidy(data.get('args'), 120)}))
                    elif name == 'AskUserQuestion':
                        qs = data.get('questions') or []
                        raw.append((ts, {'kind': 'question', 'label': tidy(qs[0].get('question') if qs else '', 140), 'count': len(qs)}))
                    elif name in ('Read', 'Grep', 'Glob'):
                        raw.append((ts, {'kind': 'context', 'label': tidy(data.get('file_path') or data.get('pattern') or data.get('path'), 90), 'path': True}))
                    elif name in ('Edit', 'Write', 'NotebookEdit'):
                        raw.append((ts, {'kind': 'edit', 'label': tidy(data.get('file_path'), 90), 'path': True}))
                    elif name == 'Bash':
                        command = data.get('command', '')
                        found = kinds(command)
                        moves = cd_targets(command)
                        here = module_of(command)
                        if here == 'hub' and not moves:
                            here = module_here(command, d.get('cwd') or '', where)
                        for target in moves:
                            if target.startswith(('/', '~', '"/')) or MODULE.search(target):
                                m = MODULE.search(target)
                                where = m.group(0) if m else 'hub'
                            elif target in ('..', '../..'):
                                where = 'hub'
                            elif re.fullmatch(r'[a-z0-9][a-z0-9._-]*', target) and where == 'hub':
                                pass
                        label = tidy(data.get('description') or command, 110)
                        if not found:
                            kind = classify_bash(command)
                            target = read_target(command) if kind == 'context' else ''
                            raw.append((ts, {'kind': kind, 'label': target or label, 'path': bool(target)}))
                            continue
                        group = [{'kind': k, 'module': here, 'label': gate_label(command) if k == 'gate' else commit_message(command) or label} for k in found]
                        raw.extend((ts, ev) for ev in group)
                        pending[b['id']] = group
                    elif name not in ('ToolSearch', 'TodoWrite', 'TaskCreate', 'TaskUpdate', 'TaskList'):
                        raw.append((ts, {'kind': 'tool', 'label': tool_label(name, b.get('input') or {})}))
                continue
            if d.get('type') != 'user':
                continue
            if isinstance(content, list):
                for b in content:
                    if b.get('type') != 'tool_result' or b.get('tool_use_id') not in pending:
                        continue
                    ev = pending.pop(b['tool_use_id'])
                    text = result_text(b)
                    if isinstance(ev, list):
                        for item in ev:
                            item['ok'] = not failed(b, text)
                        continue
                    if ev['kind'] == 'agent':
                        info = d.get('toolUseResult') if isinstance(d.get('toolUseResult'), dict) else {}
                        agent_id = info.get('agentId') or (re.search(r'agentId: (\w+)', text) or [None, None])[1]
                        ev['model'] = model_name(info.get('resolvedModel')) or ev['model']
                        if agent_id:
                            agents[agent_id] = ev
                            ev['id'] = agent_id
                        if not info.get('isAsync') and 'Async agent launched' not in text:
                            ev['end_ts'] = ts
                            ev['state'] = 'ok'
                text = ''
            else:
                text = content if isinstance(content, str) else ''
            if not text or d.get('isMeta') or d.get('isCompactSummary'):
                if d.get('isCompactSummary'):
                    raw.append((ts, {'kind': 'compact', 'label': 'contexto compactado; o handoff volta'}))
                continue
            if text.startswith('This session is being continued'):
                raw.append((ts, {'kind': 'compact', 'label': 'contexto compactado; o handoff volta'}))
                continue
            if '<task-notification>' in text:
                agent_id, state, reason, summary = notification(text)
                ev = agents.get(agent_id)
                if not ev:
                    m = re.search(r'Agent "(.+?)"', summary)
                    cands = by_description.get(m.group(1), []) if m else []
                    ev = next((c for c in cands if 'end_ts' not in c), None)
                if ev:
                    # um agente retomado pode notificar mais de uma vez: vale a última
                    ev['end_ts'] = max(ev.get('end_ts') or 0, ts)
                    ev['state'] = state
                    ev['reason'] = reason
                continue
            if text.startswith('Base directory for this skill') or any(text.lstrip().startswith(n) for n in NOISE):
                continue
            command = re.search(r'<command-name>/([\w:-]+)</command-name>', text)
            if command:
                args = (re.search(r'<command-args>(.*?)</command-args>', text, re.S) or [None, ''])[1]
                label = clean_ask(args, 400)
                raw.append((ts, {'kind': 'request', 'label': label or '/' + command.group(1), 'skill': command.group(1)}))
                request = request or label
                title = title or '/' + command.group(1)
                continue
            clean = clean_ask(text, 400)
            raw.append((ts, {'kind': 'request', 'label': clean}))
            request = request or clean
    # subagentes: cada arquivo em subagents/ é um agente, inclusive os abertos por outros agentes
    infos = {}
    if folder.exists():
        for f in sorted(folder.glob('agent-*.jsonl')):
            agent_id = f.stem[len('agent-'):]
            infos[agent_id] = read_subagent(folder, agent_id, rates)
    seen = set(meter.msgs)
    for info in sorted(infos.values(), key=lambda i: i.get('first') or 0):
        m = info['meter']
        for mid in [k for k in m.msgs if k in seen]:
            del m.msgs[mid]
        seen.update(m.msgs)
        info['models'] = m.models()
        info['samples'] = m.samples()
        info['model'] = max(info['models'], key=lambda k: info['models'][k]['calls']) if info['models'] else ''
    agent_models = []
    child_states = {}
    for info in infos.values():
        child_states.update(info.get('children') or {})
    for agent_id, info in infos.items():
        if not info.get('first'):
            continue
        meta = info.get('meta') or {}
        ev = agents.get(agent_id)
        if ev is None:
            depth = meta.get('spawnDepth') or 2
            ev = {'kind': 'agent', 'id': agent_id, 'type': meta.get('agentType') or 'general-purpose',
                  'label': tidy(meta.get('description') or meta.get('agentType') or agent_id, 70), 'model': '', 'state': 'run',
                  'depth': depth, 'parent': meta.get('parentAgentId') or ''}
            state = child_states.get(agent_id)
            if state:
                ev['state'], ev['reason'] = state[0], state[1]
            agents[agent_id] = ev
            raw.append((info['first'], ev))
        raw.extend(info.get('events') or [])
        agent_models.append(info['models'])
        if meta.get('agentType') == 'fork' or meta.get('isFork'):
            ev['fork'] = True
        ev['model'] = info['model'] or ev['model']
        ev['tools'] = info['tools']
        ev['effort'] = info['effort']
        ev['usage'] = summed(info['models'])
        ev['by_model'] = {k: round(v['usd'], 4) for k, v in info['models'].items()}
        if info.get('last') and ('end_ts' not in ev or ev['end_ts'] < info['last']):
            ev['end_ts'] = max(ev.get('end_ts') or 0, info['last'])
    raw.sort(key=lambda r: r[0])
    if not raw:
        return {'events': [], 'stats': {}}
    # relógio comprimido: pausas longas viram GAP_CAP
    marks = sorted({r[0] for r in raw} | {r[1]['end_ts'] for r in raw if r[1].get('end_ts')})
    clock, prev, acc = {}, marks[0], 0.0
    for t in marks:
        acc += min(t - prev, GAP_CAP)
        clock[t] = acc
        prev = t

    def squeeze(ts):
        i = bisect.bisect_right(marks, ts) - 1
        if i < 0:
            return 0.0
        value = clock[marks[i]] + min(ts - marks[i], GAP_CAP)
        return min(value, clock[marks[i + 1]]) if i + 1 < len(marks) else min(value, acc)

    events = []
    for ts, ev in raw:
        ev = dict(ev)
        ev['t'] = round(clock[ts], 1)
        ev['real'] = round(ts)
        if ev.get('end_ts'):
            ev['end'] = round(clock[ev['end_ts']], 1)
            ev['real_end'] = round(ev.pop('end_ts'))
        elif ev['kind'] == 'agent':
            ev['end'] = round(acc, 1)
            ev['real_end'] = round(marks[-1])
        if ev['kind'] == 'agent' and ev.get('state') == 'run':
            ev['state'] = 'ok'
        events.append(ev)
    agents_list = [e for e in events if e['kind'] == 'agent']
    gates = [e for e in events if e['kind'] == 'gate']
    main_models = meter.models()
    # curva de custo acumulado: [t, total, sessão principal]
    samples = [(ts, usd, True) for ts, usd in meter.samples()]
    for info in infos.values():
        samples += [(ts, usd, False) for ts, usd in info.get('samples') or []]
    samples.sort()
    curve, total, main_total, step = [], 0.0, 0.0, max(acc / 360, 1)
    for ts, usd, is_main in samples:
        total += usd
        main_total += usd if is_main else 0
        t = round(squeeze(ts), 1)
        if curve and t - curve[-1][0] < step:
            curve[-1] = [curve[-1][0], round(total, 4), round(main_total, 4)]
        else:
            curve.append([t, round(total, 4), round(main_total, 4)])
    models = {}
    for name, m in main_models.items():
        slot = models.setdefault(name, {'name': name, 'agents': 0, 'failed': 0, 'usd': 0.0, 'usd_main': 0.0, 'tokens': 0})
        slot['usd_main'] += m['usd']
        slot['usd'] += m['usd']
        slot['tokens'] += sum(m[k] for k in TOKEN_KEYS)
    def slot_of(name):
        return models.setdefault(name, {'name': name, 'agents': 0, 'failed': 0, 'usd': 0.0, 'usd_main': 0.0, 'tokens': 0})

    for e in agents_list:
        slot = slot_of(e['model'] or 'sem modelo')
        slot['agents'] += 1
        slot['failed'] += e['state'] == 'bad'
    for per in agent_models:
        for name, m in per.items():
            slot = slot_of(name)
            slot['usd'] += m['usd']
            slot['tokens'] += sum(m[k] for k in TOKEN_KEYS)
    for slot in models.values():
        slot['usd'] = round(slot['usd'], 2)
        slot['usd_main'] = round(slot['usd_main'], 2)
    main_usage = summed(main_models)
    stats = {
        'agents': len(agents_list), 'gates': len(gates), 'gates_failed': sum(1 for g in gates if g.get('ok') is False),
        'commits': sum(1 for e in events if e['kind'] == 'commit' and e.get('ok') is not False),
        'pushes': sum(1 for e in events if e['kind'] == 'push' and e.get('ok') is not False),
        'deploys': sum(1 for e in events if e['kind'] == 'deploy' and e.get('ok') is not False),
        'context': sum(1 for e in events if e['kind'] == 'context') + sum((e.get('tools') or {}).get('context', 0) for e in agents_list),
        'edits': sum(1 for e in events if e['kind'] == 'edit') + sum((e.get('tools') or {}).get('edit', 0) for e in agents_list),
        'questions': sum(1 for e in events if e['kind'] == 'question'),
        'compactions': sum(1 for e in events if e['kind'] == 'compact'),
        'modules': sorted({e['module'] for e in events if e.get('module') and e['module'] != 'hub' and e['kind'] in ('commit', 'gate')}),
        'agents_failed': sum(1 for e in agents_list if e['state'] == 'bad'),
        'nested': sum(1 for e in agents_list if e.get('depth', 1) > 1),
        'depth': max([e.get('depth', 1) for e in agents_list] or [0]),
        'forks': sum(1 for e in agents_list if e.get('fork')),
        'usd': round(main_usage['usd'] + sum((e.get('usage') or {}).get('usd', 0) for e in agents_list), 2),
        'usd_main': round(main_usage['usd'], 2),
        'tokens': sum(main_usage[k] for k in TOKEN_KEYS) + sum((e.get('usage') or {}).get(k, 0) for e in agents_list for k in TOKEN_KEYS),
        'usd_cache': round(sum(m['usd_cache'] for per in [main_models] + agent_models for m in per.values()), 2),
    }
    data = {'title': title, 'request': request, 'model': model_name(model), 'effort': meter.effort(), 'started': round(raw[0][0]), 'ended': round(max(marks)),
            'duration': round(acc, 1), 'events': events, 'stats': stats, 'curve': curve,
            'models': sorted(models.values(), key=lambda m: -m['usd']), 'main_usage': main_usage}
    data['insights'] = insights(data, rates, marks, agent_models)
    return data


def hours(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f'{seconds} s'
    h, m = divmod(int(round(seconds / 60)), 60)
    return f'{h} h {m:02d} min' if h else f'{m} min'


def plural(n, one, many):
    return f'{n} {one if n == 1 else many}'


def money(usd):
    return 'US$ ' + f'{usd:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def pct(part, whole):
    return round(100 * part / whole) if whole else 0


def insights(data, rates, marks, agent_models=()):
    """Leituras calculadas sobre a sessão inteira. 'estimate' marca o que é conta hipotética."""
    out = []
    events, stats = data['events'], data['stats']
    agents = [e for e in events if e['kind'] == 'agent']
    total = stats['usd'] or 0
    # 1. para onde foi o dinheiro
    if total:
        parts = [('Sessão principal', stats['usd_main'])]
        by_model = Counter()
        for per in agent_models:
            for name, m in per.items():
                by_model[name] += m['usd']
        parts += [(f'Subagentes {k}', v) for k, v in by_model.most_common()]
        top = max(parts, key=lambda p: p[1])
        who = 'A sessão principal gastou' if top[0] == 'Sessão principal' else f'Os subagentes {top[0][len("Subagentes "):]} gastaram'
        out.append({'key': 'money', 'kicker': 'Para onde foi o custo', 'value': f'{pct(top[1], total)}%',
                    'text': f'{who} {money(top[1])} dos {money(total)} da sessão.',
                    'bars': [{'label': n, 'usd': round(v, 2)} for n, v in parts if v]})
    # 2. contexto em cache
    tokens = stats.get('tokens') or 0
    cache = (data['main_usage'].get('cache') or 0) + sum((e.get('usage') or {}).get('cache', 0) for e in agents)
    if tokens and cache:
        cache_usd = stats.get('usd_cache') or 0
        out.append({'key': 'cache', 'kicker': 'Contexto relido', 'value': f'{pct(cache, tokens)}%',
                    'text': f'{pct(cache, tokens)}% dos tokens foram leitura de contexto em cache ({cache / 1e9:.2f} bi). '.replace('.', ',', 1)
                            + f'Mesmo barata, essa releitura pesa ~{pct(cache_usd, total)}% do custo: cada passo de um agente relê tudo o que ele já carregou.'})
    # 3. falhas de agentes
    bad = [e for e in agents if e['state'] == 'bad']
    if bad:
        reasons = Counter(e.get('reason') or 'sem motivo' for e in bad)
        models = Counter(e['model'] for e in bad)
        lost = sum((e.get('usage') or {}).get('usd', 0) for e in bad)
        first = min(bad, key=lambda e: e['real_end'])
        out.append({'key': 'failed', 'kicker': 'Agentes que não terminaram', 'value': str(len(bad)), 'tone': 'bad', 't': first['end'],
                    'text': f'{len(bad)} de {len(agents)} agentes ' + ('parou' if len(bad) == 1 else 'pararam') + ' por ' + ', '.join(f'{r} ({n})' for r, n in reasons.most_common())
                            + ('. Em ' if len(bad) == 1 else '. Todos em ') + ' e '.join(f'{m} ({n})' for m, n in models.most_common()) + f'. Já tinham gastado {money(lost)} antes de parar.'})
    # 4. gates vermelho → verde
    gates = [e for e in events if e['kind'] == 'gate']
    red = [g for g in gates if g.get('ok') is False]
    if red:
        waits, open_ = [], 0
        for g in red:
            nxt = next((h for h in gates if h['real'] > g['real'] and h.get('module') == g.get('module') and h['label'] == g['label'] and h.get('ok')), None)
            if nxt:
                waits.append(nxt['real'] - g['real'])
            else:
                open_ += 1
        text = f'{len(red)} de {plural(len(gates), "execução", "execuções")} de gate ' + ('deu vermelho.' if len(red) == 1 else 'deram vermelho.')
        if waits:
            text += (' Voltou a verde' if len(waits) == 1 else f' {len(waits)} voltaram a verde') + f'; o conserto levou {hours(statistics.median(waits))}' + (f' na mediana (o maior, {hours(max(waits))}).' if len(waits) > 1 else '.')
        if open_:
            text += f' {open_} ' + ('ficou sem um verde' if open_ == 1 else 'ficaram sem um verde') + ' depois na mesma sessão.'
        out.append({'key': 'gates', 'kicker': 'Gate vermelho até o verde', 'value': hours(statistics.median(waits)) if waits else '—', 'text': text, 't': red[0]['t']})
    # 5. paralelismo
    if agents:
        edges = sorted([(e['real'], 1, e) for e in agents] + [(e['real_end'], -1, e) for e in agents], key=lambda x: (x[0], x[1]))
        now = peak = 0
        peak_at = None
        for ts, delta, e in edges:
            now += delta
            if now > peak:
                peak, peak_at = now, e
        out.append({'key': 'parallel', 'kicker': 'Pico de agentes ao mesmo tempo', 'value': str(peak), 't': peak_at['t'] if peak_at else 0,
                    'text': ('No pico, 1 agente rodava sozinho.' if peak == 1 else f'No pico, {peak} agentes rodavam juntos.')
                            + (f' {stats["nested"]} dos {len(agents)} ' + ('foi aberto' if stats['nested'] == 1 else 'foram abertos') + ' por outros agentes' if stats['nested'] else ' Nenhum foi aberto por outro agente')
                            + (f' (até {stats["depth"]} níveis)' if stats['depth'] > 1 else '')
                            + ((', e 1 era fork, que herda o contexto da sessão' if stats['forks'] == 1 else f', e {stats["forks"]} eram forks que herdam o contexto da sessão') if stats['forks'] else '') + '.'})
    # 6. pausas
    gaps = sorted(((b - a, b, a) for a, b in zip(marks, marks[1:]) if b - a > 15 * 60), reverse=True)
    real = data['ended'] - data['started']
    if gaps and real:
        paused = sum(g[0] for g in gaps)
        longest, at, began = gaps[0]
        after = next((e for e in events if e['real'] >= at - 1), None)
        before = [e for e in agents if 'limite' in (e.get('reason') or '') and began - 1800 <= e.get('real_end', 0) <= began + 60]
        after_limit = [e for e in agents if 'limite' in (e.get('reason') or '') and at - 60 <= e.get('real_end', 0) <= at + 1800]
        reason = lambda group: Counter(e['reason'] for e in group).most_common(1)[0][0]
        if before:
            text = 'começou logo depois que ' + ('1 agente bateu' if len(before) == 1 else f'{len(before)} agentes bateram') + f' no {reason(before)}: foi espera até o uso liberar'
        elif after_limit:
            text = 'terminou com ' + ('1 agente retomado batendo' if len(after_limit) == 1 else f'{len(after_limit)} agentes retomados batendo') + f' no {reason(after_limit)}'
        elif after and after['kind'] == 'request':
            text = 'terminou com uma mensagem sua'
        elif after and after['kind'] == 'agent':
            text = 'terminou quando um agente devolveu'
        else:
            text = 'terminou com a sessão retomando o trabalho'
        out.append({'key': 'pauses', 'kicker': 'Tempo parado', 'value': f'{pct(paused, real)}%',
                    'text': f'A sessão ficou aberta {hours(real)}, e {hours(paused)} foram pausas de mais de 15 min. A maior, de {hours(longest)}, {text}.',
                    't': after['t'] if after else None})
    # 7. estimativa: os mesmos agentes Opus com preço Sonnet
    sonnet = next((k for k in ('claude-sonnet-5-5', 'claude-sonnet-5') if k in (rates or {})), None)
    opus = [m for per in agent_models for name, m in per.items() if name.startswith('Opus')]
    opus_agents = [per for per in agent_models if any(name.startswith('Opus') for name in per)]
    if sonnet and opus:
        now_usd = sum(m['usd'] for m in opus)
        u = {k: sum(m[k] for m in opus) for k in TOKEN_KEYS}
        u['write1h'] = 0
        alt = price(rates, sonnet, u)
        if now_usd > alt:
            out.append({'key': 'estimate', 'kicker': 'Se os agentes Opus fossem Sonnet', 'value': '−' + money(now_usd - alt), 'estimate': True,
                        'text': f'As chamadas Opus de {plural(len(opus_agents), "agente", "agentes")} custaram {money(now_usd)}. Com os mesmos tokens no preço do {model_name(sonnet)}, seriam ~{money(alt)} '
                                f'({pct(now_usd - alt, now_usd)}% a menos). É uma conta, não uma medida: o Sonnet pode precisar de mais passos, e a leitura de cache custa igual nos dois.'})
    forks = [e for e in agents if e.get('fork')]
    plain = [e for e in agents if not e.get('fork')]
    if forks and plain:
        avg_fork = sum((e.get('usage') or {}).get('usd', 0) for e in forks) / len(forks)
        avg_plain = sum((e.get('usage') or {}).get('usd', 0) for e in plain) / len(plain)
        out.append({'key': 'forks', 'kicker': 'Forks contra agentes com brief', 'value': f'{avg_fork / avg_plain:.1f}×'.replace('.', ',') if avg_plain else '—',
                    'text': f'Cada fork custou {money(avg_fork)} em média, contra {money(avg_plain)} de um agente aberto com brief próprio. O fork começa com todo o contexto da sessão.'})
    return out


def summary(data):
    """Linha do comparativo entre sessões."""
    stats = data.get('stats') or {}
    agents = [e for e in data.get('events', []) if e['kind'] == 'agent']
    return {'id': data.get('id'), 'started': data.get('started'), 'ended': data.get('ended'), 'request': data.get('request'),
            'model': data.get('model'), 'effort': data.get('effort'), 'duration': data.get('duration'),
            'usd': stats.get('usd'), 'usd_main': stats.get('usd_main'), 'tokens': stats.get('tokens'),
            'agents': Counter(e['model'] or 'sem modelo' for e in agents), 'agents_failed': stats.get('agents_failed', 0),
            'nested': stats.get('nested', 0), 'gates': stats.get('gates', 0), 'gates_failed': stats.get('gates_failed', 0),
            'commits': stats.get('commits', 0), 'pushes': stats.get('pushes', 0), 'compactions': stats.get('compactions', 0)}


def replay(external_id, cache_dir, rates=None, projects=None):
    if not re.fullmatch(r'[0-9a-f-]{36}', external_id or ''):
        raise ValueError('sessão inválida')
    path = transcript_path(external_id, projects)
    if not path:
        raise FileNotFoundError('transcrição não encontrada')
    folder = path.with_suffix('') / 'subagents'
    mtime = max([path.stat().st_mtime] + [p.stat().st_mtime for p in folder.glob('*.jsonl')] if folder.exists() else [path.stat().st_mtime])
    pricing = hashlib.sha256(json.dumps(rates, sort_keys=True).encode()).hexdigest()[:12]
    cache = Path(cache_dir) / f'{external_id}-{int(mtime)}-{pricing}-v{CACHE_VERSION}.json'
    if cache.exists():
        return json.loads(cache.read_text())
    data = parse(path, rates)
    data['id'] = external_id
    cache.parent.mkdir(parents=True, exist_ok=True)
    for old in cache.parent.glob(external_id + '-*.json'):
        old.unlink()
    cache.write_text(json.dumps(data, ensure_ascii=False))
    return data


def clean_ask(text, limit=120):
    """Pedido legível: texto colado entra sem as tags, sem o aviso de interrupção."""
    text = re.sub(r'</?pasted_content[^>]*>', ' ', str(text or ''))
    text = re.sub(r'^\s*\[Request interrupted[^\]]*\]\s*', '', text)
    return tidy(text, limit)


def first_ask(path, lines=600):
    """Primeiro pedido na transcrição principal."""
    with path.open(encoding='utf-8', errors='replace') as handle:
        for i, line in enumerate(handle):
            if i > lines:
                break
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get('type') != 'user' or item.get('isSidechain'):
                continue
            content = item.get('message', {}).get('content', '')
            if not isinstance(content, str):
                content = ' '.join(x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') == 'text')
            m = re.search(r'<command-args>(.*?)</command-args>', content, re.S)
            if m and m.group(1).strip():
                return tidy(m.group(1), 120)
            if content.strip() and not content.startswith(('<', 'Base directory', 'Working in repository')):
                return clean_ask(content)
    return ''


def library_sessions(store):
    """Até 100 sessões principais locais do Claude Code, mais recentes primeiro."""
    with store.connect() as c:
        rows = c.execute("""
            SELECT s.id, s.external_id, s.first_ts, s.last_ts,
                   (SELECT COALESCE(SUM(tokens),0) FROM events e WHERE e.sid=s.id) AS tokens,
                   (SELECT SUM(usd) FROM events e WHERE e.sid=s.id) AS usd,
                   (SELECT text FROM messages m WHERE m.sid=s.id AND m.role='user' AND m.text NOT LIKE 'Base directory%' AND m.text NOT LIKE '<%' ORDER BY ts LIMIT 1) AS ask
            FROM sessions s
            WHERE s.provider='Anthropic' AND s.parent='' AND EXISTS (
              SELECT 1 FROM session_origins o WHERE o.sid=s.id AND o.machine='local')
            ORDER BY s.last_ts DESC LIMIT 100""").fetchall()
    out = []
    for r in rows:
        path = transcript_path(r[1], Path(store.settings['sources']['claude']) / 'projects')
        if not path:
            continue
        out.append({'id': r[1], 'started': r[2], 'ended': r[3], 'tokens': r[4], 'usd': r[5], 'ask': clean_ask(first_ask(path) or r[6]), 'default': not out})
    return out


def session_usage(store, external_id):
    with store.connect() as c:
        row = c.execute("SELECT COALESCE(SUM(e.tokens),0), SUM(e.usd), COUNT(e.id) FROM sessions s JOIN events e ON e.sid=s.id WHERE s.provider='Anthropic' AND s.external_id=?", (external_id,)).fetchone()
    return {'tokens': row[0], 'usd': row[1], 'calls': row[2]}


def compare(store):
    """Resumo das 12 sessões locais mais recentes com transcrição."""
    rows = []
    for row in library_sessions(store)[:12]:
        try:
            data = replay(row['id'], store.data_dir / 'replay-cache', store.rates, Path(store.settings['sources']['claude']) / 'projects')
        except (FileNotFoundError, ValueError):
            continue
        item = summary(data)
        item['request'] = row['ask'] or clean_ask(item['request'])
        rows.append(item)
    return rows
