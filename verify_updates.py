import base64
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
SPARKLE = ROOT/'installation/sparkle'
NAMESPACE = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
ET.register_namespace('sparkle', NAMESPACE)


def run(*args):
    return subprocess.check_output([str(arg) for arg in args], stderr=subprocess.STDOUT, text=True)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def verify():
    root = Path(tempfile.mkdtemp(prefix='qa-updates-', dir=ROOT/'installation'))
    binary = root/'MURUpdateTest'
    run('/usr/bin/xcrun','swiftc','-parse-as-library','-O','-module-cache-path',ROOT/'installation/swift-cache',
        ROOT/'macos/ValidateUpdates.swift','-F',SPARKLE,'-framework','Sparkle',
        '-Xlinker','-rpath','-Xlinker','@executable_path/../Frameworks','-o',binary)
    results = []
    for scenario in ('valid', 'no-update', 'tampered-feed', 'tampered-archive'):
        case = root/scenario
        target = case/'installed/MUR Update Test.app'
        (target/'Contents/MacOS').mkdir(parents=True)
        (target/'Contents/Frameworks').mkdir()
        shutil.copy2(binary,target/'Contents/MacOS/MURUpdateTest')
        run('/usr/bin/ditto',SPARKLE/'Sparkle.framework',target/'Contents/Frameworks/Sparkle.framework')
        key = case/'test-only-private-key'
        descriptor = os.open(key, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(base64.b64encode(secrets.token_bytes(32)).decode())
        public_key = run(target/'Contents/MacOS/MURUpdateTest','public-key',key).strip()
        published = case/'published'
        published.mkdir()
        server = http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(QuietHandler,directory=str(published)))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        address = 'http://127.0.0.1:'+str(server.server_port)+'/'
        output = case/'result.json'
        profile = case/'profile.json'
        profile.write_text(json.dumps({'history':'preserve-me','subscriptions':[500,100,300,200,200,200]}))
        before = hashlib.sha256(profile.read_bytes()).hexdigest()
        info = {'CFBundleIdentifier':'app.mur.reports.update-test.'+uuid.uuid4().hex,
                'CFBundleName':'MUR Update Test','CFBundleExecutable':'MURUpdateTest','CFBundlePackageType':'APPL',
                'CFBundleVersion':'3.0.2','CFBundleShortVersionString':'3.0.0','LSMinimumSystemVersion':'13.0',
                'SUFeedURL':address+'appcast.xml','SUPublicEDKey':public_key,'SURequireSignedFeed':True,
                'SUVerifyUpdateBeforeExtraction':True,'SUEnableAutomaticChecks':False,'SUEnableSystemProfiling':False,
                'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},'MURValidationResult':str(output)}
        (target/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        run('/usr/bin/codesign','--force','--sign','-',target)
        new = case/'next/MUR Update Test.app'
        new.parent.mkdir()
        run('/usr/bin/ditto',target,new)
        version = '3.0.2' if scenario=='no-update' else '3.0.3'
        (new/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(info,CFBundleVersion=version)))
        run('/usr/bin/codesign','--force','--sign','-',new)
        archive = published/'update.zip'
        run('/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',new,archive)
        signature = run(SPARKLE/'bin/sign_update','--ed-key-file',key,'-p',archive).strip()
        if not re.fullmatch(r'[A-Za-z0-9+/=]{88}',signature):
            raise RuntimeError('Assinatura de teste inválida.')
        rss = ET.Element('rss',version='2.0')
        channel = ET.SubElement(rss,'channel')
        ET.SubElement(channel,'title').text='MUR Update Test'
        item = ET.SubElement(channel,'item')
        ET.SubElement(item,'title').text='MUR atualização de teste'
        ET.SubElement(item,'{'+NAMESPACE+'}version').text=version
        ET.SubElement(item,'{'+NAMESPACE+'}minimumSystemVersion').text='13.0'
        ET.SubElement(item,'enclosure',{'url':address+'update.zip','length':str(archive.stat().st_size),
            'type':'application/octet-stream','{'+NAMESPACE+'}edSignature':signature})
        feed = published/'appcast.xml'
        ET.ElementTree(rss).write(feed,encoding='utf-8',xml_declaration=True)
        run(SPARKLE/'bin/sign_update','--ed-key-file',key,feed)
        if scenario=='tampered-feed':
            feed.write_bytes(feed.read_bytes().replace(b'MUR Update Test',b'MUR Changed Test'))
        if scenario=='tampered-archive':
            with archive.open('r+b') as handle:
                handle.seek(128); original=handle.read(1); handle.seek(128); handle.write(bytes([original[0]^1]))
        log = (case/'test.log').open('w')
        process = subprocess.Popen([str(target/'Contents/MacOS/MURUpdateTest')],stdout=log,stderr=log)
        try:
            deadline = time.monotonic()+90
            result = {}
            while time.monotonic()<deadline:
                process.poll()
                if output.exists():
                    result = json.loads(output.read_text())
                    if result.get('phase') in ('relaunched','not-found','error'):
                        break
                time.sleep(.25)
            actual = plistlib.loads((target/'Contents/Info.plist').read_bytes())['CFBundleVersion']
            expected = {'valid':'relaunched','no-update':'not-found','tampered-feed':'error','tampered-archive':'error'}[scenario]
            assert result.get('phase')==expected,(scenario,result)
            assert actual==('3.0.3' if scenario=='valid' else '3.0.2'),(scenario,actual)
            assert hashlib.sha256(profile.read_bytes()).hexdigest()==before
            results.append({'scenario':scenario,'phase':result['phase'],'installedVersion':actual,'profilePreserved':True})
            print(json.dumps(results[-1]),flush=True)
        finally:
            if process.poll() is None:process.terminate()
            log.close()
            server.shutdown();server.server_close()
    (ROOT/'installation/validation-updates.json').write_text(json.dumps({'ok':True,'checks':results,'output':str(root)},indent=2))


if __name__=='__main__':
    verify()
