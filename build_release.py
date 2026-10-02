import argparse
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
VERSION = '3.0.0-beta.4'
IDENTIFIER = 'app.mur.reports'
BACKEND = ('core.py', 'server.py', 'replay.py', 'export_machine.py', 'import_machine.py', 'accounts.py', 'billing.py', 'limits.py', 'pricing.json')
WEB = ('index.html', 'app.js', 'theme.js', 'flow.js', 'flow.css', 'style.css', 'favicon.svg',
       'fonts/geist-latin-wght-normal.woff2', 'fonts/geist-mono-latin-wght-normal.woff2',
       'fonts/source-serif-4-latin-opsz-normal.woff2', 'fonts/LICENSES.txt')


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def runtime(architecture):
    assets = json.loads((ROOT / 'macos/runtime-manifest.json').read_text())
    asset = next(a for a in assets if ('aarch64' if architecture == 'arm64' else 'x86_64') in a['name'])
    cache = ROOT / 'installation/runtimes'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset['name']
    if not archive.exists():
        partial = archive.with_suffix('.download')
        urllib.request.urlretrieve(asset['browser_download_url'], partial)
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != asset['digest'].split(':')[1]:
        raise RuntimeError('O runtime baixado não confere com o manifesto.')
    return archive


def macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as handle:
        return handle.read(4) in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')


def sparkle():
    manifest = json.loads((ROOT/'macos/sparkle-manifest.json').read_text())
    cache = ROOT/'installation/sparkle'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache/('Sparkle-'+manifest['version']+'.tar.xz')
    if not archive.exists():
        partial = archive.with_suffix('.download')
        urllib.request.urlretrieve(manifest['url'], partial)
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
        raise RuntimeError('O Sparkle baixado não confere com o manifesto.')
    with tarfile.open(archive) as source:
        source.extractall(cache, filter='data')
    return cache


def update_channel(path):
    if path is None:
        return {}
    config = json.loads(Path(path).read_text())
    if set(config) != {'feedURL', 'publicKey', 'downloadBaseURL'}:
        raise ValueError('O canal precisa de feedURL, publicKey e downloadBaseURL; não inclua credenciais.')
    for name in ('feedURL', 'downloadBaseURL'):
        url = urlsplit(config[name])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('O canal e os downloads precisam de HTTPS público, sem credenciais ou parâmetros.')
        if url.hostname in ('localhost','127.0.0.1','::1') or url.hostname.endswith(('.local','.invalid','.test','.example')):
            raise ValueError('Endereços locais ou de exemplo não podem ser distribuídos.')
    if not urlsplit(config['feedURL']).path.endswith('.xml') or not config['downloadBaseURL'].endswith('/'):
        raise ValueError('O feed deve terminar em .xml, e a pasta de downloads deve terminar em /.')
    if len(base64.b64decode(config['publicKey'], validate=True)) != 32:
        raise ValueError('A chave pública de atualizações precisa ter 32 bytes.')
    return config


def prepare(destination, validation=False, identity='-', channel=None):
    destination.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='build-', dir=destination))
    bundle = stage / 'MUR.app'
    contents = bundle / 'Contents'
    resources = contents / 'Resources'
    executable = contents / 'MacOS/MUR'
    executable.parent.mkdir(parents=True)
    backend = resources / 'backend'
    backend.mkdir(parents=True)
    for name in BACKEND:
        shutil.copy2(ROOT / name, backend / name)
    for name in WEB:
        target = backend / 'web' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'web' / name, target)
    shutil.copy2(ROOT / 'assets/AgentesLocais.icns', resources / 'MUR.icns')
    shutil.copy2(ROOT / 'DISTRIBUTION.md', resources / 'Leia-me.md')
    shutil.copy2(ROOT / 'macos/runtime-manifest.json', resources / 'runtime-manifest.json')
    framework_source = sparkle()
    frameworks = contents/'Frameworks'
    frameworks.mkdir()
    run('/usr/bin/ditto', framework_source/'Sparkle.framework', frameworks/'Sparkle.framework')
    shutil.copy2(framework_source/'LICENSE', resources/'Sparkle-LICENSE.txt')
    shutil.copy2(ROOT/'macos/sparkle-manifest.json', resources/'sparkle-manifest.json')
    info = {'CFBundleName':'MUR', 'CFBundleDisplayName':'MUR', 'CFBundleIdentifier':IDENTIFIER + ('.validation' if validation else ''),
            'CFBundleVersion':'3.0.4', 'CFBundleShortVersionString':'3.0.0', 'CFBundleExecutable':'MUR',
            'CFBundlePackageType':'APPL', 'CFBundleIconFile':'MUR.icns', 'LSMinimumSystemVersion':'13.0',
            'NSHighResolutionCapable':True, 'NSHumanReadableCopyright':'MUR — Model Usage Reports · beta 4',
            'MURReleaseVersion':VERSION,
            'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True}}
    if channel and not validation:
        info.update(SUFeedURL=channel['feedURL'], SUPublicEDKey=channel['publicKey'],
                    SUEnableAutomaticChecks=True, SUAutomaticallyUpdate=False, SUAllowsAutomaticUpdates=False,
                    SUEnableSystemProfiling=False, SURequireSignedFeed=True, SUVerifyUpdateBeforeExtraction=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    binaries = []
    for architecture in ('arm64', 'x86_64'):
        print('Preparando ' + architecture, flush=True)
        target = resources / 'runtime' / architecture
        target.mkdir(parents=True)
        with tarfile.open(runtime(architecture)) as archive:
            archive.extractall(target, filter='data')
        binary = stage / ('MUR-' + architecture)
        command = ['/usr/bin/xcrun','swiftc','-parse-as-library','-O','-target',architecture+'-apple-macosx13.0',
                   '-module-cache-path',str(destination/'swift-cache'),'-file-prefix-map',str(ROOT)+'=/MUR']
        if validation:
            command += ['-D','APP_VALIDATION',str(ROOT/'macos/Validate.swift')]
        run(*command, ROOT/'macos/Agentes.swift', ROOT/'macos/Updates.swift', ROOT/'macos/UsageMenu.swift', '-F', frameworks, '-framework', 'Sparkle',
            '-Xlinker', '-rpath', '-Xlinker', '@executable_path/../Frameworks', '-o', binary)
        binaries.append(binary)
    run('/usr/bin/lipo','-create',*binaries,'-output',executable)
    signing = ['--force','--sign',identity]
    if identity != '-':
        signing += ['--options','runtime','--timestamp']
    for path in resources.rglob('*'):
        if macho(path):
            subprocess.run(['/usr/bin/codesign',*signing,str(path)],check=True,capture_output=True)
    for path in sorted(frameworks.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if not path.is_symlink() and (macho(path) or path.suffix in ('.app','.xpc','.framework')):
            run('/usr/bin/codesign',*signing,'--preserve-metadata=entitlements',path)
    run('/usr/bin/codesign',*signing,bundle)
    run('/usr/bin/codesign','--verify','--deep','--strict',bundle)
    files = [p for p in bundle.rglob('*') if p.is_file() and not p.is_symlink()]
    forbidden = (str(Path.home())+'/', 'chatgpt.site')
    for path in files:
        if path.suffix in ('.sqlite3','.jsonl') or path.name in ('settings.json','auth.json','account-observations.json','raw.json','reviewed.json'):
            raise RuntimeError('Arquivo privado no pacote: ' + str(path.relative_to(bundle)))
        if not str(path.relative_to(bundle)).startswith('Contents/Resources/runtime/'):
            body = path.read_bytes()
            if any(s.encode() in body for s in forbidden):
                raise RuntimeError('Referência pessoal no pacote: ' + str(path.relative_to(bundle)))
    (stage/'build-validation.json').write_text(json.dumps({'version':VERSION,'architectures':['arm64','x86_64'],
        'files':len(files),'bytes':sum(p.stat().st_size for p in files),'privateDataIncluded':False,
        'signature':'ad-hoc' if identity=='-' else 'Developer ID','notarized':False,
        'updater':'Sparkle '+json.loads((ROOT/'macos/sparkle-manifest.json').read_text())['version'],
        'updateChannelConfigured':bool(channel) and not validation},indent=2))
    (destination/'latest-build.json').write_text(json.dumps({'bundle':str(bundle),'validation':validation}))
    return bundle


def package(bundle):
    releases = ROOT / 'dist'
    releases.mkdir(exist_ok=True)
    archive = releases / f'MUR-{VERSION}-universal.zip'
    image = releases / f'MUR-{VERSION}-universal.dmg'
    run('/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',bundle,archive)
    staging = Path(tempfile.mkdtemp(prefix='dmg-',dir=ROOT/'installation'))
    run('/usr/bin/ditto',bundle,staging/'MUR.app')
    (staging/'Aplicativos').symlink_to('/Applications')
    shutil.copy2(ROOT/'DISTRIBUTION.md',staging/'Leia-me.md')
    run('/usr/bin/hdiutil','create','-ov','-volname','MUR','-srcfolder',staging,'-format','UDZO',image)
    (releases/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in (archive,image)))
    print('Distribuição: '+str(releases),flush=True)


def install(bundle):
    destination = Path.home()/'Applications/MUR.app'
    if destination.exists():
        previous = plistlib.loads((destination/'Contents/Info.plist').read_bytes())
        if previous.get('CFBundleIdentifier') not in (IDENTIFIER,'com.alan.agentes-locais.app'):
            raise RuntimeError('Já existe outro aplicativo chamado MUR; instalação preservada.')
        backups = ROOT/'installation/backups'
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='previous-',dir=backups))/'MUR.app'
        shutil.move(destination,backup)
    destination.parent.mkdir(parents=True,exist_ok=True)
    run('/usr/bin/ditto',bundle,destination)
    print('Instalado: '+str(destination),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Compila o MUR independente e prepara sua distribuição.')
    parser.add_argument('--package', action='store_true')
    parser.add_argument('--validation', action='store_true')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--sign', default='-', help='Identidade Developer ID no Keychain; padrão: beta ad-hoc.')
    parser.add_argument('--updates-config', type=Path, default=ROOT/'macos/update-channel.json', help='Canal HTTPS e chave pública para as atualizações Sparkle.')
    args = parser.parse_args()
    bundle = prepare(ROOT/'installation', args.validation, args.sign, update_channel(args.updates_config))
    if args.package:
        package(bundle)
    if args.install:
        if args.validation:
            raise SystemExit('O aplicativo de validação não pode substituir a versão instalada.')
        install(bundle)
    print(bundle,flush=True)
