import argparse
from build_release import ROOT, prepare, install, update_channel

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Prepara e instala o MUR independente para o usuário atual.')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    bundle = prepare(ROOT / 'installation', channel=update_channel(ROOT/'macos/update-channel.json'))
    if not args.prepare_only:
        install(bundle)
    print(bundle, flush=True)
