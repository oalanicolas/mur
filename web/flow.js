'use strict';
(() => {
  const view = document.getElementById('flow');
  if (!view) return;
  const byId = id => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  const REPLAY_SECONDS = 150;      // uma sessão inteira em 1×
  const FEED_SIZE = 9;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const zone = {get timeZone(){return appState?.timezone;}};
  const hhmm = ts => new Date(ts * 1000).toLocaleTimeString('pt-BR', {hour:'2-digit', minute:'2-digit', ...zone});
  const ddmm = ts => new Date(ts * 1000).toLocaleDateString('pt-BR', {day:'2-digit', month:'2-digit', ...zone});
  const full = ts => new Date(ts * 1000).toLocaleString('pt-BR', {day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', ...zone});
  const int = n => new Intl.NumberFormat('pt-BR').format(n || 0);
  const big = n => n >= 1e9 ? (n / 1e9).toLocaleString('pt-BR', {maximumFractionDigits:2}) + ' bi' : n >= 1e6 ? (n / 1e6).toLocaleString('pt-BR', {maximumFractionDigits:1}) + ' mi' : n >= 1e3 ? Math.round(n / 1e3) + ' mil' : int(n);
  const usd = n => n == null ? '—' : 'US$ ' + n.toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
  const span = s => {s = Math.max(0, Math.round(s)); if (s < 60) return s + ' s'; const m = Math.round(s / 60); return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0') + ' min';};
  const plural = (n, one, many) => int(n) + ' ' + (n === 1 ? one : many);
  const word = (n, one, many) => n === 1 ? one : many;
  // escreve só quando muda: o relógio roda a cada quadro e texto igual não deve custar layout
  const setText = (el, text) => {if (el && el.textContent !== text) el.textContent = text;};
  const cut = (text, max) => text.length <= max ? text : text.slice(0, max).replace(/\s+\S*$/, '') + '…';
  const family = model => /opus/i.test(model) ? 'opus' : /sonnet/i.test(model) ? 'sonnet' : /haiku/i.test(model) ? 'haiku' : 'other';
  const STATE = {ok:'concluído', bad:'falhou', stop:'interrompido', run:'rodando', wait:'na fila'};
  const PLACE = {request:'user', question:'user', context:'opus', edit:'opus', tool:'opus', skill:'opus', say:'opus', compact:'opus', agent:'agents', gate:'gates', commit:'ship', push:'ship', deploy:'ship'};
  const ICON = {request:'i-user', question:'i-user', context:'i-file', edit:'i-file', tool:'i-spark', skill:'i-spark', say:'i-spark', compact:'i-compact', agent:'i-agents', gate:'i-gate', commit:'i-ship', push:'i-ship', deploy:'i-ship'};
  const WIRES = [['user', 'opus'], ['opus', 'context'], ['opus', 'agents'], ['agents', 'gates'], ['gates', 'ship']];
  const ROUTE = {request:['user', 'opus'], question:['user', 'opus'], context:['opus', 'context'], agent:['opus', 'agents'], gate:['agents', 'gates'], commit:['gates', 'ship'], push:['gates', 'ship'], deploy:['gates', 'ship']};

  function node(tag, text, cls) {const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e;}
  function icon(id, cls) {const s = document.createElementNS(SVG, 'svg'); s.setAttribute('class', 'icon' + (cls ? ' ' + cls : '')); s.setAttribute('aria-hidden', 'true'); const u = document.createElementNS(SVG, 'use'); u.setAttribute('href', '#' + id); s.append(u); return s;}
  function tag(model) {return node('span', model || '—', 'rp-tag ' + family(model));}

  let data = null, T = 0, speed = 1, playing = !reduced.matches, raf = 0, last = 0, painted = -1, paintedAt = 0, loadedId = '';
  let events = [], agents = [], ends = [], duration = 1, byAgent = new Map(), families = [], treeOrder = 'tree', treeAll = false, compareRows = null;
  let replaySequence=0,replayController;
  let libraryPending=false,libraryEmpty=false,libraryRefreshQueued=false;
  const TREE_ROWS = 16;

  // ---------- carga ----------
  async function json(path,signal) {return api(path,undefined,signal);}

  async function loadSessions() {
    const done=startLoading('replay-list','Carregando sessões do fluxo…');
    try {
      const list = await json('/api/replay/sessions');
      const rows = list.rows || [];
      const select = byId('rp-session');
      select.replaceChildren(...rows.map(s => {const o = node('option', ddmm(s.started) + ' · ' + cut(s.ask || 'Claude Code', 64) + ' · ' + (s.usd == null ? big(s.tokens) : usd(s.usd))); o.value = s.id; return o;}));
      select.value = list.default;
      if (!select.value && rows[0]) select.value = rows[0].id;
      return select.value;
    } finally {done();}
  }

  async function load(id) {
    replayController?.abort();replayController=new AbortController();const controller=replayController,mine=++replaySequence;
    loadedId = id || 'padrão';
    data=null;view.classList.remove('rp-failed');byId('rp-retry').hidden=true;
    for(const control of view.querySelectorAll('.flow-controls button'))control.disabled=true;
    byId('rp-ask').textContent = 'Lendo a transcrição…';
    view.classList.add('rp-loading');
    const done=startLoading('replay-session','Carregando a sessão do fluxo…',[...view.querySelectorAll('.replay-map,.replay-lower,.rp-insights,.rp-tree,.replay-facts')]);
    try {
      const body = await json('/api/replay' + (id ? '?id=' + encodeURIComponent(id) : ''),controller.signal);
      if (replaySequence !== mine) return;
      data = body;
      for(const control of view.querySelectorAll('.flow-controls button'))control.disabled=false;
      prepare();
      T = reduced.matches ? duration : 0;
      painted = -1;
      if (T >= duration) playing = false;
      syncControls();
      paint(true);
      markCompare();
    } catch (err) {
      if(replaySequence===mine&&!controller.signal.aborted){byId('rp-ask').textContent = err.message;view.classList.add('rp-failed');byId('rp-retry').hidden=false;}
    } finally {done();if(replaySequence===mine)view.classList.remove('rp-loading');}
  }

  function prepare() {
    events = data.events || [];
    duration = Math.max(1, data.duration || events.at(-1)?.t || 1);
    agents = events.filter(e => e.kind === 'agent');
    byAgent = new Map(agents.filter(a => a.id).map(a => [a.id, a]));
    ends = agents.map(a => ({t:a.end, kind:'agent-end', agent:a, real:a.real_end || a.real})).sort((a, b) => a.t - b.t);
    const stats = data.stats || {};
    const first = events.find(e => e.kind === 'request');
    byId('rp-ask').textContent = data.request || first?.label || '—';
    byId('rp-byline').textContent = 'Você · ' + full(data.started) + (data.title ? ' · ' + data.title : '');
    byId('rp-duration').textContent = span(data.ended - data.started) + ' · ' + span(duration) + ' ativas';
    byId('rp-tokens').textContent = big(stats.tokens || data.usage?.tokens);
    const cost = byId('rp-usd');
    cost.textContent = usd(stats.usd ?? data.usage?.usd);
    cost.title = 'Calculado mensagem a mensagem desta transcrição e dos subagentes' + (data.usage?.usd ? '; o índice registra ' + usd(data.usage.usd) : '');
    byId('rp-effort').textContent = data.effort || '—';
    const model = byId('rp-model');
    model.textContent = (data.model || 'Claude') + (data.effort ? ' · ' + data.effort : '');
    model.className = 'rp-tag ' + family(data.model);
    // uma linha por modelo, com um ponto por agente
    const order = ['opus', 'sonnet', 'haiku', 'other'];
    const groups = new Map();
    agents.forEach(a => {const k = a.model || 'sem modelo'; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(a);});
    families = [...groups].sort((a, b) => order.indexOf(family(a[0])) - order.indexOf(family(b[0])) || b[1].length - a[1].length).map(([name, list]) => {
      const row = node('div', null, 'rp-model-row');
      row.dataset.model = family(name);
      const head = node('p', null, 'rp-model-head');
      const count = node('b'), spent = node('span', '', 'rp-model-usd');
      head.append(tag(name), count, spent);
      const swarm = node('div', null, 'rp-swarm');
      list.forEach(a => {const d = node('i', null, 'rp-dot'); d.title = a.label + ' · ' + (a.model || a.type) + (a.depth > 1 ? ' · aberto por outro agente' : ''); d.dataset.model = family(a.model); if (a.depth > 1) d.dataset.nested = ''; a._dot = d; swarm.append(d);});
      row.append(head, swarm);
      return {name, list, count, spent, row};
    });
    byId('rp-models').replaceChildren(...(families.length ? families.map(f => f.row) : [node('p', 'Esta sessão não usou subagentes.', 'rp-empty')]));
    byId('rp-running').hidden=!agents.length;
    buildGantt();
    insights();
    tree();
    wires();
  }

  // ---------- estado no instante T ----------
  function upto(list, t) {let lo = 0, hi = list.length; while (lo < hi) {const mid = lo + hi >> 1; if (list[mid].t <= t) lo = mid + 1; else hi = mid;} return lo;}
  function realClock(t) {
    const i = upto(events, t) - 1;
    if (i < 0) return data.started;
    const e = events[i], next = events[i + 1];
    const gap = next ? Math.min(t - e.t, next.real - e.real) : t - e.t;
    return e.real + Math.max(0, gap);
  }
  function agentState(a, t) {return t < a.t ? 'wait' : t < a.end ? 'run' : a.state || 'ok';}
  function agentSpent(a, t) {const total = a.usage?.usd || 0; if (t <= a.t) return 0; if (t >= a.end) return total; return total * (t - a.t) / Math.max(1, a.end - a.t);}
  function costAt(t) {
    const curve = data.curve || [];
    let lo = 0, hi = curve.length;
    while (lo < hi) {const mid = lo + hi >> 1; if (curve[mid][0] <= t) lo = mid + 1; else hi = mid;}
    return lo ? curve[lo - 1] : [0, 0, 0];
  }

  function paint(force) {
    if (!data) return;
    const n = upto(events, T), m = upto(ends, T);
    const key = n * 10000 + m;
    const now = performance.now();
    progress();
    if (!force && key === painted && now - paintedAt < 900) {runningBars(); return;}
    const fresh = force || painted < 0 ? [] : events.slice(Math.floor(painted / 10000), n);
    painted = key; paintedAt = now;
    const seen = events.slice(0, n);
    const count = kind => seen.reduce((s, e) => s + (e.kind === kind ? 1 : 0), 0);
    const lastOf = kinds => {for (let i = n - 1; i >= 0; i--) if (kinds.includes(events[i].kind)) return events[i]; return null;};
    // Você
    const asks = count('request') + count('question'), reads = count('context'), edits = count('edit');
    const tools = count('tool') + count('skill') + count('gate') + count('commit') + count('push') + count('deploy');
    byId('rp-n-user').textContent = int(asks);
    byId('rp-l-user').textContent = word(asks, ' mensagem', ' mensagens');
    const ask = lastOf(['request', 'question']);
    byId('rp-now-user').textContent = ask ? (ask.kind === 'question' ? 'Respondeu: ' : '') + ask.label : 'Ainda sem pedido.';
    // Sessão principal
    byId('rp-n-read').textContent = int(reads);
    byId('rp-l-read').textContent = word(reads, ' leitura · ', ' leituras · ');
    byId('rp-n-edit').textContent = int(edits);
    byId('rp-l-edit').textContent = word(edits, ' edição · ', ' edições · ');
    byId('rp-n-tool').textContent = int(tools);
    byId('rp-l-tool').textContent = word(tools, ' comando', ' comandos');
    const act = lastOf(['context', 'edit', 'tool', 'skill', 'say', 'agent', 'gate', 'commit', 'push', 'deploy']);
    byId('rp-now-opus').textContent = act ? describe(act) : 'Esperando o pedido.';
    byId('rp-compact').hidden = !seen.some(e => e.kind === 'compact');
    // Contexto
    const files = [];
    for (let i = n - 1; i >= 0 && files.length < 4; i--) if (events[i].kind === 'context') files.push(events[i]);
    byId('rp-files').replaceChildren(...(files.length ? files.map(f => node('li', f.label, f.path ? 'path' : '')) : [node('li', 'Nada lido ainda.', 'rp-empty')]));
    // Subagentes por modelo
    const states = agents.map(a => agentState(a, T));
    agents.forEach((a, i) => {if (a._dot && a._dot.dataset.state !== states[i]) a._dot.dataset.state = states[i];});
    const run = states.filter(s => s === 'run').length, done = states.filter(s => s === 'ok').length, cut = states.filter(s => s === 'stop').length, bad = states.filter(s => s === 'bad').length;
    byId('rp-agents-sum').textContent = agents.length ? run + ' rodando · ' + done + word(done, ' concluído', ' concluídos') + (bad ? ' · ' + bad + word(bad, ' falhou', ' falharam') : '') + (cut ? ' · ' + cut + word(cut, ' interrompido', ' interrompidos') : '') + ' · ' + agents.length + ' na sessão' : 'nenhum nesta sessão';
    const running = agents.filter((a, i) => states[i] === 'run');
    const rows = running.slice(0, 6).map(a => {
      const li = node('li');
      const head = node('div', null, 'rp-run-head');
      head.append(tag(a.model), node('b', a.label), node('span', a.fork ? 'fork' : a.type, 'rp-type'));
      const bar = node('div', null, 'rp-run-bar ' + family(a.model)); bar.append(node('i'));
      const parent = a.depth > 1 ? byAgent.get(a.parent) : null;
      li.append(head, bar, node('span', '', 'rp-run-time'));
      if (parent) li.append(node('span', '↳ aberto por ' + parent.label, 'rp-run-parent'));
      li._agent = a; return li;
    });
    if (running.length > 6) rows.push(node('li', '+' + (running.length - 6) + ' rodando ao mesmo tempo', 'rp-more'));
    if (!running.length) rows.push(node('li', !agents.length ? 'Esta sessão não usou subagentes.' : done + cut + bad ? 'Nenhum subagente rodando agora.' : 'A sessão ainda não delegou.', 'rp-empty'));
    byId('rp-running').replaceChildren(...rows);
    runningBars();
    // Prova
    const gates = seen.filter(e => e.kind === 'gate');
    const red = gates.filter(g => g.ok === false).length;
    byId('rp-gates-sum').textContent = gates.length ? plural(gates.length, 'execução', 'execuções') + ' · ' + plural(red, 'vermelha', 'vermelhas') : 'nenhum gate ainda';
    byId('rp-gates').replaceChildren(...(gates.length ? gates.slice(-5).reverse().map(g => {const li = node('li', null, g.ok === false ? 'bad' : 'ok'); li.append(icon(g.ok === false ? 'i-x' : 'i-check'), node('b', g.label), node('span', g.module === 'hub' ? 'workspace' : g.module.split('/').pop())); return li;}) : [node('li', 'Os gates rodam depois do trabalho.', 'rp-empty')]));
    // Entrega
    const ship = seen.filter(e => ['commit', 'push', 'deploy'].includes(e.kind) && e.ok !== false);
    const modules = new Map();
    ship.forEach(e => modules.set(e.module, modules.get(e.module) === 'push' && e.kind === 'commit' ? 'push' : e.kind));
    byId('rp-ship-sum').textContent = ship.length ? plural(count('commit'), 'commit', 'commits') + ' · ' + count('push') + ' com push' + (count('deploy') ? ' · ' + plural(count('deploy'), 'deploy', 'deploys') : '') : 'nada entregue ainda';
    const chips = byId('rp-modules'), had = new Map([...chips.children].map(li => [li.dataset.module, li]));
    chips.replaceChildren(...(modules.size ? [...modules].map(([mod, kind]) => {
      const li = had.get(mod) || node('li', mod === 'hub' ? 'hub · gitlink' : mod.split('/').pop());
      li.dataset.module = mod; li.className = kind + (had.has(mod) || !had.size && force ? '' : ' new'); li.title = mod + ' · ' + kind; return li;
    }) : [node('li', 'Commits, envios e publicações registrados aparecem aqui.', 'rp-empty')]));
    // Nós acesos e pulsos nos conectores
    const recent = duration / REPLAY_SECONDS * 1.4;
    const lit = new Set();
    for (let i = n - 1; i >= 0 && T - events[i].t < recent; i--) lit.add(PLACE[events[i].kind]);
    if (running.length) lit.add('agents');
    for (const el of byId('rp-map').querySelectorAll('.rp-node')) el.classList.toggle('lit', lit.has(el.dataset.node));
    if (playing && !reduced.matches) fresh.slice(-4).forEach(e => pulse(ROUTE[e.kind]));
    feed(n, m);
    treeState(states);
  }

  function describe(e) {
    if (e.kind === 'agent') {
      const parent = e.depth > 1 ? byAgent.get(e.parent) : null;
      return (parent ? 'Um agente abriu ' : 'Delegou a ') + (e.fork ? 'um fork' : e.type) + ' em ' + (e.model || 'modelo herdado') + ': ' + e.label;
    }
    if (e.kind === 'gate') return 'Gate ' + e.label + (e.ok === false ? ' falhou' : ' passou') + (e.module && e.module !== 'hub' ? ' em ' + e.module : '');
    if (e.kind === 'commit') return 'Commit em ' + e.module + ': ' + e.label;
    if (e.kind === 'push') return 'Push em ' + e.module + ': ' + e.label;
    if (e.kind === 'deploy') return 'Deploy: ' + e.label;
    if (e.kind === 'skill') return 'Carregou a skill ' + e.label;
    if (e.kind === 'context') return e.path ? 'Leu ' + e.label : e.label;
    if (e.kind === 'edit') return 'Editou ' + e.label;
    if (e.kind === 'compact') return 'Contexto compactado; o handoff voltou e o trabalho seguiu.';
    if (e.kind === 'question') return 'Perguntou: ' + e.label;
    return e.label;
  }

  // custo e barras que mudam a cada quadro
  function runningBars() {
    for (const li of byId('rp-running').children) {
      const a = li._agent; if (!a) continue;
      const f = Math.min(1, Math.max(0, (T - a.t) / Math.max(1, a.end - a.t)));
      li.querySelector('.rp-run-bar i').style.transform = 'scaleX(' + f.toFixed(3) + ')';
      setText(li.querySelector('.rp-run-time'), 'há ' + span(Math.max(60, realClock(T) - a.real)) + ' · ' + usd(agentSpent(a, T)));
    }
    for (const f of families) {
      const started = f.list.filter(a => a.t <= T).length;
      setText(f.count, started + ' de ' + f.list.length);
      setText(f.spent, usd(f.list.reduce((s, a) => s + agentSpent(a, T), 0)));
    }
    const [, total, main] = costAt(T);
    setText(byId('rp-cost-now'), usd(total) + ' até aqui');
    setText(byId('rp-main-cost'), 'A sessão principal custou ' + usd(main) + ' até aqui');
  }

  function feed(n, m) {
    const items = [];
    let i = n - 1, j = m - 1;
    while (items.length < FEED_SIZE && (i >= 0 || j >= 0)) {
      if (j >= 0 && (i < 0 || ends[j].t >= events[i].t)) {items.push(ends[j]); j--;}
      else {items.push(events[i]); i--;}
    }
    const list = byId('rp-feed');
    const prev = new Set([...list.children].map(li => li.dataset.key));
    list.replaceChildren(...items.map(e => {
      const end = e.kind === 'agent-end', state = end ? e.agent.state || 'ok' : '';
      const li = node('li', null, 'k-' + (end ? 'end-' + state : e.kind) + (e.ok === false ? ' bad' : ''));
      li.dataset.key = e.kind + e.t + (e.label || e.agent?.label || '');
      if (prev.size && !prev.has(li.dataset.key)) li.classList.add('new');
      let text;
      if (end) {
        const a = e.agent, money = a.usage ? ' · ' + usd(a.usage.usd) : '';
        text = (state === 'ok' ? 'Devolveu' : state === 'bad' ? 'Falhou' + (a.reason ? ' por ' + a.reason : '') : 'Interrompido') + ' (' + (a.model || '—') + money + '): ' + a.label;
      } else text = e.kind === 'request' ? 'Você: ' + e.label : describe(e);
      li.append(node('time', hhmm(e.real)), icon(end ? (state === 'ok' ? 'i-check' : 'i-stop') : e.kind === 'gate' ? (e.ok === false ? 'i-x' : 'i-check') : ICON[e.kind] || 'i-spark'), node('span', text));
      li.title = text;
      return li;
    }));
  }

  function progress() {
    const f = T / duration, clock = realClock(T), pct = String(Math.round(f * 100));
    const when = ddmm(clock) + ' · ' + hhmm(clock);
    setText(byId('rp-clock'), when);
    setText(byId('rp-progress'), pct + '% da sessão');
    const head = gantt.querySelector('.rp-head');
    if (head) head.style.transform = 'translateX(' + (f * 100).toFixed(3) + '%)';
    if (gantt.getAttribute('aria-valuenow') !== pct) {
      gantt.setAttribute('aria-valuenow', pct);
      gantt.setAttribute('aria-valuetext', pct + '% · ' + when + ' · ' + usd(costAt(T)[1]) + ' gastos até aqui');
    }
  }

  function seek(t) {T = Math.min(duration, Math.max(0, t)); playing = false; syncControls(); painted = -1; paint(true);}

  // ---------- linha do tempo ----------
  function buildGantt() {
    const host = byId('rp-gantt');
    const lanes = [['user', 'Você'], ['opus', 'Sessão'], ['agents', 'Subagentes'], ['gates', 'Prova'], ['ship', 'Entrega'], ['cost', 'Custo']];
    const rows = [];
    agents.forEach(a => {let r = rows.findIndex(end => end <= a.t); if (r < 0) {r = rows.length; rows.push(0);} rows[r] = a.end; a._row = r;});
    const agentRows = Math.max(1, Math.min(rows.length, 18));
    const heights = {user:14, opus:14, agents:agentRows * 5 + 4, gates:14, ship:14, cost:34};
    const W = 1000, gap = 8;
    let y = 0;
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('aria-hidden', 'true');
    const labels = node('div', null, 'rp-lanes');
    labels.setAttribute('aria-hidden', 'true');
    const shape = (tagName, attrs, title) => {const r = document.createElementNS(SVG, tagName); for (const [k, v] of Object.entries(attrs)) r.setAttribute(k, v); if (title) {const t = document.createElementNS(SVG, 'title'); t.textContent = title; r.append(t);} svg.append(r); return r;};
    const add = (cls, x, yy, w, h, title) => shape('rect', {x:x.toFixed(2), y:yy.toFixed(2), width:Math.max(.8, w).toFixed(2), height:h, class:cls}, title);
    const X = t => t / duration * W;
    for (const [lane, label] of lanes) {
      const h = heights[lane];
      const l = node('span', label); l.style.height = h + 'px'; l.style.marginBottom = gap + 'px'; labels.append(l);
      add('lane', 0, y, W, h);
      if (lane === 'agents') agents.forEach(a => add('bar ' + family(a.model) + (a.state === 'bad' ? ' bad' : a.state === 'stop' ? ' stop' : ''), X(a.t), y + 2 + (a._row % agentRows) * 5, X(a.end) - X(a.t), 3, a.label + ' · ' + (a.model || '—') + (a.usage ? ' · ' + usd(a.usage.usd) : '')));
      else if (lane === 'cost') {
        const curve = data.curve || [], top = Math.max(.01, curve.at(-1)?.[1] || 0);
        if (curve.length) {
          const Y = v => (y + h - 2 - v / top * (h - 4)).toFixed(2);
          const line = curve.map(p => X(p[0]).toFixed(2) + ' ' + Y(p[1])).join(' L');
          shape('path', {d:'M0 ' + (y + h - 2) + ' L' + line + ' L' + W + ' ' + Y(curve.at(-1)[1]) + ' L' + W + ' ' + (y + h - 2) + ' Z', class:'cost-area'});
          shape('path', {d:'M' + line, class:'cost-line'}, 'Custo acumulado: ' + usd(curve.at(-1)[1]));
          shape('path', {d:'M' + curve.map(p => X(p[0]).toFixed(2) + ' ' + Y(p[2])).join(' L'), class:'cost-main'}, 'Só a sessão principal: ' + usd(curve.at(-1)[2]));
        }
      } else events.filter(e => PLACE[e.kind] === lane).forEach(e => add('tick k-' + e.kind + (e.ok === false ? ' bad' : ''), X(e.t), y + 2, 1.2, h - 4));
      y += h + gap;
    }
    events.filter(e => e.kind === 'compact').forEach(e => add('compact-line', X(e.t), 0, 1, y - gap, 'Contexto compactado'));
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + (y - gap));
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.style.height = (y - gap) + 'px';
    const plot = node('div', null, 'rp-plot');
    const track = node('div', null, 'rp-track'); track.append(node('div', null, 'rp-head'));
    plot.append(svg, track);
    host.replaceChildren(labels, plot);
  }

  const gantt = byId('rp-gantt');
  function seekFrom(event) {
    const plot = gantt.querySelector('.rp-plot'); if (!plot || !data) return;
    const box = plot.getBoundingClientRect();
    T = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)) * duration;
    if (T >= duration) playing = false;
    syncControls();
    painted = -1; paint(true);
  }
  gantt.addEventListener('pointerdown', event => {if (!event.target.closest('.rp-plot')) return; gantt.setPointerCapture(event.pointerId); seekFrom(event); gantt.classList.add('dragging');});
  gantt.addEventListener('pointermove', event => {if (gantt.hasPointerCapture(event.pointerId)) seekFrom(event);});
  gantt.addEventListener('pointerup', () => gantt.classList.remove('dragging'));
  gantt.addEventListener('keydown', event => {
    const step = {ArrowRight:.02, ArrowLeft:-.02, PageUp:.1, PageDown:-.1}[event.key];
    if (event.key === 'Home') T = 0; else if (event.key === 'End') T = duration; else if (step) T = Math.min(duration, Math.max(0, T + step * duration)); else return;
    if (T >= duration) playing = false;
    event.preventDefault(); syncControls(); painted = -1; paint(true);
  });

  // ---------- leituras ----------
  function insights() {
    const list = data.insights || [];
    byId('rp-insights').replaceChildren(...(list.length ? list.map(item => {
      const card = node('article', null, 'rp-insight' + (item.tone ? ' ' + item.tone : '') + (item.estimate ? ' estimate' : ''));
      const head = node('p', null, 'rp-insight-kicker');
      head.append(node('span', item.kicker));
      if (item.estimate) head.append(node('span', 'estimativa', 'rp-badge'));
      card.append(head, node('b', item.value, 'rp-insight-value'));
      if (item.bars?.length) {
        const total = item.bars.reduce((s, b) => s + b.usd, 0) || 1;
        const bar = node('div', null, 'rp-split');
        bar.setAttribute('role', 'img');
        bar.setAttribute('aria-label', item.bars.map(b => b.label + ' ' + usd(b.usd)).join(', '));
        item.bars.forEach(b => {const seg = node('i', null, /principal/.test(b.label) ? 'main' : family(b.label)); seg.style.width = (b.usd / total * 100).toFixed(2) + '%'; seg.title = b.label + ' · ' + usd(b.usd); bar.append(seg);});
        const key = node('p', null, 'rp-split-key');
        item.bars.forEach(b => {const k = node('span'); k.append(node('i', null, /principal/.test(b.label) ? 'main' : family(b.label)), document.createTextNode(b.label + ' ' + usd(b.usd))); key.append(k);});
        card.append(bar, key);
      }
      card.append(node('p', item.text, 'rp-insight-text'));
      if (item.t != null) {
        const go = node('button', 'Ver no replay', 'rp-link');
        go.type = 'button';
        go.addEventListener('click', () => {seek(item.t); byId('rp-map').scrollIntoView({behavior:reduced.matches ? 'auto' : 'smooth', block:'start'});});
        card.append(go);
      }
      return card;
    }) : [node('p', 'Sem dados suficientes para leituras nesta sessão.', 'rp-empty')]));
  }

  // ---------- tabela de agentes ----------
  // abaixo de 820 px a tabela vira cartões sem cabeçalho: cada célula leva o nome da coluna
  const AGENT_COLS = ['Agente', 'Modelo', 'Duração', 'Trabalho', 'Tokens', 'Custo', 'Estado'];
  const COMPARE_COLS = ['Data', 'Pedido', 'Ativa', 'Agentes por modelo', 'Custo', 'Gates', 'Entrega'];
  function labelCells(tr, names) {[...tr.children].forEach((td, i) => {td.dataset.label = names[i];});}
  function tree() {
    const body = byId('rp-agent-rows');
    const main = data.main_usage || {};
    const max = Math.max(main.usd || 0, ...agents.map(a => a.usage?.usd || 0), .01);
    let ordered;
    if (treeOrder === 'cost') ordered = [...agents].sort((a, b) => (b.usage?.usd || 0) - (a.usage?.usd || 0));
    else {
      const kids = new Map();
      agents.forEach(a => {const p = a.depth > 1 && byAgent.has(a.parent) ? a.parent : ''; if (!kids.has(p)) kids.set(p, []); kids.get(p).push(a);});
      ordered = [];
      const walk = (id, guard) => (kids.get(id) || []).forEach(a => {ordered.push(a); if (a.id && guard < 6) walk(a.id, guard + 1);});
      walk('', 0);
    }
    const tokens = u => u ? (u.input || 0) + (u.cache || 0) + (u.write || 0) + (u.output || 0) : 0;
    const costCell = value => {const td = node('td', null, 'num rp-cost-cell'); const bar = node('span', null, 'rp-cost-bar'); const fill = node('i'); fill.style.width = (value / max * 100).toFixed(1) + '%'; bar.append(fill); td.append(bar, node('span', usd(value))); return td;};
    const rows = [];
    const lead = node('tr', null, 'rp-main-row');
    const name = node('td', null, 'rp-agent-name'); name.append(node('b', 'Sessão principal'), node('span', 'conversa com você', 'rp-type'));
    const mtd = node('td'); mtd.append(tag(data.model));
    lead.append(name, mtd, node('td', span(duration) + ' ativas'), node('td', plural(data.stats?.context || 0, 'leitura', 'leituras') + ' no total', 'rp-muted'), node('td', big(tokens(main)), 'num'), costCell(main.usd || 0), node('td', data.effort ? 'esforço ' + data.effort : '—', 'rp-muted'));
    labelCells(lead, AGENT_COLS);
    rows.push(lead);
    ordered.forEach(a => {
      const tr = node('tr');
      tr._agent = a;
      const cell = node('td', null, 'rp-agent-name');
      if (treeOrder === 'tree' && a.depth > 1) cell.style.paddingLeft = (14 + (a.depth - 1) * 18) + 'px';
      if (treeOrder === 'tree' && a.depth > 1) cell.append(node('span', '↳', 'rp-branch'));
      const go = node('button', null, 'rp-row-btn'); go.type = 'button'; go.append(node('b', a.label)); go.setAttribute('aria-label', 'Reproduzir a partir de ' + a.label);
      cell.append(go, node('span', (a.fork ? 'fork' : a.type) + (treeOrder === 'cost' && a.depth > 1 ? ' · nível ' + a.depth : ''), 'rp-type'));
      const model = node('td'); model.append(tag(a.model));
      const tools = a.tools || {};
      const work = [tools.context ? plural(tools.context, 'leitura', 'leituras') : '', tools.edit ? plural(tools.edit, 'edição', 'edições') : '', tools.gate ? plural(tools.gate, 'gate', 'gates') : ''].filter(Boolean).join(' · ') || '—';
      const stateCell = node('td');
      tr._state = stateCell;
      tr.append(cell, model, node('td', span((a.real_end || a.real) - a.real), 'rp-nowrap'), node('td', work, 'rp-muted rp-nowrap'), node('td', big(tokens(a.usage)), 'num'), costCell(a.usage?.usd || 0), stateCell);
      labelCells(tr, AGENT_COLS);
      rows.push(tr);
    });
    rows.forEach((tr, i) => {if (i > TREE_ROWS && !treeAll) tr.hidden = true;});
    body.replaceChildren(...rows);
    const more = byId('rp-tree-more');
    more.hidden = agents.length <= TREE_ROWS;
    more.textContent = treeAll ? 'Mostrar só os ' + TREE_ROWS + ' primeiros' : 'Mostrar os ' + agents.length + ' agentes';
  }
  function stateCell(td, a, s) {
    if (td.dataset.s === s) return;
    td.dataset.s = s;
    td.className = 'rp-state ' + s;
    td.replaceChildren(icon(s === 'ok' ? 'i-check' : s === 'bad' ? 'i-x' : s === 'run' ? 'i-spark' : 'i-stop'), node('span', STATE[s] || s, 'rp-state-text'));
    if (a.reason && (s === 'bad' || s === 'stop')) td.append(node('span', a.reason, 'rp-reason'));
  }
  function treeState(states) {
    const index = new Map(agents.map((a, i) => [a, states[i]]));
    for (const tr of byId('rp-agent-rows').children) {
      const a = tr._agent; if (!a) continue;
      const s = index.get(a);
      tr.classList.toggle('live', s === 'run');
      tr.classList.toggle('later', s === 'wait');
      stateCell(tr._state, a, s);
    }
  }
  byId('rp-agent-rows').addEventListener('click', event => {const tr = event.target.closest('tr'); if (tr?._agent) seek(tr._agent.t + .01);});
  byId('rp-tree-more').addEventListener('click', () => {treeAll = !treeAll; tree(); painted = -1; paint(true);});
  byId('rp-tree-sort').addEventListener('click', event => {
    const b = event.target.closest('button[data-value]'); if (!b || !data) return;
    treeOrder = b.dataset.value;
    for (const o of byId('rp-tree-sort').querySelectorAll('button')) o.setAttribute('aria-pressed', String(o === b));
    tree(); painted = -1; paint(true);
  });

  // ---------- comparativo ----------
  async function loadCompare() {
    if (compareRows) return;
    compareRows = [];
    const note = byId('rp-compare-note');
    byId('rp-compare-retry').hidden=true;
    const done=startLoading('replay-compare','Carregando o comparativo de sessões…');
    note.textContent = 'Lendo as transcrições… a primeira vez leva até um minuto.';
    try {compareRows = (await json('/api/replay/compare')).rows || [];}
    catch (err) {note.textContent = err.message;compareRows=null;byId('rp-compare-retry').hidden=false;return;}
    finally {done();}
    const maxAgents = Math.max(1, ...compareRows.map(r => Object.values(r.agents || {}).reduce((s, n) => s + n, 0)));
    const maxUsd = Math.max(.01, ...compareRows.map(r => r.usd || 0));
    const total = compareRows.reduce((s, r) => s + (r.usd || 0), 0);
    note.textContent = compareRows.length + ' sessões · ' + usd(total) + ' no total · clique numa linha para reproduzir';
    byId('rp-compare-rows').replaceChildren(...compareRows.map(r => {
      const tr = node('tr');
      tr.dataset.id = r.id;
      const ask = node('td', null, 'rp-ask-cell'); const open = node('button', null, 'rp-row-btn'); open.type = 'button'; open.append(node('span', r.request || '—')); open.setAttribute('aria-label', 'Reproduzir a sessão de ' + ddmm(r.started) + ': ' + (r.request || 'sem pedido')); ask.append(open); ask.title = r.request || '';
      const mix = node('td', null, 'rp-mix-cell');
      const counts = Object.entries(r.agents || {}).sort((a, b) => b[1] - a[1]);
      const sum = counts.reduce((s, [, n]) => s + n, 0);
      const bar = node('span', null, 'rp-mix');
      bar.style.width = Math.max(2, sum / maxAgents * 100).toFixed(1) + '%';
      counts.forEach(([model, n]) => {const seg = node('i', null, family(model)); seg.style.flexGrow = String(n); seg.title = n + ' em ' + model; bar.append(seg);});
      const label = node('span', sum ? counts.map(([model, n]) => n + ' ' + model.replace(/\s[\d.]+$/, '')).join(' · ') : 'sem subagentes', 'rp-mix-label');
      if (r.agents_failed) label.append(node('em', ' · ' + r.agents_failed + word(r.agents_failed, ' falhou', ' falharam')));
      mix.append(sum ? bar : '', label);
      const cost = node('td', null, 'num rp-cost-cell'); const cbar = node('span', null, 'rp-cost-bar'); const fill = node('i'); fill.style.width = ((r.usd || 0) / maxUsd * 100).toFixed(1) + '%'; cbar.append(fill); cost.append(cbar, node('span', usd(r.usd)));
      const gates = node('td', null, 'rp-nowrap'); gates.append(document.createTextNode(int(r.gates) + ' '), node('span', r.gates_failed ? '· ' + r.gates_failed + (r.gates_failed === 1 ? ' vermelho' : ' vermelhos') : '· todos verdes', r.gates_failed ? 'rp-red' : 'rp-muted'));
      tr.append(node('td', ddmm(r.started), 'rp-date'), ask, node('td', span(r.duration || 0), 'rp-nowrap'), mix, cost, gates, node('td', r.commits || r.pushes ? plural(r.commits, 'commit', 'commits') + ' · ' + plural(r.pushes, 'push', 'pushes') : '—', 'rp-muted rp-nowrap'));
      labelCells(tr, COMPARE_COLS);
      return tr;
    }));
    markCompare();
  }
  function markCompare() {
    const id = data?.id;
    for (const tr of byId('rp-compare-rows').children) {if (tr.dataset.id === id) tr.setAttribute('aria-current', 'true'); else tr.removeAttribute('aria-current');}
  }
  function openSession(tr) {
    if (!tr?.dataset.id || tr.dataset.id === data?.id) return;
    byId('rp-session').value = tr.dataset.id;
    playing = !reduced.matches; syncControls(); load(tr.dataset.id);
    view.querySelector('.replay-head').scrollIntoView({behavior:reduced.matches ? 'auto' : 'smooth', block:'start'});
  }
  byId('rp-compare-rows').addEventListener('click', event => openSession(event.target.closest('tr')));
  byId('rp-compare-retry').addEventListener('click',event=>{void runAction(event.currentTarget,'Carregando comparativo…',loadCompare);});

  // ---------- conectores ----------
  function wires() {
    const map = byId('rp-map'), svg = byId('rp-wires');
    if (!map || map.offsetParent === null) return;
    const box = map.getBoundingClientRect();
    const rect = name => {const r = map.querySelector('[data-node="' + name + '"]').getBoundingClientRect(); return {l:r.left - box.left, r:r.right - box.left, t:r.top - box.top, b:r.bottom - box.top, cx:(r.left + r.right) / 2 - box.left, cy:(r.top + r.bottom) / 2 - box.top};};
    svg.setAttribute('viewBox', '0 0 ' + box.width + ' ' + box.height);
    svg.replaceChildren();
    for (const [a, b] of WIRES) {
      const A = rect(a), B = rect(b);
      let d;
      if (A.b > B.t - 4) {const y = Math.min(A.cy, B.cy); d = `M${A.r} ${y} L${B.l} ${y}`;}
      else {const x = a === 'opus' ? A.cx : B.cx; d = `M${x} ${A.b} L${x} ${B.t}`;}
      for (const cls of ['wire', 'wire-pulse']) {const p = document.createElementNS(SVG, 'path'); p.setAttribute('d', d); p.setAttribute('class', cls); p.setAttribute('pathLength', '100'); p.dataset.wire = a + '>' + b; svg.append(p);}
    }
  }
  const lastPulse = {};
  function pulse(route) {
    if (!route) return;
    const key = route.join('>'), now = performance.now();
    if (now - (lastPulse[key] || 0) < 420) return;
    lastPulse[key] = now;
    const p = byId('rp-wires').querySelector('.wire-pulse[data-wire="' + key + '"]'); if (!p) return;
    p.classList.remove('go'); void p.getBoundingClientRect(); p.classList.add('go');
  }
  new ResizeObserver(() => wires()).observe(byId('rp-map'));

  // ---------- relógio ----------
  function frame(now) {
    raf = 0;
    if (!visible()) return;
    const dt = Math.min(.25, (now - last) / 1000); last = now;
    if (playing && data && !view.classList.contains('rp-loading')) {
      T += dt * speed * duration / REPLAY_SECONDS;
      if (T >= duration) {T = duration; playing = false; syncControls();}
    }
    paint(false);
    raf = requestAnimationFrame(frame);
  }
  function visible() {return !view.hidden && !document.hidden;}
  async function start() {
    if(libraryPending)return;
    byId('rp-retry').hidden=true;view.classList.remove('rp-failed');
    loadedId = 'lista';libraryPending=true;let id;
    try{id=await loadSessions();}
    catch(err){loadedId='';libraryEmpty=false;libraryRefreshQueued=false;byId('rp-ask').textContent=err.message;byId('rp-byline').textContent='Tente novamente para carregar as sessões.';view.classList.remove('rp-empty');view.classList.add('rp-failed');byId('rp-retry').hidden=false;return;}
    finally{libraryPending=false;}
    const hasSession=Boolean(id);
    libraryEmpty=!hasSession;const retryEmpty=libraryEmpty&&libraryRefreshQueued;libraryRefreshQueued=false;
    view.classList.toggle('rp-empty',!hasSession);
    for(const button of view.querySelectorAll('.flow-controls button'))button.disabled=!hasSession;
    if(!hasSession){playing=false;byId('rp-ask').textContent='Nenhuma transcrição local do Claude Code encontrada. Comece uma análise ou confira a pasta em Configurações.';byId('rp-byline').textContent='O fluxo aparece quando há uma sessão compatível neste computador.';if(retryEmpty){loadedId='';sync();}return;}
    await load(id);loadCompare();
  }
  function sync() {
    if(!view.hidden&&!loadedId)start();
    if (!visible()) {if (raf) cancelAnimationFrame(raf); raf = 0; return;}
    wires();
    if (!raf) {last = performance.now(); raf = requestAnimationFrame(frame);}
  }

  const toggle = byId('flow-play'), restart = byId('flow-restart'), speeds = byId('flow-speed'), fullBtn = byId('flow-full');
  function syncControls() {
    toggle.querySelector('span').textContent = playing ? 'Pausar' : data && T >= duration ? 'Rever' : 'Continuar';
    toggle.querySelector('use').setAttribute('href', playing ? '#i-pause' : '#i-play');
  }
  toggle.addEventListener('click', () => {if (!playing && T >= duration) T = 0; playing = !playing; syncControls(); painted = -1; paint(true);});
  restart.addEventListener('click', () => {T = 0; playing = true; syncControls(); painted = -1; paint(true);});
  speeds.addEventListener('click', event => {const b = event.target.closest('button[data-value]'); if (!b) return; speed = Number(b.dataset.value); for (const o of speeds.querySelectorAll('button')) o.setAttribute('aria-pressed', String(o === b));});
  fullBtn.addEventListener('click', () => {if (document.fullscreenElement) document.exitFullscreen(); else view.requestFullscreen?.().catch(() => {});});
  fullBtn.hidden = !document.fullscreenEnabled;
  document.addEventListener('fullscreenchange', () => {
    const on = document.fullscreenElement === view;
    fullBtn.lastChild.textContent = on ? 'Sair da tela cheia' : 'Tela cheia';
    fullBtn.querySelector('use').setAttribute('href', on ? '#i-collapse' : '#i-expand');
    setTimeout(wires, 80);
  });
  byId('rp-session').addEventListener('change', event => {playing = !reduced.matches; syncControls(); load(event.target.value);});
  byId('rp-retry').addEventListener('click',()=>{if(loadedId)void load(byId('rp-session').value);else void start();});
  syncControls();

  descriptions.flow = ['Fluxo de trabalho', 'Reprodução das suas sessões locais do Claude Code, com modelo, atividades e custo de cada agente.'];
  new MutationObserver(sync).observe(view, {attributes:true, attributeFilter:['hidden']});
  document.addEventListener('visibilitychange', sync);
  document.addEventListener('mur:index-updated',()=>{if(libraryPending){libraryRefreshQueued=true;return;}if(libraryEmpty){loadedId='';sync();}});
  sync();
})();
