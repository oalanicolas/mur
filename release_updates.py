import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from build_release import ROOT, VERSION, IDENTIFIER, update_channel


def prepare_feed(bundle, notes):
    channel = update_channel(ROOT/'macos/update-channel.json')
    info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    expected = {'CFBundleIdentifier':IDENTIFIER,'MURReleaseVersion':VERSION,
                'SUFeedURL':channel['feedURL'],'SUPublicEDKey':channel['publicKey'],
                'SURequireSignedFeed':True,'SUVerifyUpdateBeforeExtraction':True}
    if any(info.get(key)!=value for key,value in expected.items()):
        raise ValueError('O bundle não corresponde à versão e ao canal de produção.')
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],check=True)
    tools = ROOT/'installation/sparkle/bin'
    public = subprocess.check_output([str(tools/'generate_keys'),'--account',IDENTIFIER,'-p'],text=True).strip()
    if public != channel['publicKey']:
        raise ValueError('A chave de assinatura não corresponde à chave pública distribuída.')
    output = ROOT/'dist/channel'
    output.mkdir(parents=True,exist_ok=True)
    for suffix in ('zip','dmg'):
        source = ROOT/'dist'/f'MUR-{VERSION}-universal.{suffix}'
        destination = output/source.name
        if destination.exists() and hashlib.sha256(destination.read_bytes()).digest()!=hashlib.sha256(source.read_bytes()).digest():
            raise ValueError('Já existe outro arquivo para esta versão; incremente a versão antes de publicar.')
        shutil.copy2(source,destination)
    archive = output/f'MUR-{VERSION}-universal.zip'
    with zipfile.ZipFile(archive) as packaged:
        for name in ('Contents/Info.plist','Contents/MacOS/MUR','Contents/Resources/backend/billing.py','Contents/Resources/backend/web/app.js'):
            if packaged.read('MUR.app/'+name)!=(bundle/name).read_bytes():
                raise ValueError('O instalador não corresponde ao bundle informado.')
    signature = subprocess.check_output([str(tools/'sign_update'),'--account',IDENTIFIER,'-p',str(archive)],text=True).strip()
    if not re.fullmatch(r'[A-Za-z0-9+/=]{88}',signature):
        raise ValueError('Assinatura da atualização inválida.')
    namespace = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
    ET.register_namespace('sparkle',namespace)
    feed = output/'appcast.xml'
    if feed.exists():
        root = ET.parse(feed).getroot()
        rss_channel = root.find('channel')
        if rss_channel is None:raise ValueError('Catálogo anterior inválido.')
        if any(item.findtext('{'+namespace+'}version')==info['CFBundleVersion'] for item in rss_channel.findall('item')):
            raise ValueError('Esta versão já está no catálogo; incremente a versão para uma nova publicação.')
    else:
        root = ET.Element('rss',version='2.0')
        rss_channel = ET.SubElement(root,'channel')
        ET.SubElement(rss_channel,'title').text='MUR — Model Usage Reports'
        ET.SubElement(rss_channel,'link').text='https://mur.lendario.ai/'
        ET.SubElement(rss_channel,'description').text='Atualizações oficiais do MUR para macOS.'
        ET.SubElement(rss_channel,'language').text='pt-br'
    item = ET.Element('item')
    ET.SubElement(item,'title').text='MUR '+VERSION
    ET.SubElement(item,'{'+namespace+'}version').text=info['CFBundleVersion']
    ET.SubElement(item,'{'+namespace+'}shortVersionString').text=VERSION
    ET.SubElement(item,'{'+namespace+'}minimumSystemVersion').text=info['LSMinimumSystemVersion']
    ET.SubElement(item,'description').text=notes.read_text()
    ET.SubElement(item,'enclosure',{'url':channel['downloadBaseURL']+archive.name,
        'length':str(archive.stat().st_size),'type':'application/octet-stream','{'+namespace+'}edSignature':signature})
    rss_channel.insert(0,item)
    draft = output/'appcast.pending.xml'
    ET.ElementTree(root).write(draft,encoding='utf-8',xml_declaration=True)
    subprocess.run([str(tools/'sign_update'),'--account',IDENTIFIER,str(draft)],check=True)
    subprocess.run([str(tools/'sign_update'),'--account',IDENTIFIER,'--verify',str(draft)],check=True)
    subprocess.run([str(tools/'sign_update'),'--account',IDENTIFIER,'--verify',str(archive),signature],check=True)
    draft.replace(feed)
    releases = [output/f'MUR-{VERSION}-universal.{suffix}' for suffix in ('zip','dmg')]
    (output/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in releases))
    (output/'release.json').write_text(json.dumps({'version':VERSION,'build':info['CFBundleVersion'],
        'channel':channel['feedURL'],'notarized':False,'downloads':[{ 'name':p.name,'bytes':p.stat().st_size,
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in releases]},indent=2))
    site = ROOT/'dist/site'
    (site/'updates').mkdir(parents=True,exist_ok=True)
    (site/'assets').mkdir(exist_ok=True)
    for name in ('index.html','style.css','_headers'):
        shutil.copy2(ROOT/'distribution'/name,site/name)
    for name in ('SHA256SUMS.txt','release.json'):
        shutil.copy2(output/name,site/name)
    shutil.copy2(feed,site/'updates/appcast.xml')
    shutil.copy2(ROOT/'DISTRIBUTION.md',site/'leia-me.txt')
    for source,name in [('web/favicon.svg','icon.svg'),('web/fonts/geist-latin-wght-normal.woff2','geist.woff2'),
                        ('web/fonts/source-serif-4-latin-opsz-normal.woff2','serif.woff2'),('web/fonts/LICENSES.txt','LICENSES.txt')]:
        shutil.copy2(ROOT/source,site/'assets'/name)
    print(output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Assina o catálogo e prepara somente os arquivos públicos da atualização.')
    parser.add_argument('bundle',type=Path)
    parser.add_argument('--notes',type=Path,required=True)
    args=parser.parse_args()
    prepare_feed(args.bundle,args.notes)
