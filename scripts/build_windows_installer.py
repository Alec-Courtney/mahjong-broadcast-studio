"""Build a self-contained update; never package the developer's event database."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from app_version import VERSION


def build(output, compiler=None):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dist = output/'bundle'
    bundle = dist/'MahjongOverlayStudio'
    if bundle.exists():
        raise SystemExit('Build output already exists; choose a new --output directory.')
    import imageio_ffmpeg
    decoder = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    data = [('LICENSE','.'),('docs/ORIGINAL_THIRD_PARTY_NOTICES.md','docs'),
            ('resources/engine.js','resources'),('resources/ASSET_SOURCES.json','resources'),
            ('resources/NotoSansCJKjp-Regular.otf','resources'),('resources/OFL-Noto.txt','resources'),
            ('resources/LICENSE-MahjongRepository.txt','resources'),
            ('resources/app_icon/mahjong_broadcast.ico','resources/app_icon')]
    for pattern in ('controls/*.svg','demo/*.json','demo/avatars/*.png','demo/portraits/mleague/*.png',
                    'demo_team_logos/*.png','tiles/*.svg','tiles/*.txt','sticks/*.png','sticks/*.txt',
                    'postmatch_audio/*_reveal.wav','postmatch_audio/*_sweep.wav','riichi_audio/*.wav'):
        data.extend((str(path.relative_to(ROOT)),str(path.parent.relative_to(ROOT)))
                    for path in (ROOT/'resources').glob(pattern))
    command = [sys.executable,'-m','PyInstaller','--clean','--noconfirm','--windowed','--onedir',
               '--name','MahjongOverlayStudio','--icon',str(ROOT/'resources/app_icon/mahjong_broadcast.ico'),'--paths',str(ROOT/'src'),
               '--additional-hooks-dir',str(ROOT/'scripts/pyinstaller/hooks'),
               '--distpath',str(dist),'--workpath',str(output/'work'),'--specpath',str(output)]
    for name in ('cv2','numpy','onnxruntime','SpoutGL','imageio_ffmpeg'):
        command += ['--exclude-module',name]
    for source,destination in data:
        command += ['--add-data',str(ROOT/source)+';'+destination]
    env = dict(os.environ, PATH=os.pathsep.join([str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']]))
    env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    subprocess.run(command+[str(ROOT/'run.py')],cwd=ROOT,env=env,check=True)
    # The runtime uses this exact decoder before considering any system install.
    target = bundle/'_internal/resources/audio/ffmpeg.exe'
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(decoder,target)
    licenses = bundle/'licenses';licenses.mkdir(exist_ok=True)
    for path in (ROOT/'resources/licenses').iterdir():
        if path.is_file():shutil.copyfile(path,licenses/path.name)
    distribution = importlib.metadata.distribution('imageio-ffmpeg')
    shutil.copyfile(Path(distribution.locate_file('imageio_ffmpeg-0.6.0.dist-info/LICENSE')),licenses/'IMAGEIO-FFMPEG-LICENSE.txt')
    shutil.copyfile(Path(sys.base_prefix)/'LICENSE.txt',licenses/'PYTHON-LICENSE.txt')
    for name in ('LICENSE','mahjong_camera_switch.lua'):
        shutil.copyfile(ROOT/name,bundle/name)
    shutil.copyfile(ROOT/'docs/START_HERE_WINDOWS.txt',bundle/'START-HERE.txt')
    for name in ('team-league-demo.json','individual-league-demo.json','individual-knockout-demo.json','PHOTO_NOTICE.md'):
        target=bundle/'demo-suite'/name;target.parent.mkdir(exist_ok=True)
        if name.endswith('.json'):
            from backup_package import convert_legacy
            convert_legacy(ROOT/'demo-suite'/name,target.with_suffix('.mjbackup'),assets_root=ROOT)
        else:shutil.copyfile(ROOT/'demo-suite'/name,target)
    shutil.copyfile(ROOT/'resources/demo/roster.json',bundle/'demo-suite/roster.json')
    documentation=bundle/'docs';documentation.mkdir(exist_ok=True)
    for path in (ROOT/('docs/releases/v'+VERSION+'.md'),ROOT/'docs/BACKUP_PACKAGE_FORMAT.md'):
        shutil.copyfile(path,documentation/path.name)
    manifest = dict(version=VERSION,events_bundled=False,files={})
    for path in sorted(bundle.rglob('*')):
        if path.is_file():manifest['files'][path.relative_to(bundle).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    (output/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    if compiler is not None:
        subprocess.run([str(compiler.resolve()),'/DProjectRoot='+str(ROOT),'/DBundleDir='+str(bundle),
                        '/DOutputDir='+str(output),'/DAppVersion='+VERSION,
                        '/DVersionInfoVersion='+VERSION.split('-')[0]+'.0',str(ROOT/'scripts/windows-installer.iss')],check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'local-packages'/('v'+VERSION))
    parser.add_argument('--compiler',type=Path)
    parser.add_argument('--bundle-only',action='store_true')
    options=parser.parse_args()
    if not options.bundle_only and options.compiler is None:parser.error('--compiler is required unless --bundle-only is selected')
    build(options.output,options.compiler)
