const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');

async function waitFor(condition, timeout=15000) {
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline){if(await condition())return;await new Promise(resolve=>setTimeout(resolve,50));}
  throw new Error('Tempo esgotado na validação da interface.');
}

(async()=>{
  fs.mkdirSync(path.join(__dirname,'installation'),{recursive:true});
  const root=fs.mkdtempSync(path.join(__dirname,'installation','ui-'));
  const home=path.join(root,'home'),profile=path.join(root,'profile'),ready=path.join(root,'ready.json');
  const logs=path.join(home,'.codex','sessions');fs.mkdirSync(logs,{recursive:true});
  const timestamp=new Date().toISOString();
  const records=[
    {type:'session_meta',timestamp,payload:{id:'ui-fixture',cwd:path.join(home,'Code','Example')}},
    {type:'turn_context',timestamp,payload:{model:'gpt-6.1-sol',effort:'low'}},
    {type:'event_msg',timestamp,payload:{type:'token_count',info:{total_token_usage:{input_tokens:100,output_tokens:10},last_token_usage:{input_tokens:100,output_tokens:10}}}}
  ];
  fs.writeFileSync(path.join(logs,'fixture.jsonl'),records.map(row=>JSON.stringify(row)).join('\n')+'\n');
  const server=spawn(process.env.MUR_PYTHON||'python3',['server.py','--port','0','--data-dir',profile,'--ready-file',ready,'--parent-pid',String(process.pid)],{
    cwd:__dirname,env:{...process.env,MUR_HOME:home,MUR_TIMEZONE:'UTC'},stdio:['ignore','ignore','pipe']
  });
  let serverError='';server.stderr.on('data',chunk=>{serverError+=chunk;});
  let browser;
  const checks=[],errors=[];
  function check(label,value){assert.ok(value,label);checks.push(label);console.log('OK: '+label);}
  try{
    await waitFor(()=>fs.existsSync(ready)||server.exitCode!==null);
    assert.equal(server.exitCode,null,serverError);
    const base='http://127.0.0.1:'+JSON.parse(fs.readFileSync(ready)).port;
    const state=await fetch(base+'/api/state').then(response=>response.json());
    await fetch(base+'/api/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':state.csrf},body:'{}'});
    await waitFor(async()=>{const current=await fetch(base+'/api/state').then(response=>response.json());return current.metadata.initial_scan_complete&&current.counts.events===1;});
    browser=await chromium.launch({headless:true,...(process.env.MUR_BROWSER_CHANNEL?{channel:process.env.MUR_BROWSER_CHANNEL}:{})});
    const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    let releaseStartup,startupRequests=0;
    await page.route('**/api/state',async route=>{if(++startupRequests===1)await new Promise(resolve=>{releaseStartup=resolve;});await route.continue().catch(()=>{});});
    await page.goto(base);
    await waitFor(()=>Boolean(releaseStartup));
    check('Abertura lenta mantém a exportação bloqueada e mostra carregamento',await page.locator('#export').isDisabled()&&(await page.locator('#request-label').innerText()).includes('Abrindo'));
    await page.selectOption('[name=provider]','OpenAI');await page.waitForFunction(()=>document.querySelector('#overview-summary').getAttribute('aria-busy')==='false');
    check('Filtro consultado antes do estado inicial também mantém a exportação bloqueada',await page.locator('#export').isDisabled());
    releaseStartup();
    await page.waitForFunction(()=>document.querySelector('#metric-tokens').textContent==='110'&&document.querySelector('#request-status').hasAttribute('data-idle'));
    await page.unroute('**/api/state');
    check('Perfil sintético isolado mostra 110 tokens',await page.locator('#metric-tokens').innerText()==='110');
    check('Resumo financeiro continua acima dos filtros',await page.evaluate(()=>document.querySelector('#overview-summary').getBoundingClientRect().bottom<=document.querySelector('#filters').getBoundingClientRect().top));
    await page.evaluate(async()=>{const input=document.querySelector('[name=q]');input.focus();input.value='Texto em edição';await loadState();});
    check('Atualização automática preserva o texto em edição',await page.locator('[name=q]').inputValue()==='Texto em edição');
    await page.locator('#clear').click();await page.waitForFunction(()=>document.querySelector('#request-status').hasAttribute('data-idle'));
    await page.locator('[name=q]').fill('Busca em edição');
    check('Digitar na busca bloqueia imediatamente a exportação do filtro anterior',await page.locator('#export').isDisabled());
    await page.locator('#clear').click();await page.waitForFunction(()=>document.querySelector('#request-status').hasAttribute('data-idle'));

    const indexHeld=[];let indexQueries=0;
    await page.route('**/api/overview?**',async route=>{
      const response=await route.fetch(),snapshot=await response.json();
      if(++indexQueries===1)snapshot.totals.tokens=55;
      if(indexQueries<=2)await new Promise(resolve=>indexHeld.push(resolve));
      await route.fulfill({response,json:snapshot});
    });
    await page.route('**/api/state',async route=>{const response=await route.fetch(),state=await response.json();state.metadata.last_scan+=1;await route.fulfill({response,json:state});});
    await page.locator('#clear').click();await waitFor(()=>indexHeld.length===1);
    await page.evaluate(()=>loadState());indexHeld[0]();await waitFor(()=>indexHeld.length===2);
    check('Índice concluído durante uma consulta repete a leitura antes de liberar exportação',await page.locator('#export').isDisabled()&&await page.locator('#overview-summary').getAttribute('aria-busy')==='true');
    indexHeld[1]();await page.waitForFunction(()=>document.querySelector('#request-status').hasAttribute('data-idle')&&!document.querySelector('#export').disabled);
    check('Resultados parciais de uma consulta anterior ao índice não ficam na tela',await page.locator('#metric-tokens').innerText()==='110'&&indexQueries===2);
    await page.unroute('**/api/overview?**');await page.unroute('**/api/state');
    await page.evaluate(()=>loadState());await page.waitForFunction(()=>document.querySelector('#request-status').hasAttribute('data-idle')&&!document.querySelector('#export').disabled);

    const held=[];let failing=false;
    await page.route('**/api/overview?**',async route=>{
      if(failing){await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Consulta indisponível no teste.'})});return;}
      const provider=new URL(route.request().url()).searchParams.get('provider');
      if(provider)await new Promise(resolve=>held.push({provider,release:resolve}));
      await route.continue().catch(()=>{});
    });
    await page.selectOption('[name=provider]','OpenAI');await waitFor(()=>held.length===1);
    check('Filtro mostra carregamento e atenua os resultados',await page.locator('#overview-summary').getAttribute('aria-busy')==='true'&&await page.locator('#request-label').innerText()==='Aplicando filtros…');
    check('Filtros permanecem utilizáveis durante a consulta',await page.locator('[name=provider]').isEnabled());
    await page.screenshot({path:path.join(root,'loading-desktop.png'),fullPage:true});
    await page.selectOption('[name=provider]','Anthropic');await waitFor(()=>held.length===2);
    held[0].release();await new Promise(resolve=>setTimeout(resolve,100));
    check('Resposta substituída não encerra o loading da seleção nova',await page.locator('#overview-summary').getAttribute('aria-busy')==='true');
    held[1].release();
    await page.waitForFunction(()=>document.querySelector('#metric-tokens').textContent==='0'&&document.querySelector('#overview-summary').getAttribute('aria-busy')==='false');
    check('Filtro mais recente determina os dados',await page.locator('[name=provider]').inputValue()==='Anthropic');
    failing=true;await page.locator('#clear').click();await page.locator('#retry-view').waitFor();
    check('Falha encerra loading e impede uso de dados antigos',await page.locator('#overview-summary').getAttribute('aria-busy')==='false'&&await page.locator('#export').isDisabled());
    await page.route('**/api/state',async route=>{const response=await route.fetch(),state=await response.json();state.progress.error='Falha fictícia de indexação.';await route.fulfill({response,json:state});});
    await page.evaluate(()=>loadState());
    check('Erro de indexação preserva a nova tentativa da consulta',await page.locator('#retry-view').isVisible()&&await page.locator('#export').isDisabled());
    await page.unroute('**/api/state');await page.route('**/api/state',route=>route.fulfill({status:503,json:{error:'Estado indisponível no teste.'}}));
    await page.evaluate(()=>loadState());
    check('Erro de conexão no polling também preserva a nova tentativa',await page.locator('#retry-view').isVisible()&&await page.locator('#overview-summary').evaluate(node=>node.inert));
    await page.unroute('**/api/state');
    failing=false;await page.locator('#retry-view').click();
    await page.waitForFunction(()=>document.querySelector('#metric-tokens').textContent==='110'&&document.querySelector('#request-status').hasAttribute('data-idle'));
    check('Nova tentativa restaura dados e exportação',await page.locator('#export').isEnabled()&&await page.locator('#error').isHidden());
    await page.unroute('**/api/overview?**');

    let releaseSessions;
    await page.route('**/api/sessions?**',async route=>{await new Promise(resolve=>{releaseSessions=resolve;});await route.continue().catch(()=>{});});
    await page.locator('[data-view=sessions]').click();await waitFor(()=>Boolean(releaseSessions));
    check('Navegação para conversas tem loading',await page.locator('#sessions').getAttribute('aria-busy')==='true');
    releaseSessions();await page.waitForFunction(()=>document.querySelector('#sessions').getAttribute('aria-busy')==='false');
    await page.unroute('**/api/sessions?**');
    await page.locator('[data-view=settings]').click();
    let releaseSave,saves=0;
    await page.route('**/api/settings',async route=>{saves++;await new Promise(resolve=>{releaseSave=resolve;});await route.continue();});
    await page.locator('#save-subscriptions').click();await waitFor(()=>Boolean(releaseSave));
    check('Salvar mostra spinner e impede repetição',await page.locator('#save-subscriptions').innerText()==='Salvando histórico…'&&await page.locator('#save-subscriptions').isDisabled());
    await page.evaluate(()=>document.querySelector('#settings-form').dispatchEvent(new Event('submit',{cancelable:true,bubbles:true})));
    check('Segundo envio do mesmo formulário é ignorado',saves===1);
    releaseSave();await page.waitForFunction(()=>!document.querySelector('#save-subscriptions').disabled);
    await page.unroute('**/api/settings');

    let releaseImport;
    await page.route('**/api/import',async route=>{await new Promise(resolve=>{releaseImport=resolve;});await route.continue();});
    await page.locator('#import-machine').setInputFiles({name:'invalid.mur',mimeType:'application/octet-stream',buffer:Buffer.from('invalid')});
    await waitFor(()=>Boolean(releaseImport));
    check('Importação indica andamento',await page.locator('#import-machine').isDisabled()&&(await page.locator('#request-label').innerText()).includes('Importando'));
    releaseImport();await page.waitForFunction(()=>!document.querySelector('#import-machine').disabled);
    check('Importação inválida restaura o controle',Boolean(await page.locator('#transfer-result').innerText()));
    await page.locator('[data-view=overview]').click();await page.waitForFunction(()=>!document.querySelector('#export').disabled);
    let releaseExport;
    await page.route('**/api/export?**',async route=>{await new Promise(resolve=>{releaseExport=resolve;});await route.continue();});
    const download=page.waitForEvent('download');await page.locator('#export').click();await waitFor(()=>Boolean(releaseExport));
    check('Exportação tem loading até o arquivo estar pronto',await page.locator('#export').isDisabled()&&(await page.locator('#request-label').innerText()).includes('Exportando'));
    let releaseExportFilter;
    await page.route('**/api/overview?**',async route=>{await new Promise(resolve=>{releaseExportFilter=resolve;});await route.continue();});
    await page.selectOption('[name=provider]','OpenAI');await waitFor(()=>Boolean(releaseExportFilter));
    releaseExport();const exported=await download;
    await page.waitForFunction(()=>!document.querySelector('#export').classList.contains('is-working'));
    check('Fim da exportação mantém o botão bloqueado enquanto o filtro carrega',await page.locator('#export').isDisabled());
    releaseExportFilter();await page.waitForFunction(()=>!document.querySelector('#export').disabled);await page.unroute('**/api/overview?**');
    await page.locator('#clear').click();await page.waitForFunction(()=>document.querySelector('#request-status').hasAttribute('data-idle'));
    check('CSV contém somente a sessão sintética',fs.readFileSync(await exported.path(),'utf8').includes('ui-fixture'));

    let releaseReplay,libraryRequests=0;
    await page.route('**/api/replay/sessions',async route=>{if(++libraryRequests===1)await new Promise(resolve=>{releaseReplay=resolve;});await route.continue();});
    await page.evaluate(()=>Object.defineProperty(document,'hidden',{get:()=>true,configurable:true}));
    await page.locator('[data-view=flow]').click();await waitFor(()=>Boolean(releaseReplay));
    check('Fluxo de trabalho indica leitura de sessões',(await page.locator('#request-label').innerText()).includes('sessões do fluxo'));
    await page.evaluate(()=>document.dispatchEvent(new Event('mur:index-updated')));
    releaseReplay();await page.waitForFunction(()=>document.querySelector('#flow').classList.contains('rp-empty')&&document.querySelector('#request-status').hasAttribute('data-idle'));
    check('Fluxo selecionado carrega mesmo com a janela em segundo plano',await page.locator('#flow').evaluate(node=>node.classList.contains('rp-empty')));
    await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
    check('Índice atualizado durante leitura de uma biblioteca vazia repete a consulta',libraryRequests===2);
    await page.unroute('**/api/replay/sessions');
    const replayStart=Date.now()/1000;let replayFails=true,compareFails=true,libraryFails=true,releaseCompare;
    await page.route('**/api/replay/compare',async route=>{if(compareFails){await route.fulfill({status:503,json:{error:'Comparativo indisponível no teste.'}});return;}await new Promise(resolve=>{releaseCompare=resolve;});await route.fulfill({json:{rows:[]}});});
    await page.route('**/api/replay/sessions',route=>route.fulfill(libraryFails?{status:503,json:{error:'Biblioteca indisponível no teste.'}}:{json:{rows:[{id:'single',started:replayStart,ask:'Sessão fictícia',tokens:0,usd:0}],default:'single'}}));
    await page.route('**/api/replay?**',route=>route.fulfill(replayFails?{status:503,json:{error:'Falha temporária no teste.'}}:{json:{id:'single',started:replayStart,ended:replayStart+10,duration:10,request:'Sessão fictícia recuperada',model:'claude-sonnet-5',events:[{kind:'request',t:0,real:replayStart,label:'Sessão fictícia recuperada'}],stats:{tokens:0,usd:0},usage:{tokens:0,usd:0},insights:[]}}));
    await page.evaluate(()=>document.dispatchEvent(new Event('mur:index-updated')));await page.locator('#rp-retry').waitFor();
    check('Falha ao atualizar uma biblioteca vazia deixa a nova tentativa visível',await page.locator('.replay-side').isVisible());
    libraryFails=false;await page.locator('#rp-retry').click();await page.waitForFunction(()=>document.querySelector('#rp-ask').textContent==='Falha temporária no teste.'&&!document.querySelector('#rp-retry').hidden);
    check('Falha do fluxo bloqueia a reprodução antiga',await page.locator('#flow-play').isDisabled()&&await page.locator('#rp-map').isHidden());
    replayFails=false;await page.locator('#rp-retry').click();
    await page.waitForFunction(()=>document.querySelector('#rp-ask').textContent==='Sessão fictícia recuperada'&&!document.querySelector('#flow').classList.contains('rp-failed'));
    check('Uma biblioteca com uma sessão permite tentar novamente',await page.locator('#rp-map').isVisible()&&await page.locator('#rp-retry').isHidden());
    await page.locator('#rp-compare-retry').waitFor();compareFails=false;
    await page.locator('#rp-compare-retry').click();await waitFor(()=>Boolean(releaseCompare));
    check('Comparativo permite tentar novamente e mostra carregamento',await page.locator('#rp-compare-retry').isDisabled()&&(await page.locator('#request-label').innerText()).includes('comparativo'));
    releaseCompare();await page.waitForFunction(()=>document.querySelector('#rp-compare-note').textContent.startsWith('0 sessões')&&document.querySelector('#rp-compare-retry').hidden);
    check('Nova tentativa recupera o comparativo sem recarregar o painel',await page.locator('#rp-ask').innerText()==='Sessão fictícia recuperada');
    await page.unroute('**/api/replay/compare');
    await page.unroute('**/api/replay/sessions');await page.unroute('**/api/replay?**');
    await page.route('**/api/replay/sessions',route=>route.fulfill({json:{rows:['single','selected'].map(id=>({id,started:replayStart,ask:'Sessão '+id,tokens:0,usd:0})),default:'single'}}));
    const replayRequests=[];let releaseSelected;
    await page.route('**/api/replay?**',async route=>{
      const id=new URL(route.request().url()).searchParams.get('id');replayRequests.push(id);
      if(id==='selected')await new Promise(resolve=>{releaseSelected=resolve;});
      await route.fulfill({json:{id,started:replayStart,ended:replayStart+10,duration:10,request:'Sessão '+id,model:'claude-sonnet-5',events:[{kind:'request',t:0,real:replayStart,label:'Sessão '+id},...Array.from({length:17},(_,i)=>({kind:'agent',id:'agent-'+i,label:'Agente '+i,model:'claude-sonnet-5',type:'teste',depth:1,state:'ok',t:1+i*.1,end:9,real:replayStart+1+i*.1,real_end:replayStart+9,usage:{input:1,output:1,usd:0}}))],stats:{tokens:34,usd:0},usage:{tokens:34,usd:0},insights:[]}});
    });
    await page.reload();await page.waitForFunction(()=>document.querySelector('#rp-ask').textContent==='Sessão single');
    await page.selectOption('#rp-session','selected');await waitFor(()=>Boolean(releaseSelected));
    check('Árvore e linha do tempo anteriores ficam indisponíveis durante a leitura',await page.locator('#rp-tree-more').evaluate(button=>{button.focus();return button.closest('.rp-tree').inert&&document.activeElement!==button;})&&await page.locator('.replay-lower').getAttribute('aria-busy')==='true');
    await page.evaluate(()=>{document.dispatchEvent(new Event('mur:index-updated'));document.dispatchEvent(new Event('mur:index-updated'));});
    await new Promise(resolve=>setTimeout(resolve,100));
    check('Atualização do índice preserva a sessão escolhida enquanto ela carrega',await page.locator('#rp-session').inputValue()==='selected'&&replayRequests.join(',')==='single,selected');
    releaseSelected();await page.waitForFunction(()=>document.querySelector('#rp-ask').textContent==='Sessão selected'&&!document.querySelector('#flow').classList.contains('rp-loading'));
    check('Sessão escolhida termina de carregar após a atualização do índice',await page.locator('#flow-play').isEnabled());
    await page.locator('#rp-tree-more').click();
    check('Árvore volta a permitir a expansão após carregar a sessão',await page.locator('#rp-agent-rows tr:not([hidden])').count()===18);
    await page.emulateMedia({reducedMotion:'reduce',colorScheme:'dark'});
    await page.locator('[data-view=overview]').click();await page.waitForFunction(()=>!document.querySelector('#export').disabled);
    await page.setViewportSize({width:390,height:844});
    let releaseMobile;
    await page.route('**/api/overview?**',async route=>{await new Promise(resolve=>{releaseMobile=resolve;});await route.continue();});
    await page.selectOption('[name=provider]','OpenAI');await waitFor(()=>Boolean(releaseMobile));
    check('Loading respeita movimento reduzido',await page.locator('.loading-spinner').evaluate(node=>getComputedStyle(node).animationName)==='none');
    check('Sem transbordamento no celular',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(root,'loading-mobile.png'),fullPage:true});releaseMobile();
    await page.waitForFunction(()=>document.querySelector('#request-status').hasAttribute('data-idle'));
    check('Sem erros de JavaScript',errors.length===0);
    fs.writeFileSync(path.join(root,'result.json'),JSON.stringify({ok:true,checks,errors},null,2));
    console.log(JSON.stringify({ok:true,checks:checks.length,evidence:root}));
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
