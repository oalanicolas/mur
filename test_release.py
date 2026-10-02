import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from build_release import update_channel
import release_updates as release


class ReleaseTests(unittest.TestCase):
    def test_channel_rejects_local_addresses_credentials_and_invalid_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'channel.json'
            valid={'feedURL':'https://mur.lendario.ai/updates/appcast.xml','downloadBaseURL':'https://mur.lendario.ai/downloads/','publicKey':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}
            path.write_text(json.dumps(valid));self.assertEqual(update_channel(path),valid)
            for change in [{'feedURL':'http://mur.lendario.ai/appcast.xml'},{'feedURL':'https://user:password@mur.lendario.ai/appcast.xml'},
                           {'feedURL':'https://localhost/appcast.xml'},{'feedURL':'https://mur.lendario.ai/appcast.xml?token=private'},
                           {'publicKey':'invalid'}]:
                path.write_text(json.dumps(dict(valid,**change)))
                with self.assertRaises(ValueError):update_channel(path)

    def test_canceled_keychain_access_never_promotes_an_unsigned_feed(self):
        for existing in (False,True):
            with self.subTest(existing=existing),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);(root/'macos').mkdir();(root/'dist/channel').mkdir(parents=True)
                channel={'feedURL':'https://mur.lendario.ai/updates/appcast.xml','downloadBaseURL':'https://mur.lendario.ai/downloads/','publicKey':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}
                (root/'macos/update-channel.json').write_text(json.dumps(channel))
                info={'CFBundleIdentifier':release.IDENTIFIER,'MURReleaseVersion':release.VERSION,'CFBundleVersion':'3.0.2',
                      'SUFeedURL':channel['feedURL'],'SUPublicEDKey':channel['publicKey'],'SURequireSignedFeed':True,
                      'SUVerifyUpdateBeforeExtraction':True,'LSMinimumSystemVersion':'13.0'}
                bundle=root/'MUR.app'
                files={'Contents/Info.plist':plistlib.dumps(info),'Contents/MacOS/MUR':b'executable',
                       'Contents/Resources/backend/billing.py':b'billing','Contents/Resources/backend/web/app.js':b'app'}
                for name,body in files.items():
                    destination=bundle/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(body)
                with zipfile.ZipFile(root/'dist'/f'MUR-{release.VERSION}-universal.zip','w') as archive:
                    for name,body in files.items():archive.writestr('MUR.app/'+name,body)
                (root/'dist'/f'MUR-{release.VERSION}-universal.dmg').write_bytes(b'dmg')
                feed=root/'dist/channel/appcast.xml'
                previous=b'<rss><channel><title>Previous signed release</title></channel></rss>'
                if existing:feed.write_bytes(previous)
                notes=root/'notes.html';notes.write_text('<p>Release</p>')
                def output(args,**kwargs):
                    return channel['publicKey'] if str(args[0]).endswith('generate_keys') else 'A'*86+'=='
                def run(args,**kwargs):
                    if str(args[-1]).endswith('appcast.pending.xml'):raise subprocess.CalledProcessError(1,args)
                with patch.object(release,'ROOT',root),patch.object(release.subprocess,'check_output',side_effect=output),patch.object(release.subprocess,'run',side_effect=run):
                    with self.assertRaises(subprocess.CalledProcessError):release.prepare_feed(bundle,notes)
                self.assertEqual(feed.exists(),existing)
                if existing:self.assertEqual(feed.read_bytes(),previous)
                self.assertFalse((root/'dist/site/updates/appcast.xml').exists())


if __name__=='__main__':unittest.main()
