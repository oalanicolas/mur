import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent


def validate(bundle, architecture):
    root = Path(tempfile.mkdtemp(prefix='qa-'+architecture+'-',dir=ROOT/'installation'))
    home,profile,output = root/'home',root/'profile',root/'result'
    log = home/'.codex/sessions/test.jsonl'
    log.parent.mkdir(parents=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = [
        {'timestamp':now,'type':'session_meta','payload':{'id':'portable-native','cwd':str(home/'Code/MyProject')}},
        {'timestamp':now,'type':'turn_context','payload':{'model':'gpt-6.1-sol','effort':'low'}},
        {'timestamp':now,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':100,'output_tokens':10},'last_token_usage':{'input_tokens':100,'output_tokens':10}}}}
    ]
    log.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    claims = {'email':'example-person@example.test','https://api.openai.com/auth':{'chatgpt_plan_type':'pro','chatgpt_account_id':'synthetic-account','chatgpt_user_id':'synthetic-user'}}
    token = 'header.'+base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')+'.signature'
    (home/'.codex/auth.json').write_text(json.dumps({'auth_mode':'chatgpt','tokens':{'id_token':token,'refresh_token':'SYNTHETIC-SECRET-NOT-FOR-EXPORT'}}))
    (home/'.claude').mkdir()
    (home/'.claude.json').write_text(json.dumps({'oauthAccount':{'accountUuid':'claude-fixture','organizationUuid':'org-fixture','emailAddress':'example-person@example.test','organizationRateLimitTier':'default_claude_max_20x'}}))
    (home/'.grok').mkdir()
    (home/'.grok/auth.json').write_text(json.dumps({'synthetic':{'user_id':'grok-fixture','email':'example-person@example.test'}}))
    before = hashlib.sha256(log.read_bytes()).hexdigest()
    environment = dict(os.environ,MUR_HOME=str(home),MUR_DATA_DIR=str(profile))
    process = subprocess.Popen(['/usr/bin/arch','-'+architecture,str(bundle/'Contents/MacOS/MUR'),str(output)],env=environment)
    try:
        code = process.wait(timeout=100)
    except subprocess.TimeoutExpired:
        process.terminate()
        raise RuntimeError('A janela não concluiu a validação em 100 segundos.')
    result = json.loads((output/'result.json').read_text())
    if code or not result['ok']:
        raise RuntimeError(json.dumps(result,ensure_ascii=False))
    assert before == hashlib.sha256(log.read_bytes()).hexdigest(), 'O histórico original foi alterado.'
    pid = result['backendPID']
    for _ in range(30):
        try:
            os.kill(pid,0)
        except ProcessLookupError:
            break
        time.sleep(.2)
    else:
        raise RuntimeError('O serviço continuou rodando após encerrar o aplicativo.')
    result.update(architecture=architecture,sourceUnchanged=True,backendStopped=True,output=str(output))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle',type=Path)
    args = parser.parse_args()
    results = [validate(args.bundle,architecture) for architecture in ('arm64','x86_64')]
    (ROOT/'installation/validation-portable-native.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps({'ok':True,'architectures':[r['architecture'] for r in results],'checks':sum(len(r['checks'])+2 for r in results)},ensure_ascii=False))
