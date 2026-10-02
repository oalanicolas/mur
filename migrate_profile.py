import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from core import Store, timestamp


def migrate(source, destination):
    if source.resolve() == destination.resolve():
        raise ValueError('Origem e destino precisam ser diferentes.')
    if not (source/'agents.sqlite3').is_file():
        raise ValueError('O índice anterior não foi encontrado.')
    if destination.exists():
        backup = Path(tempfile.mkdtemp(prefix='MUR-before-migration-',dir=destination.parent))/'profile'
        shutil.move(destination,backup)
        print('Perfil anterior preservado em '+str(backup),flush=True)
    destination.mkdir(parents=True,mode=0o700)
    with sqlite3.connect(f'file:{source.resolve()}/agents.sqlite3?mode=ro',uri=True) as origin:
        with sqlite3.connect(destination/'agents.sqlite3') as target:
            origin.backup(target,pages=1000)
    if (source/'settings.json').exists():
        shutil.copy2(source/'settings.json',destination/'settings.json')
    report_root = source.parent.parent
    for original,name in [(report_root/'project-audit/project-classifications.json','project-classifications.json'),
                          (report_root/'relatorio-consumo-ia-semanal.html','report.html')]:
        if original.exists():
            shutil.copy2(original,destination/name)
    store = Store(destination)
    store.save_settings(dict(store.settings,setupComplete=True))
    raw = report_root/'raw.json'
    if raw.exists():
        archived = json.loads(raw.read_text())
        with store.connect() as c:
            store.meta(c,'audit_window',[timestamp(archived['start']),timestamp(archived['end'])])
    for file in destination.iterdir():
        if file.is_file():file.chmod(0o600)
    result = store.overview({'period':'combined'}) if store.state()['imports'] else store.overview({'period':'7d'})
    print(json.dumps({'counts':store.state()['counts'],'monthly':sum(store.settings['monthly'].values()),
                      'tokens':result['totals']['tokens'],'imports':len(store.state()['imports'])},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Migra explicitamente o índice da versão pessoal, preservando o original.')
    parser.add_argument('source',type=Path)
    parser.add_argument('--destination',type=Path,default=Path.home()/'Library/Application Support/MUR')
    args = parser.parse_args()
    migrate(args.source,args.destination)
