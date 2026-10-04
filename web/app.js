'use strict';
const $ = selector => document.querySelector(selector);
const number = v => new Intl.NumberFormat('pt-BR').format(v || 0);
const compact = v => new Intl.NumberFormat('pt-BR', {notation:'compact', maximumFractionDigits:2}).format(v || 0);
const money = v => v == null ? 'Não apurado' : new Intl.NumberFormat('pt-BR', {style:'currency', currency:'USD'}).format(v);
const date = (v, options={}) => v ? new Intl.DateTimeFormat('pt-BR', {timeZone:appState?.timezone, day:'2-digit', month:'2-digit', year:'numeric', ...options}).format(new Date(v*1000)) : 'Sem horário registrado';
const datetime = v => date(v, {hour:'2-digit', minute:'2-digit'});
const el = (tag, text, className) => {const node=document.createElement(tag); if(text!=null)node.textContent=text; if(className)node.className=className; return node;};
const button = (text, callback, className='quiet') => {const node=el('button',text,className);node.type='button';node.addEventListener('click',callback);return node;};
let appState, overview, currentView='overview', csrf='', offset=0, detailOffset=0, sequence=0, lastScan=null, lastMessages=0, viewController, refreshQueued,viewRefreshPending=false,filtersPending=false,viewRetryNeeded=false;
let params={period:'7d',provider:'',model:'',project:'',machine:'',q:''};
try {params={...params,...JSON.parse(localStorage.getItem('agentes.filters')||'{}')}; } catch {}
const incoming=new URLSearchParams(location.search);
for(const key of ['period','machine','provider','model','project','q'])if(incoming.has(key))params[key]=incoming.get(key);
const machineName=value=>appState?.machines?.find(m=>m.id===value)?.label||value;
const PROVIDERS=['Anthropic','OpenAI','xAI'];
const providerOrder=label=>{const i=PROVIDERS.indexOf(label);return i<0?PROVIDERS.length:i;};
const dot=provider=>{const node=el('i',null,'dot');node.dataset.provider=provider||'';node.setAttribute('aria-hidden','true');return node;};
const percent=v=>(v*100).toLocaleString('pt-BR',{maximumFractionDigits:v<.01?1:0})+'%';
let chartMetric='tokens';
const form=$('#filters');
for(const [key,value] of Object.entries(params))if(form.elements[key])form.elements[key].value=value;
function error(message,retry=viewRetryNeeded){$('#error-message').textContent=message;$('#error').hidden=!message;$('#retry-view').hidden=!message||!retry;}
const loadingJobs=new Map(),loadingTargets=new Set();
function paintLoading(){
  const jobs=[...loadingJobs.values()],active=jobs.filter(job=>!job.silent).at(-1);
  $('#request-status').toggleAttribute('data-idle',!active);
  $('#request-label').textContent=active?.label||'';
  for(const target of loadingTargets){
    const busy=jobs.some(job=>job.targets.includes(target));
    target.classList.toggle('is-pending',busy);target.setAttribute('aria-busy',String(busy));target.inert=busy||target.classList.contains('is-stale');
  }
}
function startLoading(key,label,targets=[],silent=false){
  const job={label,targets,silent};loadingJobs.set(key,job);targets.forEach(target=>loadingTargets.add(target));paintLoading();
  return ()=>{if(loadingJobs.get(key)===job){loadingJobs.delete(key);paintLoading();}};
}
async function runAction(control,label,action){
  if(control.disabled)return;
  const text=control.querySelector('span')||control,original=text.textContent,done=startLoading(control,label);
  control.disabled=true;control.classList.add('is-working');control.setAttribute('aria-busy','true');text.textContent=label;
  try{return await action();}
  finally{done();control.disabled=control.id==='export'&&['overview','sessions','projects'].includes(currentView)&&(!appState||filtersPending||loadingJobs.has('view')||document.getElementById(currentView).classList.contains('is-stale'));control.classList.remove('is-working');control.removeAttribute('aria-busy');text.textContent=original;}
}
async function api(path, data,signal){
  const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':csrf},body:JSON.stringify(data)};
  const controller=new AbortController(),abort=()=>controller.abort();let timedOut=false;
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},120000);
  if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  try{
    const response=await fetch(path,{...options,signal:controller.signal});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'Não foi possível consultar o painel.');
    return result;
  }catch(e){if(timedOut)throw new Error('A consulta demorou mais que o esperado. Tente novamente.');throw e;}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
function query(extra={}){return new URLSearchParams({...params,...extra}).toString();}
function saveFilters(){try{localStorage.setItem('agentes.filters',JSON.stringify(params));}catch{}}
function empty(parent,text){parent.replaceChildren(el('div',text,'empty'));}
function sessionLink(id){detailOffset=0;location.hash='session='+encodeURIComponent(id);}
function applyProject(project){params.project=project;form.elements.project.value=project;saveFilters();offset=0;location.hash='sessions';loadView();}
function options(select, values){const value=params[select.name]||'';select.replaceChildren(new Option('Todos',''),...values.map(v=>new Option(v,v)));if(value&&!values.includes(value))select.add(new Option(value,value));select.value=value;}

async function loadState(){
  while(loadState.running)await loadState.running;
  let release;loadState.running=new Promise(resolve=>{release=resolve;});const hadState=Boolean(appState);
  const done=!appState?startLoading('startup','Abrindo o painel…'):()=>{};
  try{
    appState=await api('/api/state');csrf=appState.csrf;
    const p=appState.progress,m=appState.metadata;
    $('#machine').textContent=appState.machines.map(m=>m.label).join(' + ');
    const machines=form.elements.machine;
    machines.replaceChildren(new Option('Todos os computadores',''),...appState.machines.map(m=>new Option(m.label,m.id)));
    if(params.machine&&!appState.machines.some(m=>m.id===params.machine))params.machine='';
    machines.value=params.machine;
    for(const [period,available] of [['combined',m.combined_window],['audit',m.audit_window]]){
      form.elements.period.querySelector('[value="'+period+'"]').hidden=!available;
      if(params.period===period&&!available)params.period='7d';
    }
    syncForm();
    $('#saved-report').hidden=!appState.hasReport;
    $('#welcome').hidden=appState.settings.setupComplete||currentView!=='overview';
    const detected=Object.entries(appState.detectedSources).filter(([,exists])=>exists).map(([name])=>({codex:'Codex',claude:'Claude Code',grok:'Grok'}[name]));
    $('#detected-sources').textContent=detected.length?'Pastas encontradas: '+detected.join(', ')+'.':'Nenhuma pasta de registros encontrada. Você pode configurar as fontes ou importar a coleta de outro Mac.';
    renderAccounts();
    renderUpdates();
    renderLimits();
    $('#footer-timezone').textContent='Histórico local · '+appState.timezone;
    $('#last-sync').textContent=p.running?p.phase:m.last_scan?'Atualizado em '+datetime(m.last_scan):'Aguardando a primeira indexação';
    if(refreshQueued){refreshQueued.started||=p.running;if(m.last_scan!==refreshQueued.baseline||p.error||(refreshQueued.started&&!p.running))refreshQueued=null;}
    const indexing=p.running||Boolean(refreshQueued);
    $('#index-progress').hidden=!indexing;
    $('#index-label').textContent=p.running?(m.initial_scan_complete?'Atualizando registros':'Primeira leitura: números parciais até concluir')+' · '+number(p.processed)+' de '+number(p.total)+' arquivos':'Preparando a leitura dos registros…';
    $('#index-meter').value=p.total?p.processed/p.total*100:0;
    $('#refresh').disabled=indexing;
    $('#refresh').classList.toggle('is-working',indexing);$('#refresh').setAttribute('aria-busy',String(indexing));
    $('#refresh span').textContent=p.running?'Atualizando…':refreshQueued?'Preparando…':'Atualizar registros';
    $('#start-analysis').disabled=indexing;$('#start-analysis').classList.toggle('is-working',indexing);
    $('#footer-status').textContent=number(appState.counts.sessions)+' sessões catalogadas · '+number(appState.counts.files)+' arquivos';
    if(p.error)error('A atualização encontrou um problema: '+p.error+'. Os dados já indexados continuam disponíveis.');
    if(lastScan!==m.last_scan||(!m.initial_scan_complete&&lastMessages!==appState.counts.events)||(!hadState&&loadView.shown)){
      lastScan=m.last_scan;lastMessages=appState.counts.events;
      document.dispatchEvent(new Event('mur:index-updated'));
      if((hadState||loadView.shown)&&currentView!=='settings'&&currentView!=='detail'){
        if(loadingJobs.has('view'))viewRefreshPending=true;else void loadView({silent:true});
      }
    }
  }catch(e){error('Não foi possível conectar ao serviço local. Abra “MUR” novamente. '+e.message);}
  finally{loadState.running=null;release();done();}
}

function rankList(target, rows, onClick, {limit=8, provider=false, measure='tokens'}={}){
  target.replaceChildren();
  if(!rows.length)return empty(target,'Nenhum consumo encontrado neste recorte.');
  const max=Math.max(...rows.map(r=>r[measure]||0),1);
  rows.slice(0,limit).forEach(r=>{
    const line=el('div',null,'rank-row'),name=button('',()=>onClick(r.label),'');
    if(provider)name.append(dot(r.label));
    name.append(document.createTextNode(r.label||'Não identificado'));
    line.append(name,el('strong',compact(r.tokens)));
    line.append(el('small',number(r.sessions)+(r.sessions===1?' sessão':' sessões')),el('small',money(r.usd),'right'));
    const bar=el('div',null,'rank-bar'),fill=el('i');fill.style.width=Math.max(.5,(r[measure]||0)/max*100)+'%';if(provider)fill.dataset.provider=r.label;bar.append(fill);line.append(bar);
    target.append(line);
  });
}

function legend(target, rows, total){
  target.replaceChildren(...rows.map(r=>{const item=el('span');item.append(dot(r.label),document.createTextNode(r.label));if(total!=null)item.append(el('b',percent((r.tokens||0)/(total||1))));return item;}));
}

function renderShare(data){
  const rows=[...data.providers].sort((a,b)=>providerOrder(a.label)-providerOrder(b.label)),total=data.totals.tokens||0,bar=$('#provider-share');
  legend($('#share-legend'),rows,total);
  bar.replaceChildren(...rows.filter(r=>r.tokens>0).map(r=>{const seg=el('span');seg.dataset.provider=r.label;seg.style.flex=String(r.tokens);seg.title=r.label+' · '+percent(r.tokens/(total||1))+' · '+number(r.tokens)+' tokens';return seg;}));
  bar.setAttribute('aria-label','Participação nos tokens: '+rows.map(r=>r.label+' '+percent(r.tokens/(total||1))).join(', '));
}

function niceMax(v){const step=10**Math.floor(Math.log10(v||1));for(const m of [1,1.5,2,2.5,3,4,5,6,8,10])if(m*step>=v)return m*step;return 10*step;}

function renderChart(){
  const target=$('#daily-chart'),tip=$('#chart-tip'),metric=chartMetric,rows=overview?.days||[];
  const chartKey=metric+'|'+query();target.classList.toggle('settled',target.dataset.key===chartKey);target.dataset.key=chartKey;
  target.replaceChildren();tip.hidden=true;
  const providers=[...new Set(rows.flatMap(r=>Object.keys(r.byProvider||{})))].sort((a,b)=>providerOrder(a)-providerOrder(b));
  legend($('#chart-legend'),providers.map(label=>({label})));
  if(!rows.length)return empty(target,'O gráfico aparecerá quando houver registros no período.');
  const format=v=>metric==='usd'?(v==null?'—':money(v)):number(v),short=v=>metric==='usd'?(v==null?'—':'US$ '+compact(v)):compact(v);
  const values=rows.map(r=>r[metric]||0),top=niceMax(Math.max(...values,1)),plot=parseFloat(getComputedStyle(target).getPropertyValue('--plot'))||240;
  for(const f of [0,.25,.5,.75,1]){const line=el('div',null,'grid-line'+(f===0?' base':''));line.style.top=(16+plot-f*plot)+'px';line.append(el('span',f===0?'0':short(top*f)));target.append(line);}
  const peak=values.indexOf(Math.max(...values)),every=Math.ceil(rows.length/(target.clientWidth<600?8:16));
  rows.forEach((r,i)=>{
    const v=r[metric],node=button('',()=>{params.period='custom';params.start=r.label;params.end=r.label;syncForm();saveFilters();offset=0;loadView();},'day-column');
    const day=r.label.slice(8)+'/'+r.label.slice(5,7);
    node.setAttribute('aria-label',day+' · '+format(v)+'. Filtrar este dia.');
    const height=Math.max(2,(v||0)/top*plot),bar=el('span',null,'day-fill');bar.style.height=height+'px';bar.style.setProperty('--i',i);
    for(const p of providers){const part=r.byProvider?.[p]?.[metric];if(!part)continue;const seg=el('span',null,'seg');seg.dataset.provider=p;seg.style.flex=part+' 1 0';bar.append(seg);}
    node.append(bar,el('span',i%every===0||i===rows.length-1&&rows.length<=16?day:'','day-label'));
    if(i===peak&&v){const label=el('span',short(v),'day-value');label.style.bottom=(height+38)+'px';node.append(label);}
    const show=()=>{
      tip.replaceChildren(el('strong',new Intl.DateTimeFormat('pt-BR',{weekday:'long',day:'numeric',month:'long',timeZone:'UTC'}).format(new Date(r.label+'T12:00:00Z'))));
      for(const p of providers){const part=r.byProvider?.[p];if(!part)continue;const row=el('div',null,'row');row.append(dot(p),el('span',p),el('b',format(part[metric])));tip.append(row);}
      const total=el('div',null,'row total');total.append(el('span'),el('span','Total'),el('b',format(v)));tip.append(total,el('small',number(r.sessions)+' sessões · clique para filtrar o dia'));
      const box=node.getBoundingClientRect(),panel=target.parentElement.getBoundingClientRect();
      tip.hidden=false;tip.style.left=Math.min(Math.max(box.left-panel.left+box.width/2,120),panel.width-120)+'px';tip.style.top=(box.bottom-panel.top-30-height)+'px';
    };
    node.addEventListener('pointerenter',show);node.addEventListener('focus',show);
    node.addEventListener('pointerleave',()=>{tip.hidden=true;});node.addEventListener('blur',()=>{tip.hidden=true;});
    target.append(node);
  });
}

function renderOverview(data){
  const t=data.totals;
  $('#metric-usd').textContent=money(t.usd);
  $('#metric-tokens').textContent=compact(t.tokens);
  $('#metric-tokens').title=number(t.tokens)+' tokens';
  $('#metric-cache').textContent=(t.tokens?(t.cache/t.tokens*100).toLocaleString('pt-BR',{maximumFractionDigits:1}):'0')+'% de leitura de cache · '+number(t.tokens)+' tokens';
  $('#metric-sessions').textContent=number(t.sessions);
  $('#metric-events').textContent=number(t.events)+' registros de uso · '+number(t.calls)+' chamadas registradas';
  const pending=t.subscriptionsPending||0;
  $('#metric-subscription').textContent=!t.billingReviewed?'Não informado':pending&&!t.subscriptionsKnown?'A confirmar':money(t.allocated);
  $('#metric-monthly').textContent=t.billingReviewed?(pending?'Custo parcial: '+pending+' período(s) com datas ou valores a confirmar. ':'')+(t.monthlyPending?'Renovações atuais: subtotal de '+money(t.monthly)+' por mês; há dados a confirmar.':'Renovações atuais: '+money(t.monthly)+' por mês.'):'Opcional: informe valores e datas em Contas e assinaturas.';
  $('#metric-paid').textContent=t.paymentsCount?'Pagamentos cadastrados no período: '+money(t.paidRecorded)+' · '+t.paymentsCount+' lançamento(s), por data.':'Pagamentos no período: não cadastrados.';
  $('#machine-panel').hidden=!appState?.imports?.length;
  $('#machine-list').replaceChildren(table(['Computador','Tokens','Sessões','Custo técnico'],(data.machines||[]).map(r=>[
    button(machineName(r.label),()=>{params.machine=r.label;syncForm();saveFilters();offset=0;loadView();},'project-button'),number(r.tokens),number(r.sessions),money(r.usd)
  ])));
  renderShare(data);
  rankList($('#providers'),[...data.providers].sort((a,b)=>providerOrder(a.label)-providerOrder(b.label)),provider=>{params.provider=provider;syncForm();saveFilters();loadView();},{provider:true});
  rankList($('#top-models'),data.models,model=>{params.model=model;syncForm();saveFilters();loadView();},{limit:6});
  rankList($('#top-projects'),data.projects,applyProject,{limit:6});
  renderChart();
  const coverage=$('#coverage-content');coverage.replaceChildren();
  const paragraphs=[
    number(t.unpriced_tokens)+' tokens sem preço apurado neste recorte. Falta de tarifa ou de registro não significa custo zero.',
    number(appState?.counts.skipped_usage)+' registros com categorias inconsistentes ou sem uma chamada datada foram identificados no índice. Esses registros não entram nos totais.',
    'Tabela de tarifas com referência em '+(appState?.settings.ratesDate||'data não informada')+'. Grok usa custo registrado pelo CLI. Contextos OpenAI acima de 272 mil tokens ficam sem estimativa quando não há regra de tarifa específica.',
    'Tokens incluem entrada, leitura e gravação de cache e saída. Raciocínio é parte da saída e não é somado duas vezes. Contadores Codex são reconciliados entre arquivos; Claude usa IDs de mensagem; Grok usa turnos deduplicados.',
    'Este computador é atualizado pelos registros locais. Outros Macs entram pelas coletas importadas em Configurações. Conversas exclusivamente na web e despesas de mídia sem recibo não entram automaticamente.',
    number(data.duplicateEvents||0)+' eventos encontrados em mais de um computador são contados uma vez, com preferência pelo registro local. Sessões compartilhadas podem aparecer em mais de uma origem, mas contam uma vez no total. As mensalidades são globais e não são duplicadas.',
    'Os logs originais são somente leitura. Mensagens textuais são consultáveis; ferramentas, raciocínio interno, anexos e mensagens maiores que 4 MB não são exibidos. Recibos efêmeros preservados na auditoria permanecem identificados pela sua origem.'
  ];
  for(const text of paragraphs)coverage.append(el('p',text));
  const issues=appState?.metadata.source_issues||[];
  if(issues.length)coverage.append(el('p',number(issues.length)+' arquivos não puderam ser lidos; consulte o estado das fontes em Configurações.'));
}

function table(headers,rows){
  const wrap=el('div',null,'table-scroll'),t=el('table'),thead=el('thead'),head=el('tr'),body=el('tbody');
  headers.forEach((h,i)=>head.append(el('th',h,i>1?'numeric':'')));thead.append(head);t.append(thead,body);wrap.append(t);
  rows.forEach(row=>{const tr=el('tr');row.forEach((value,i)=>{const cell=el('td',null,i>1?'numeric':'');cell.append(value instanceof Node?value:document.createTextNode(value));tr.append(cell);});body.append(tr);});
  return wrap;
}

async function renderSessions(version,signal){
  const data=await api('/api/sessions?'+query({offset,sort:$('#session-sort').value}),undefined,signal);
  if(version!==sequence)return;
  $('#session-count').textContent=number(data.total)+' conversas com consumo';
  const target=$('#session-list');
  if(!data.rows.length)empty(target,'Nenhuma conversa com consumo corresponde aos filtros. Tente outro período ou limpe a busca.');
  else target.replaceChildren(table(['Conversa','Projeto','Tokens','Custo técnico','Último uso'],data.rows.map(r=>{
    const name=el('div');name.append(button(r.title||r.external_id,()=>sessionLink(r.id),'session-title'),(()=>{const meta=el('span',null,'cell-secondary');meta.append(dot(r.provider),document.createTextNode(r.provider+' · '+r.channel));return meta;})());
    const project=el('div',r.project);project.append(el('span',(r.models||'').split(',').join(' · '),'cell-secondary model-ids'),el('span',(r.machines||'local').split(',').map(machineName).join(' + '),'cell-secondary'));
    return [name,project,compact(r.tokens),money(r.usd),datetime(r.last_ts)];
  })));
  $('#page-number').textContent=data.total?(offset+1)+'–'+Math.min(offset+data.limit,data.total)+' de '+number(data.total):'0 resultados';
  $('#previous').disabled=offset===0;$('#next').disabled=offset+data.limit>=data.total;
}

function renderProjects(data){
  const target=$('#project-list');
  if(!data.projects.length)return empty(target,'Nenhum projeto com consumo neste período.');
  target.replaceChildren(table(['Projeto','Participação','Sessões','Tokens','Custo técnico'],data.projects.map(r=>[
    button(r.label,()=>applyProject(r.label),'project-button'),(()=>{const share=r.tokens/(data.totals.tokens||1),cell=el('span',null,'share-cell'),track=el('i'),fill=el('b');fill.style.width=Math.max(1,share*100)+'%';track.append(fill);cell.append(track,document.createTextNode((share*100).toLocaleString('pt-BR',{maximumFractionDigits:1})+'%'));return cell;})(),number(r.sessions),compact(r.tokens),money(r.usd)
  ])));
}

const statusLabels={running:'Processo ativo',log_running:'Sinal de execução',unconfirmed:'Sem confirmação',recent:'Atividade recente',completed:'Concluído',succeeded:'Concluído',failed:'Falhou',cancelled:'Cancelado',unknown:'Estado desconhecido'};
async function renderLive(version=sequence,signal){
  const data=await api('/api/live',undefined,signal);
  if(version!==sequence||currentView!=='live')return;
  $('#live-count').textContent=data.rows.filter(r=>['running','log_running'].includes(r.state)).length||'';
  const target=$('#live-list');target.replaceChildren();
  if(!data.rows.length)return empty(target,'Nenhum sinal recente de execução encontrado. O histórico permanece disponível em Conversas.');
  for(const r of data.rows){
    const line=el('article',null,'agent-row');
    const title=r.sid?button(r.title||r.external_id||r.id,()=>sessionLink(r.sid),'session-title'):el('h3',r.title||r.id);
    line.append(title,el('span',statusLabels[r.state]||r.state,'badge '+r.state));
    line.append(el('p',r.provider+' · '+(r.model||'modelo não registrado')+' · '+(r.project||'projeto não identificado')+' · '+datetime(r.last_ts),'agent-meta'));
    line.append(el('p',r.evidence,'agent-meta evidence'));target.append(line);
  }
}

async function renderUpdates(action='status',enabled){
  $('#app-version').textContent=appState?.version||'';
  const bridge=window.webkit?.messageHandlers?.murUpdates;
  if(!bridge)return;
  try{
    const state=await bridge.postMessage({action,...(typeof enabled==='boolean'?{enabled}:{})});
    $('#app-version').textContent=state.version;
    $('#update-status').textContent=state.message;
    $('#check-updates').disabled=!state.configured||!state.canCheck;
    $('#automatic-updates').disabled=!state.configured;
    $('#automatic-updates').checked=state.automatic;
    $('#update-last-check').textContent=state.lastCheck?'Última verificação: '+datetime(state.lastCheck):'';
  }catch{
    $('#update-status').textContent='Não foi possível consultar as atualizações. Reabra o MUR e tente novamente.';
  }
}
$('#check-updates').addEventListener('click',()=>renderUpdates('check'));
$('#automatic-updates').addEventListener('change',event=>renderUpdates('automatic',event.target.checked));

const percentage=value=>new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(value)+'%';
let limitsSignature='';
function renderLimits(){
  if(!appState)return;
  const state=appState.limits||{enabled:false,rows:[]};
  $('#live-limits').checked=state.enabled;
  const consulting=state.refreshing||loadingJobs.has($('#refresh-limits'));
  $('#refresh-limits').disabled=!state.enabled||consulting;
  $('#refresh-limits').classList.toggle('is-working',consulting);$('#refresh-limits').setAttribute('aria-busy',String(consulting));
  $('#refresh-limits').textContent=consulting?'Consultando…':'Atualizar saldos';
  const available=state.enabled?state.rows.flatMap(row=>row.status==='ready'&&!row.stale?row.windows.map(w=>w.remainingPercent):[]):[];
  $('#limit-badge').textContent=available.length?percentage(Math.min(...available)):'';
  const accounts=(appState.accounts||[]).filter(account=>account.present);
  const signature=JSON.stringify([state,accounts]);if(signature===limitsSignature)return;limitsSignature=signature;
  const target=$('#limit-cards');target.replaceChildren();
  if(!state.enabled){empty(target,'Ative a consulta acima para ver o saldo informado pelos serviços.');return;}
  if(!accounts.length){empty(target,'Nenhuma conta conectada foi identificada. Entre no Codex, Claude Code ou Grok neste Mac.');return;}
  for(const account of accounts){
    const row=state.rows.find(r=>r.accountId===account.id);
    const card=el('article',null,'panel limit-card');card.dataset.provider=account.provider;
    const header=el('div',null,'section-heading'),title=el('h2');title.append(dot(account.provider),({OpenAI:'Codex',Anthropic:'Claude',xAI:'Grok'}[account.provider]));header.append(title);card.append(header,el('p',account.label+(account.plan?' · '+account.plan:''),'muted'));
    if(!row){card.append(el('p','Aguardando consulta…','muted'));target.append(card);continue;}
    if(row.stale)card.append(el('p','Último saldo conhecido · desatualizado','limit-warning'));
    for(const window of row.windows){
      const block=el('div',null,'limit-window'),head=el('div',null,'limit-window-head');
      head.append(el('span',window.label),el('strong',percentage(window.remainingPercent)+' restante'));
      const meter=el('progress');meter.max=100;meter.value=window.remainingPercent;meter.setAttribute('aria-label',window.label+': '+percentage(window.remainingPercent)+' restante');
      if(window.remainingPercent<=10)block.classList.add('limit-low');
      block.append(head,meter,el('p',window.resetsAt?'Renova em '+datetime(window.resetsAt):'O provedor não informou a próxima renovação.','muted'));card.append(block);
    }
    for(const credit of row.credits)card.append(el('p',credit.label+': '+number(credit.balance)+' créditos','limit-credit'));
    if(row.status!=='ready')card.append(el('p',row.message,'explanation'));
    if(account.provider==='Anthropic'&&row.status==='connectionRequired')card.append(button('Conectar Claude',async event=>{
      $('#limits-result').textContent='Conexão opcional para consultar o saldo do Claude. O macOS pode pedir autorização; você pode negar e continuar usando o MUR.';
      try{await runAction(event.currentTarget,'Conectando Claude…',async()=>{await api('/api/limits/refresh',{connectClaude:true});await loadState();});}catch(e){error(e.message);}
    }));
    card.append(el('small',row.lastSuccess?'Saldo consultado em '+datetime(row.lastSuccess):'Saldo ainda não disponível','muted'));target.append(card);
  }
}
$('#live-limits').addEventListener('change',async event=>{
  try{await runAction(event.target,'Salvando preferência…',async()=>{await api('/api/settings',{liveLimitsEnabled:event.target.checked});await loadState();});}
  catch(e){error(e.message);await loadState();}
});
$('#refresh-limits').addEventListener('click',async()=>{
  try{await runAction($('#refresh-limits'),'Solicitando saldos…',async()=>{await api('/api/limits/refresh',{});$('#limits-result').textContent='Consulta solicitada. O MUR respeita o intervalo de cinco minutos entre consultas ao mesmo provedor.';await loadState();});renderLimits();}
  catch(e){error(e.message);}
});
async function renderMenuPreference(action='status',enabled){
  const bridge=window.webkit?.messageHandlers?.murMenu;if(!bridge)return;
  try{const state=await bridge.postMessage({action,...(typeof enabled==='boolean'?{enabled}:{})});$('#menu-bar-setting').hidden=false;$('#menu-bar').checked=state.enabled;}catch(e){error(e.message);}
}
$('#menu-bar').addEventListener('change',event=>renderMenuPreference('enabled',event.target.checked));
renderMenuPreference();

let accountsSignature='';
function renderAccounts(){
  const rows=appState?.accounts||[];
  const signature=JSON.stringify(rows);if(signature===accountsSignature)return;accountsSignature=signature;
  for(const target of [$('#welcome-accounts'),$('#detected-accounts')]){
    target.replaceChildren();
    const shown=target.id==='welcome-accounts'?rows.filter(r=>r.present):rows;
    if(!shown.length){target.append(el('p','Não foi possível identificar contas nestes arquivos. Você pode cadastrar as assinaturas manualmente.','muted'));continue;}
    for(const row of shown){
      const card=el('article',null,'account-card');
      card.append(el('strong',row.provider+' · '+row.label),el('span',row.plan?'Plano detectado: '+row.plan:'Plano não disponível nos arquivos locais','cell-secondary'),el('small',row.present?'Presente nos arquivos locais':'Observada anteriormente · '+datetime(row.lastSeen),'muted'));
      if(target.id==='detected-accounts')card.append(button('Usar esta conta',()=>{
        const existing=[...$('#subscription-rows').children].reverse().find(n=>n.querySelector('[data-field=accountId]').value===row.id);
        if(existing){existing.scrollIntoView({block:'center'});existing.querySelector('[data-field=monthlyUsd]').focus();return;}
        const field=addSubscriptionRow({provider:row.provider,label:row.label,accountId:row.id,monthlyUsd:null,quantity:1});
        field.scrollIntoView({block:'center'});field.querySelector('[data-field=monthlyUsd]').focus();
      }));
      target.append(card);
    }
  }
}

function addSubscriptionRow(subscription){
  const row=el('fieldset',null,'subscription-row');row.dataset.id=subscription.id||crypto.randomUUID();
  row.append(el('legend','Período de assinatura'));
  function field(label,name,input){const wrapper=el('label',label);input.dataset.field=name;wrapper.append(input);row.append(wrapper);return input;}
  const provider=field('Provedor','provider',el('select'));
  provider.append(...['OpenAI','Anthropic','xAI'].map(value=>new Option(value==='Anthropic'?'Claude':value==='xAI'?'Grok':value,value)));provider.value=subscription.provider;
  const name=field('Nome para reconhecer esta assinatura','label',el('input'));name.value=subscription.label||'';name.required=true;name.maxLength=120;name.placeholder='Ex.: conta pessoal';
  const account=field('Conta vinculada (opcional)','accountId',el('select'));
  function accountOptions(){const selected=account.value||subscription.accountId||'';account.replaceChildren(new Option('Sem vínculo · preenchimento manual',''),...(appState?.accounts||[]).filter(a=>a.provider===provider.value).map(a=>new Option(a.label+(a.plan?' · '+a.plan:''),a.id)));account.value=selected;if(account.selectedIndex<0)account.value='';}
  accountOptions();
  const amount=field('Valor mensal por assinatura · US$','monthlyUsd',el('input'));amount.type='number';amount.min='0';amount.max='1000000';amount.step='.01';amount.placeholder='Confirmar depois';amount.value=subscription.monthlyUsd??'';
  const quantity=field('Quantidade','quantity',el('input'));quantity.type='number';quantity.min='1';quantity.max='100';quantity.step='1';quantity.required=true;quantity.value=subscription.quantity||1;
  function linked(){quantity.disabled=Boolean(account.value);if(account.value)quantity.value=1;}
  linked();account.addEventListener('change',linked);provider.addEventListener('change',()=>{account.value='';subscription.accountId='';accountOptions();linked();});
  const renews=field('Esta assinatura ainda terá renovações','renews',el('input'));renews.type='checkbox';renews.checked=subscription.renews!==false;
  for(const [label,key] of [['Preço válido a partir de','startDate'],['Primeiro dia sem este preço','endDate'],['Renovação encerrada ou prevista para','cancelDate']]){
    const input=field(label,key,el('input'));input.type='date';input.value=subscription[key]||'';
  }
  row.append(el('p','Início em branco: histórico a confirmar. Fim em branco: período aberto. Se cancelou, confirme até quando o valor pago cobre o acesso.','muted'));
  const payments=el('details');payments.append(el('summary','Pagamentos efetuados · '+(subscription.payments||[]).length));
  const paymentRows=el('div',null,'payment-rows');payments.append(paymentRows);
  for(const payment of subscription.payments||[])addPaymentRow(paymentRows,payment);
  payments.append(button('Registrar pagamento',()=>{addPaymentRow(paymentRows,{});payments.open=true;}));row.append(payments);
  row.append(button('Adicionar próximo período de preço',()=>{
    const next=addSubscriptionRow({provider:provider.value,label:(name.value+' · novo preço').slice(0,120),accountId:account.value,monthlyUsd:null,quantity:Number(quantity.value),startDate:row.querySelector('[data-field=endDate]').value});
    next.scrollIntoView({block:'center'});next.querySelector('[data-field=startDate]').focus();
  }));
  row.append(button('Remover da lista',()=>row.remove()));$('#subscription-rows').append(row);return row;
}

function addPaymentRow(container,payment){
  const row=el('fieldset',null,'payment-row');row.dataset.paymentId=payment.id||crypto.randomUUID();row.append(el('legend','Pagamento'));
  for(const [title,key,type] of [['Data do pagamento','date','date'],['Valor total pago · US$','amountUsd','number']]){
    const label=el('label',title),input=el('input');input.dataset.paymentField=key;input.type=type;input.value=payment[key]??'';input.required=true;
    if(type==='number'){input.min='0';input.max='10000000';input.step='.01';}label.append(input);row.append(label);
  }
  row.append(el('p','Total da cobrança, já incluindo todas as contas desta linha.','muted'),button('Remover pagamento',()=>row.remove()));container.append(row);
}

function renderSettings(){
  if(!appState)return;
  const settings=appState.settings,f=$('#settings-form');
  $('#subscription-rows').replaceChildren();
  for(const subscription of settings.subscriptions||[])addSubscriptionRow(subscription);
  $('#save-subscriptions').textContent=settings.setupComplete?'Salvar histórico':'Salvar e começar análise';
  f.elements.refreshSeconds.value=settings.refreshSeconds;
  const sourceForm=$('#sources-form');
  sourceForm.elements.machineLabel.value=settings.machineLabel;
  sourceForm.elements.timezone.value=settings.timezone;
  for(const [name,path] of Object.entries(settings.sources))sourceForm.elements[name].value=path;
  const sources=$('#sources');sources.replaceChildren();
  for(const [name,path] of Object.entries(settings.sources)){sources.append(el('strong',name==='codex'?'Codex':name==='claude'?'Claude Code':'Grok'),el('span',path,'source-path'));}
  for(const m of appState.imports||[])sources.append(el('strong',m.label+' · coleta pontual'),el('span',datetime(m.start)+' → '+datetime(m.end)+' · '+number(m.events)+' eventos · '+number(m.sessions)+' sessões','source-path'));
  const issues=appState.metadata.source_issues||[];
  for(const issue of issues)sources.append(el('p',issue.source+': '+issue.error,'source-path'));
  if(appState.metadata.catalog_error)sources.append(el('p','Catálogo Codex: '+appState.metadata.catalog_error,'source-path'));
  $('#database-count').textContent=number(appState.counts.events)+' eventos · '+number(appState.counts.messages)+' mensagens · '+number(appState.counts.files)+' arquivos indexados';
  $('#rate-note').textContent='Referência importada: '+settings.ratesDate+'. USD por milhão de tokens. Não há consulta automática de preços nem comprovação de cobrança. Modelos ausentes permanecem sem estimativa.';
  $('#rates').replaceChildren(table(['Modelo','Fonte','Entrada','Cache','Saída'],settings.rates.map(r=>{
    const a=el('a','Referência');a.href=r.url;a.target='_blank';a.rel='noopener noreferrer';
    return [r.model,a,money(r.input),money(r.cache),money(r.output)];
  })));
}

async function renderDetail(id,version,signal){
  const data=await api('/api/session?'+new URLSearchParams({id,offset:detailOffset}),undefined,signal);
  if(version!==sequence)return;
  const target=$('#detail'),s=data.session;target.replaceChildren();target.dataset.provider=s.provider;
  const back=button('',()=>{location.hash='sessions';});back.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-back"/></svg>';back.append('Voltar às conversas');
  const top=el('div',null,'detail-header');top.append(back);
    if(s.provider==='OpenAI'&&(s.machines||['local']).includes('local')&&/^[a-f0-9-]{36}$/i.test(s.external_id)){const a=el('a','Abrir no Codex');a.href='codex://threads/'+s.external_id;top.append(a);}
  target.append(top,el('h2',s.title||s.external_id,'detail-title'),el('p',s.provider+' · '+s.channel+' · '+s.external_id,'detail-path'));
  const stats=el('div',null,'detail-stats');
  for(const [value,label] of [[compact(data.stats.tokens),'tokens registrados'],[money(data.stats.usd),'custo técnico estimado'],[number(data.messageCount),'mensagens textuais']]){const item=el('div');item.append(el('strong',value),el('p',label,'muted'));stats.append(item);}
  target.append(stats,el('p','Totais desta conversa nos dados disponíveis · '+(s.machines||['local']).map(machineName).join(' + ')+'. '+datetime(data.stats.first)+' → '+datetime(data.stats.last),'muted'));
  const f=el('form',null,'project-form'),label=el('label','Projeto desta conversa'),input=el('input');input.value=s.project;input.required=true;input.maxLength=160;label.append(input);const submit=el('button','Salvar projeto');submit.type='submit';f.append(label,submit);
  f.addEventListener('submit',async event=>{event.preventDefault();try{await runAction(submit,'Salvando projeto…',()=>api('/api/project',{id:s.id,project:input.value}));submit.textContent='Projeto salvo';}catch(e){error(e.message);}});
  target.append(f,el('p',s.basis+' Confiança: '+(s.confidence||'não registrada')+'.','explanation'));
  if(data.children.length){const child=el('div',null,'subagents');child.append(el('h3','Subagentes vinculados'));data.children.forEach(r=>child.append(button(r.title||r.id,()=>sessionLink(r.id))));target.append(child);}
  const source=el('details',null,'coverage');source.append(el('summary','Origem e modelos'),el('p',s.cwd,'detail-path'),el('p',s.source,'detail-path'));for(const m of data.models)source.append(el('p',m.model+' · '+number(m.tokens)+' tokens · '+money(m.usd)));target.append(source);
  const nav=el('div',null,'message-nav');const count=el('h2','Mensagens');nav.append(count,button('Mais recentes',()=>{detailOffset=Math.max(0,data.messageCount-60);loadView();}));target.append(nav);
  if(!data.messages.length)target.append(el('div',(s.machines||[]).some(m=>m!=='local')?'A coleta importada traz identificação e consumo das conversas. As mensagens textuais permaneceram no computador de origem.':'Nenhuma mensagem textual foi encontrada. Esta sessão pode conter somente recibos, ferramentas ou registros que ainda serão indexados.','empty'));
  const transcript=el('div',null,'transcript');
  for(const m of data.messages){const article=el('article',null,'message '+m.role),head=el('header');head.append(el('strong',m.role==='user'?'Você':'Agente'),el('span',datetime(m.ts),'muted'));article.append(head,el('div',m.text,'body'));transcript.append(article);}
  if(data.messages.length)target.append(transcript);
  const pages=el('div',null,'message-nav'),prev=button('Mensagens anteriores',()=>{detailOffset=Math.max(0,detailOffset-60);loadView();}),next=button('Próximas mensagens',()=>{detailOffset+=60;loadView();});prev.disabled=detailOffset===0;next.disabled=detailOffset+60>=data.messageCount;pages.append(prev,el('span',data.messageCount?`${detailOffset+1}–${Math.min(detailOffset+60,data.messageCount)} de ${number(data.messageCount)}`:'0 mensagens','muted'),next);target.append(pages);
}

const descriptions={overview:['Visão geral','Conversas, projetos e consumo em um só lugar.'],limits:['Uso disponível','O saldo das suas contas e quando os limites renovam.'],live:['Agentes em execução','Sinais dos registros locais, sem enviar comandos aos agentes.'],sessions:['Conversas','Encontre o trabalho, consulte mensagens e acompanhe o consumo.'],projects:['Projetos','Veja em quais projetos os tokens foram usados.'],settings:['Configurações','Fontes, assinaturas e atualização do índice local.'],detail:['Conversa','Mensagens, origem e consumo desta sessão.']};
function syncForm(){for(const [key,value] of Object.entries(params))if(form.elements[key]&&form.elements[key]!==document.activeElement)form.elements[key].value=value;document.querySelectorAll('.date-field').forEach(n=>n.hidden=params.period!=='custom');}
async function loadView({silent=false}={}){
  viewRefreshPending=false;filtersPending=false;const retrying=viewRetryNeeded;viewRetryNeeded=false;
  viewController?.abort();viewController=new AbortController();const controller=viewController;
  const version=++sequence,hash=location.hash.slice(1)||'overview';
  const previousView=currentView;
  currentView=hash.startsWith('session=')?'detail':(descriptions[hash]?hash:'overview');
  if(previousView!==currentView||!loadView.shown){loadView.shown=true;scrollTo(0,0);}
  for(const n of document.querySelectorAll('.view'))n.hidden=(n.dataset.section||n.id)!==currentView;
  for(const a of document.querySelectorAll('[data-view]')){if(a.dataset.view===currentView)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}
  $('#page-title').textContent=descriptions[currentView][0];$('#page-description').textContent=descriptions[currentView][1];
  const filtered=['overview','sessions','projects'].includes(currentView);form.hidden=!filtered;$('#period-note').hidden=!filtered;$('#export').hidden=!filtered;
  $('#machine-note').hidden=!filtered||!appState?.imports?.length;
  $('#welcome').hidden=appState?.settings.setupComplete!==false||currentView!=='overview';
  const targets=[document.getElementById(currentView),...(currentView==='overview'?[$('#overview-summary')]:[])];
  const focus=document.activeElement,restoreFocus=targets.some(target=>target.contains(focus));
  const labels={overview:'Atualizando os números…',sessions:'Carregando conversas…',projects:'Carregando projetos…',detail:'Abrindo a conversa…',live:'Consultando agentes…'};
  const done=startLoading('view',filtered?'Aplicando filtros…':labels[currentView]||'Atualizando o painel…',silent?[]:targets,silent);
  if(!silent||retrying)error('');
  if(filtered)$('#export').disabled=true;
  let succeeded=false;
  try{
    if(currentView==='flow')return;
    if(currentView==='limits')return renderLimits();
    if(currentView==='live'){await renderLive(version,controller.signal);succeeded=true;return;}
    if(currentView==='settings')return renderSettings();
    if(currentView==='detail'){await renderDetail(decodeURIComponent(hash.slice(8)),version,controller.signal);succeeded=true;return;}
    const data=await api('/api/overview?'+query(),undefined,controller.signal);if(version!==sequence||viewRefreshPending)return;overview=data;
    options(form.elements.model,data.options.models);options(form.elements.project,data.options.projects);syncForm();
    $('#period-note').textContent=datetime(data.start)+' → '+datetime(data.end)+' · '+(appState?.timezone||'fuso local')+' · '+(params.machine?machineName(params.machine):'todos os computadores')+' · '+(params.provider||'todos os provedores')+(appState?.progress.running?' · leitura em andamento':'');
    const remotes=appState?.imports||[];
    $('#machine-note').hidden=!remotes.length;
    $('#machine-note').textContent=remotes.map(remote=>remote.label+': '+datetime(remote.start)+' → '+datetime(remote.end)+'.').join(' ')+' Atualizar registros verifica este Mac. Para renovar uma coleta importada, exporte novamente no computador de origem.';
    if(currentView==='overview')renderOverview(data);
    if(currentView==='projects')renderProjects(data);
    if(currentView==='sessions')await renderSessions(version,controller.signal);
    succeeded=true;
  }catch(e){if(version===sequence&&!controller.signal.aborted){viewRetryNeeded=true;if(!silent)targets.forEach(target=>target.classList.add('is-stale'));error(e.message+' Tente novamente para atualizar esta tela.',true);}}
  finally{
    if(version===sequence){
      if(viewRefreshPending){void loadView({silent});done();return;}
      if(succeeded){targets.forEach(target=>target.classList.remove('is-stale'));if(filtered)$('#export').disabled=!appState||filtersPending||loadingJobs.has($('#export'));}
      done();
      if(restoreFocus&&focus.isConnected&&!focus.disabled&&document.activeElement===document.body)focus.focus({preventScroll:true});
      if(previousView!==currentView)requestAnimationFrame(()=>scrollTo(0,0));
    }else done();
  }
}

let debounce;
form.addEventListener('submit',event=>event.preventDefault());
form.addEventListener('input',event=>{viewController?.abort();params=Object.fromEntries(new FormData(form));filtersPending=true;$('#export').disabled=true;clearTimeout(debounce);debounce=setTimeout(()=>{syncForm();saveFilters();offset=0;if(params.period!=='custom'||(params.start&&params.end))loadView();},event.target.type==='search'?250:0);});
$('#clear').addEventListener('click',()=>{clearTimeout(debounce);params={period:'7d',provider:'',model:'',project:'',machine:'',q:''};form.reset();syncForm();saveFilters();offset=0;loadView();});
$('#retry-view').addEventListener('click',()=>loadView());
$('#refresh').addEventListener('click',async()=>{
  const control=$('#refresh');if(control.disabled)return;
  refreshQueued={baseline:appState?.metadata.last_scan,started:false};
  control.disabled=true;control.classList.add('is-working');control.setAttribute('aria-busy','true');$('#refresh span').textContent='Preparando…';
  $('#index-progress').hidden=false;$('#index-label').textContent='Preparando a leitura dos registros…';$('#index-meter').removeAttribute('value');
  const done=startLoading('refresh','Solicitando atualização dos registros…');
  try{await api('/api/refresh',{});await loadState();setTimeout(loadState,800);}
  catch(e){refreshQueued=null;control.disabled=false;control.classList.remove('is-working');control.removeAttribute('aria-busy');$('#refresh span').textContent='Atualizar registros';$('#index-progress').hidden=true;error(e.message);}
  finally{done();}
});
const darkQuery=matchMedia('(prefers-color-scheme: dark)');
const currentTheme=()=>document.documentElement.dataset.theme||(darkQuery.matches?'dark':'light');
function themeLabel(){$('#theme-label').textContent=currentTheme()==='dark'?'Usar tema claro':'Usar tema escuro';}
$('#theme-toggle').addEventListener('click',()=>{const theme=currentTheme()==='light'?'dark':'light';document.documentElement.dataset.theme=theme;try{localStorage.setItem('agentes.theme',theme);}catch{}themeLabel();});
darkQuery.addEventListener('change',themeLabel);themeLabel();
$('#chart-measure').addEventListener('click',event=>{const option=event.target.closest('button[data-value]');if(!option||option.dataset.value===chartMetric)return;chartMetric=option.dataset.value;for(const b of $('#chart-measure').querySelectorAll('button'))b.setAttribute('aria-pressed',String(b===option));renderChart();});
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(currentView==='overview'&&overview)renderChart();},150);});
$('#session-sort').addEventListener('change',()=>{offset=0;loadView();});
$('#previous').addEventListener('click',()=>{offset=Math.max(0,offset-40);loadView();});
$('#next').addEventListener('click',()=>{offset+=40;loadView();});
async function download(control,path,label){
  return runAction(control,label,async()=>{
    if(path==='/api/transfer'){
      const state=await api('/api/state');if(state.progress.running||!state.metadata.initial_scan_complete)throw new Error('Conclua a leitura dos registros antes de exportar.');
    }
    const bridge=window.webkit?.messageHandlers?.murExports;
    if(bridge)return bridge.postMessage({action:'save',path});
    const response=await fetch(path);
    if(!response.ok)throw new Error('Não foi possível gerar a exportação. Tente novamente.');
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=el('a');
    link.href=url;link.download=path.startsWith('/api/transfer')?'MUR-7-dias.mur':'MUR-consumo.csv';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    return {saved:true};
  });
}
$('#export').addEventListener('click',async()=>{try{await download($('#export'),'/api/export?'+query({sort:$('#session-sort').value}),'Exportando CSV…');}catch(e){error(e.message);}});
$('#settings-form').addEventListener('submit',async event=>{
  event.preventDefault();const f=event.currentTarget,submit=$('#save-subscriptions');if(submit.disabled)return;
  const subscriptions=[...$('#subscription-rows').children].map(row=>{
    const value=key=>row.querySelector('[data-field="'+key+'"]').value;
    const payments=[...row.querySelectorAll('[data-payment-id]')].map(payment=>({id:payment.dataset.paymentId,date:payment.querySelector('[data-payment-field=date]').value,amountUsd:Number(payment.querySelector('[data-payment-field=amountUsd]').value)}));
    return {id:row.dataset.id,provider:value('provider'),label:value('label'),accountId:value('accountId'),monthlyUsd:value('monthlyUsd')===''?null:Number(value('monthlyUsd')),quantity:Number(value('quantity')),startDate:value('startDate'),endDate:value('endDate'),cancelDate:value('cancelDate'),renews:row.querySelector('[data-field=renews]').checked,payments};
  });
  try{await runAction(submit,'Salvando histórico…',async()=>{
    await api('/api/settings',{subscriptions,refreshSeconds:Number(f.elements.refreshSeconds.value)});
    const firstRun=!appState.settings.setupComplete;
    if(firstRun){
      refreshQueued={baseline:appState.metadata.last_scan,started:false};
      try{await api('/api/refresh',{});}catch(e){refreshQueued=null;throw e;}
    }
    $('#settings-result').textContent='Histórico salvo. Cada período usa o preço e a vigência correspondentes.';
    await loadState();if(firstRun)location.hash='overview';
  });}catch(e){error(e.message);}
});
window.addEventListener('hashchange',()=>{error('');loadView();});
syncForm();if(location.hash==='#flow')loadView();loadState().then(loadView);
setInterval(()=>{if((!document.hidden||refreshQueued||appState?.progress.running)&&!loadState.running)loadState();},5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!loadState.running)void loadState();});
setInterval(()=>{if(!document.hidden&&currentView==='live'&&!loadingJobs.has('view'))renderLive().catch(e=>error(e.message));},8000);

$('#start-analysis').addEventListener('click',()=>$('#refresh').click());
$('#add-subscription').addEventListener('click',()=>addSubscriptionRow({provider:'OpenAI',label:'',monthlyUsd:null,quantity:1}));
$('#no-subscriptions').addEventListener('click',()=>{if($('#subscription-rows').children.length){error('Para encerrar uma assinatura, preencha as datas do período. Preserve os registros que já existiam.');return;}$('#settings-form').requestSubmit();});
$('#sources-form').addEventListener('submit',async event=>{
  event.preventDefault();const f=event.currentTarget,submit=f.querySelector('button');
  try{await runAction(submit,'Salvando fontes…',async()=>{await api('/api/settings',{machineLabel:f.elements.machineLabel.value,timezone:f.elements.timezone.value,sources:Object.fromEntries(['codex','claude','grok'].map(k=>[k,f.elements[k].value]))});$('#sources-result').textContent='Fontes salvas. Use Atualizar registros para ler as novas pastas.';await loadState();});}
  catch(e){error(e.message);}
});
$('#export-machine').addEventListener('click',async()=>{
  try{await download($('#export-machine'),'/api/transfer','Exportando este Mac…');}
  catch(e){$('#transfer-result').textContent=e.message;}
});
$('#import-machine').addEventListener('change',async event=>{
  const input=event.currentTarget,file=input.files[0];if(!file)return;
  $('#transfer-result').textContent='Conferindo a coleta…';
  try{await runAction(input,'Importando coleta…',async()=>{
    const response=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Local-Token':csrf},body:file});
    const body=await response.json();if(!response.ok)throw new Error(body.error);
    $('#transfer-result').textContent='Coleta importada. O período “7 dias · coleta importada” reúne os computadores sem duplicar eventos.';
    params.period='combined';params.machine='';saveFilters();await loadState();renderSettings();
  });}catch(e){$('#transfer-result').textContent=e.message;}finally{input.value='';}
});
