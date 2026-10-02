import json
import tempfile
import unittest
from pathlib import Path

import replay


def line(ts, kind, content, **extra):
    data = {'type': kind, 'timestamp': f'2026-09-26T08:{ts:02d}:00Z', 'message': {'role': kind, 'content': content}}
    data.update(extra)
    return json.dumps(data)


class ReplayTest(unittest.TestCase):
    def build(self):
        root = Path(tempfile.mkdtemp())
        main = root / 'abc.jsonl'
        sub = root / 'abc' / 'subagents'
        sub.mkdir(parents=True)
        rows = [
            line(0, 'user', '<command-message>game-library-studio</command-message> <command-name>/game-library-studio</command-name> <command-args>revise as bibliotecas contra /Users/example/Code/Games/libraries/brawlhalla-lab</command-args>'),
            line(0, 'user', 'Base directory for this skill: /Users/example/Code/Games/.claude/skills/game-library-studio'),
            line(1, 'assistant', [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': '/Users/example/Code/Games/squads/game-library-studio/references/extraction-bar.md'}}], **{'message': {'role': 'assistant', 'model': 'claude-opus-5-5', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': '/Users/example/Code/Games/squads/game-library-studio/references/extraction-bar.md'}}]}}),
            line(2, 'assistant', [{'type': 'tool_use', 'id': 'a1', 'name': 'Agent', 'input': {'subagent_type': 'auditor', 'description': 'Auditar kingdom-rush', 'prompt': 'x'}}]),
            json.dumps({'type': 'user', 'timestamp': '2026-09-26T08:02:01Z', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a1', 'content': 'Async agent launched successfully.\nagentId: ag1'}]}, 'toolUseResult': {'isAsync': True, 'agentId': 'ag1', 'resolvedModel': 'claude-sonnet-5-5'}}),
            line(3, 'assistant', [{'type': 'tool_use', 'id': 'g1', 'name': 'Bash', 'input': {'command': 'cd /Users/example/Code/Games/libraries/kingdom-rush && python3 validate.py'}}]),
            line(3, 'user', [{'type': 'tool_result', 'tool_use_id': 'g1', 'content': 'FAILED: 2 checks', 'is_error': True}]),
            line(4, 'assistant', [{'type': 'tool_use', 'id': 'g2', 'name': 'Bash', 'input': {'command': 'grep -n run validate.py'}}]),
            line(5, 'user', 'This session is being continued from a previous conversation.'),
            line(9, 'user', '<task-notification><task-id>ag1</task-id><status>completed</status><summary>Agent "Auditar kingdom-rush" finished</summary></task-notification>'),
            line(10, 'assistant', [{'type': 'tool_use', 'id': 'c1', 'name': 'Bash', 'input': {'command': 'git commit -q -m "chore: recibo do gate" && git push'}}]),
            line(10, 'user', [{'type': 'tool_result', 'tool_use_id': 'c1', 'content': 'ok'}]),
        ]
        main.write_text('\n'.join(rows))
        usage = {'input_tokens': 1000, 'output_tokens': 500, 'cache_read_input_tokens': 100000, 'cache_creation_input_tokens': 0}
        (sub / 'agent-ag1.jsonl').write_text('\n'.join([
            json.dumps({'type': 'assistant', 'timestamp': '2026-09-26T08:02:10Z', 'message': {'id': 'm1', 'model': 'claude-sonnet-5-5', 'usage': usage, 'content': [{'type': 'tool_use', 'id': 'x', 'name': 'Read', 'input': {}}]}}),
            json.dumps({'type': 'assistant', 'timestamp': '2026-09-26T08:02:11Z', 'message': {'id': 'm1', 'model': 'claude-sonnet-5-5', 'usage': usage, 'content': []}}),
            json.dumps({'type': 'user', 'timestamp': '2026-09-26T08:07:00Z', 'message': {'role': 'user', 'content': '<task-notification><task-id>ag2</task-id><status>failed</status><summary>Agent "neto" failed: Agent terminated early due to an API error: You\'ve hit your session limit</summary></task-notification>'}}),
            json.dumps({'type': 'assistant', 'timestamp': '2026-09-26T08:08:00Z', 'effort': 'xhigh', 'message': {'id': 'm2', 'model': 'claude-sonnet-5-5', 'usage': usage, 'content': [{'type': 'tool_use', 'id': 'y', 'name': 'Bash', 'input': {'command': 'python3 validate.py'}}]}}),
        ]))
        (sub / 'agent-ag2.jsonl').write_text('\n'.join([
            # o fork herda a mensagem m1 do pai: ela não pode custar duas vezes
            json.dumps({'type': 'assistant', 'timestamp': '2026-09-26T08:04:00Z', 'message': {'id': 'm1', 'model': 'claude-sonnet-5-5', 'usage': usage, 'content': []}}),
            json.dumps({'type': 'assistant', 'timestamp': '2026-09-26T08:04:00Z', 'message': {'id': 'm3', 'model': 'claude-opus-5-5', 'usage': usage, 'content': []}}),
        ]))
        (sub / 'agent-ag2.meta.json').write_text(json.dumps({'agentType': 'fork', 'isFork': True, 'description': 'neto', 'parentAgentId': 'ag1', 'spawnDepth': 2}))
        return main

    def test_parse_real_shape(self):
        data = replay.parse(self.build())
        kinds = [e['kind'] for e in data['events']]
        self.assertEqual(data['request'], 'revise as bibliotecas contra libraries/brawlhalla-lab')
        self.assertEqual(data['title'], '/game-library-studio')
        self.assertEqual(data['model'], 'Opus 5.5')
        self.assertIn('compact', kinds)
        agent = next(e for e in data['events'] if e['kind'] == 'agent')
        self.assertEqual((agent['type'], agent['model'], agent['state']), ('auditor', 'Sonnet 5.5', 'ok'))
        self.assertEqual(agent['tools'], {'context': 1, 'edit': 0, 'gate': 1, 'tool': 0})
        self.assertEqual(agent['usage']['input'], 2000, 'a mesma message.id conta uma vez')
        self.assertGreater(agent['end'], agent['t'])
        gates = [e for e in data['events'] if e['kind'] == 'gate' and not e.get('by')]
        self.assertEqual(len(gates), 1, 'grep em validate.py não é gate')
        by_agent = [e for e in data['events'] if e['kind'] == 'gate' and e.get('by')]
        self.assertEqual([e['by'] for e in by_agent], ['ag1'], 'gate rodado pelo subagente entra na prova')
        self.assertEqual((gates[0]['module'], gates[0]['ok']), ('libraries/kingdom-rush', False))
        push = next(e for e in data['events'] if e['kind'] == 'push')
        self.assertEqual((push['module'], push['label']), ('libraries/kingdom-rush', 'chore: recibo do gate'))
        self.assertEqual(data['stats']['gates_failed'], 1)
        self.assertNotIn('/Users/', json.dumps(data))

    def test_long_pauses_are_capped(self):
        data = replay.parse(self.build())
        events = data['events']
        self.assertLess(data['duration'], events[-1]['real'] - events[0]['real'])
        for a, b in zip(events, events[1:]):
            self.assertLessEqual(0, b['t'] - a['t'])
            self.assertLessEqual(b['t'] - a['t'], b['real'] - a['real'])
        # 5 min reais entre a compactação e o push viram no máximo 2 marcas de 90 s
        self.assertLessEqual(events[-1]['t'] - events[-2]['t'], 2 * replay.GAP_CAP)

    def test_nested_agents_cost_and_insights(self):
        rates = {'claude-sonnet-5-5': {'input': 2, 'cache': .2, 'write5m': 2.5, 'write1h': 4, 'output': 10},
                 'claude-opus-5-5': {'input': 4, 'cache': .2, 'write5m': 5, 'write1h': 8, 'output': 20}}
        data = replay.parse(self.build(), rates)
        agents = {e['id']: e for e in data['events'] if e['kind'] == 'agent'}
        nested = agents['ag2']
        self.assertEqual((nested['depth'], nested['parent'], nested['model'], nested['state'], nested['reason']), (2, 'ag1', 'Opus 5.5', 'bad', 'limite da sessão'))
        self.assertTrue(nested['fork'])
        self.assertAlmostEqual(agents['ag1']['usage']['usd'], 2 * (1000 * 2 + 100000 * .2 + 500 * 10) / 1e6)
        self.assertAlmostEqual(agents['ag2']['usage']['usd'], (1000 * 4 + 100000 * .2 + 500 * 20) / 1e6, msg='cópia herdada pelo fork fica com o pai')
        self.assertEqual(data['stats']['nested'], 1)
        self.assertEqual(data['stats']['agents_failed'], 1)
        self.assertAlmostEqual(data['curve'][-1][1], data['stats']['usd'], places=2)
        keys = {i['key'] for i in data['insights']}
        self.assertTrue({'money', 'failed', 'parallel'} <= keys)
        estimate = next(i for i in data['insights'] if i['key'] == 'estimate')
        self.assertTrue(estimate['estimate'])

    def test_commands_count_what_runs(self):
        self.assertEqual(replay.kinds('ps aux | grep -E "gate.py|validate.py" | grep -v grep'), [])
        self.assertEqual(replay.kinds("python3 - <<'EOF'\np = Path('validate.py')\nEOF"), [], 'heredoc que edita validate.py não é gate')
        self.assertEqual(replay.kinds('python3 framework/scripts/gameops.py preflight . && git commit -q -m x'), ['commit', 'gate'])
        self.assertEqual(replay.kinds('cat > a.md <<EOF\nnpm test\nEOF\npython3 validate.py'), ['gate'])
        self.assertEqual(replay.kinds('git -c user.email=a@b commit -m "git push depois"'), ['commit'])
        self.assertEqual(replay.module_of('python3 squads/game-library-studio/scripts/gls.py check libraries/a-valquiria'), 'libraries/a-valquiria')
        self.assertEqual(replay.module_of('cd "/x/Code/Games/libraries/hades-ii" && python3 validate.py'), 'libraries/hades-ii')
        self.assertEqual(replay.module_of('python3 squads/rom-extract/validate.py'), 'hub')

    def test_failures_and_paths(self):
        self.assertTrue(replay.failed({}, '# pass 60\n# fail 1\n'), 'falha do node:test')
        self.assertTrue(replay.failed({}, 'ok 1\nnot ok 2 - x\n'))
        self.assertFalse(replay.failed({}, '# pass 60\n# fail 0\n'))
        state, reason = replay.notification('<task-notification><task-id>a9</task-id><status>completed</status><summary>Agent "x" stopped at its 40-turn limit (partial result)</summary></task-notification>')[1:3]
        self.assertEqual((state, reason), ('stop', 'limite de turnos (resultado parcial)'))
        self.assertEqual(replay.tidy('du -sh /private/var/folders/vv/kd/T/co'), 'du -sh tmp')
        self.assertEqual(replay.hours(34 * 3600 + 25 * 60 + 50), '34 h 26 min')

    def test_tool_labels_are_readable(self):
        self.assertEqual(replay.tool_label('SendMessage', {'to': 'auditor-kr'}), 'Mensagem para o agente auditor-kr')
        self.assertEqual(replay.tool_label('SendMessage', {'to': 'uds:/tmp/cc-socks/1.sock'}), 'Mensagem para outra sessão local')
        self.assertEqual(replay.tool_label('ListAgents', {}), 'Conferiu os agentes em andamento')


if __name__ == '__main__':
    unittest.main()
