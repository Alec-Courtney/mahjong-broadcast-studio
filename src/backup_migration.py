"""Installer-owned conversion of legacy data; never rewrites source JSON."""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile
from backup_package import convert_legacy,parse_json,MAX_METADATA,json_bytes
from app_version import VERSION


def migrate_installation(root):
    root=Path(root).resolve()
    if not root.is_dir():raise ValueError('原安装目录不存在')
    candidates=set(root.glob('*.json'))
    for name in ('data','exports','legacy-backups','assets/imported/documents','demo-suite'):
        directory=root/name
        if directory.is_dir():candidates.update(directory.rglob('*.json'))
    converted=[];errors=[];skipped=0
    for source in sorted(candidates):
        if source.is_symlink() or not source.resolve().is_relative_to(root):continue
        try:
            if source.stat().st_size>MAX_METADATA:raise ValueError('JSON 文件超过256MiB')
            data=parse_json(source.read_bytes())
            if not isinstance(data,dict) or not (data.get('version') in (5,6) and isinstance(data.get('state'),dict)
                    or data.get('format')=='MahjongOverlayStudioEventSettings'):
                skipped+=1;continue
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            target=convert_legacy(source,assets_root=root)
            if hashlib.sha256(source.read_bytes()).hexdigest()!=before:raise ValueError('JSON 在转换期间改变，请退出导播台后重试')
            converted.append(target.relative_to(root).as_posix())
        except Exception as error:errors.append(dict(file=source.relative_to(root).as_posix(),error=str(error)))
    result=dict(version=VERSION,converted=converted,skipped=skipped,errors=errors,original_json_preserved=True)
    report=root/'data'/('backup-migration-'+VERSION+'.json');report.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.migration-',suffix='.tmp',dir=report.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(json_bytes(result));f.flush();os.fsync(f.fileno())
        os.replace(temp,report)
    finally:Path(temp).unlink(missing_ok=True)
    return result


def migration_main(arguments=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--migrate-backups',action='store_true')
    parser.add_argument('--root',type=Path,required=True)
    options=parser.parse_args(arguments)
    try:return int(bool(migrate_installation(options.root)['errors']))
    except Exception:return 1
