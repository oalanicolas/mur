import test from 'node:test';
import assert from 'node:assert/strict';
import worker from './worker.mjs';

const path='https://mur.lendario.ai/downloads/MUR-3.0.0-beta.2-universal.zip';
function environment(object){return {ASSETS:{fetch:()=>new Response('site')},RELEASES:{get:async()=>object,head:async()=>object}};}
function object(extra={}){return {size:100,httpEtag:'"abc"',body:'test',writeHttpMetadata:()=>{},...extra};}
test('only immutable public installer names can be downloaded',async()=>{
  for(const name of ['settings.json','auth.json','data/agents.sqlite3','MUR.zip'])assert.equal((await worker.fetch(new Request('https://mur.lendario.ai/downloads/'+name),environment(object()))).status,404);
  assert.equal((await worker.fetch(new Request(path,{method:'POST'}),environment(object()))).status,405);
});
test('installer downloads include integrity-friendly cache and download headers',async()=>{
  const response=await worker.fetch(new Request(path),environment(object({range:{offset:0,length:100}})));
  assert.equal(response.status,200);assert.match(response.headers.get('Content-Disposition'),/attachment/);
  assert.match(response.headers.get('Cache-Control'),/no-transform/);assert.equal(response.headers.get('Content-Length'),'100');
  assert.equal(response.headers.get('Content-Range'),null);
});
test('range and head requests support resumable downloads',async()=>{
  const response=await worker.fetch(new Request(path,{headers:{Range:'bytes=20-39'}}),environment(object({range:{offset:20,length:20}})));
  assert.equal(response.status,206);assert.equal(response.headers.get('Content-Range'),'bytes 20-39/100');
  const head=await worker.fetch(new Request(path,{method:'HEAD'}),environment(object({range:{offset:0,length:100}})));
  assert.equal(head.status,200);assert.equal(head.headers.get('Content-Range'),null);assert.equal(await head.text(),'');
});
test('conditional requests and missing files do not return a false success',async()=>{
  const withoutBody=object();delete withoutBody.body;
  assert.equal((await worker.fetch(new Request(path,{headers:{'If-None-Match':'"abc"'}}),environment(withoutBody))).status,304);
  assert.equal((await worker.fetch(new Request(path),environment(null))).status,404);
  assert.equal(await (await worker.fetch(new Request('https://mur.lendario.ai/'),environment(null))).text(),'site');
});
