"""Portable ZIP backups with separate JSON records and streamed media files.

This module uses only the standard library so the legacy converter stays small.
Local session.json v6 is deliberately unchanged for read-only score listeners.
"""
from __future__ import annotations
import base64
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

FORMAT = 'MahjongOverlayStudioBackup'
EXTENSION = '.mjbackup'
FILE_FILTER = '赛事备份包 (*.mjbackup)'
MAX_METADATA = 256 * 1024 * 1024
MAX_TOTAL = 8 * 1024**3
MAX_MUSIC = 512 * 1024**2
MAX_IMAGE = 8 * 1024**2
MAX_FILES = 20000
MEDIA_FIELDS = {'photo','team_logo','corner_logo','opening_title','postmatch_background_path','postmatch_music_path'}
ASSET_KEY = re.compile(r'assets/imported/(?:images|logos|backgrounds|music)/[0-9a-f]{64}\.(?:png|jpg|webp|bmp|wav|mp3|flac|ogg|m4a|aac)')


def json_bytes(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')


def parse_json(raw):
    def reject(value):raise ValueError('数据不能包含 '+value)
    try:return json.loads(raw.decode('utf-8-sig'),parse_constant=reject)
    except (UnicodeError,json.JSONDecodeError) as e:raise ValueError('备份中的 JSON 无效') from e


def media_records(value):
    if isinstance(value,dict):
        for field in MEDIA_FIELDS:
            if isinstance(value.get(field),str) and value[field]:yield value,field
        for child in value.values():yield from media_records(child)
    elif isinstance(value,list):
        for child in value:yield from media_records(child)


@contextmanager
def stream(source):
    if isinstance(source,bytes):
        with io.BytesIO(source) as f:yield f
    else:
        with Path(source).open('rb') as f:yield f


def digest_source(source):
    sha=hashlib.sha256();size=0
    with stream(source) as f:
        while block:=f.read(1024*1024):sha.update(block);size+=len(block)
    return sha.hexdigest(),size


def checked_media(source,field,suffix):
    with stream(source) as f:header=f.read(32)
    if field=='postmatch_music_path':
        valid={'.wav':header[:4]==b'RIFF' and header[8:12]==b'WAVE',
               '.mp3':header[:3]==b'ID3' or len(header)>1 and header[0]==255 and header[1]&224==224,
               '.flac':header[:4]==b'fLaC','.ogg':header[:4]==b'OggS',
               '.m4a':header[4:8]==b'ftyp','.aac':len(header)>1 and header[0]==255 and header[1]&246==240}
        if not valid.get(suffix):raise ValueError('音乐格式无效：'+suffix)
        return suffix,MAX_MUSIC
    for signature,ext in ((b'\x89PNG\r\n\x1a\n','.png'),(b'\xff\xd8','.jpg'),(b'BM','.bmp')):
        if header.startswith(signature):return ext,MAX_IMAGE
    if header[:4]==b'RIFF' and header[8:12]==b'WEBP':return '.webp',MAX_IMAGE
    raise ValueError('图片格式无效，请先转换为 PNG、JPG、WEBP 或 BMP')


def _find_source(reference,storage,source_base):
    path=Path(reference)
    if path.is_absolute():candidates=[path]
    else:
        candidates=[Path(base)/path for base in (source_base,storage) if base is not None]
        if storage is not None and reference.replace('\\','/').startswith(('assets/imported/','media/')):
            candidates.extend(parent/path for parent in Path(storage).resolve().parents)
    found=next((p for p in candidates if p.is_file()),None)
    if found is None:raise ValueError('素材缺失：'+reference+'；请把素材放回原位置，或选择原软件目录后重试')
    return found


def write_package(filename,payload,*,storage=None,source_base=None,assets=None,kind='event',overwrite=True):
    """Write a complete sibling temporary archive, then publish it atomically."""
    from app_version import VERSION
    target=Path(filename)
    if target.suffix.lower()!=EXTENSION:raise ValueError('备份文件扩展名必须为 .mjbackup')
    if target.exists() and not overwrite:raise ValueError('同名备份包已存在，未覆盖：'+str(target))
    data=parse_json(json_bytes(payload));embedded=data.pop('assets',{})
    data.pop('asset_version',None)
    sources={};manifest_files={};seen={};total=0
    for record,field in media_records(data):
        ref=record[field];cache=(field,ref)
        if cache in seen:record[field]=seen[cache];continue
        supplied=(assets.get(ref) if assets is not None else embedded.get(ref))
        if isinstance(supplied,dict):
            try:raw=base64.b64decode(supplied['data'],validate=True)
            except (KeyError,ValueError) as e:raise ValueError('旧 JSON 素材编码无效：'+ref) from e
            if hashlib.sha256(raw).hexdigest()!=supplied.get('sha256') or Path(ref).stem!=supplied['sha256']:
                raise ValueError('旧 JSON 素材校验失败：'+ref)
            supplied=raw
        source=supplied if supplied is not None else _find_source(ref,storage,source_base)
        suffix,limit=checked_media(source,field,Path(ref).suffix.lower())
        digest,size=digest_source(source)
        if ASSET_KEY.fullmatch(ref.replace('\\','/')) and Path(ref).stem!=digest:
            raise ValueError('托管媒体素材校验失败，请重新导入原素材')
        if not 0<size<=limit:raise ValueError('素材为空或超出单文件限制：'+ref)
        folder='music' if field=='postmatch_music_path' else 'backgrounds' if field in ('opening_title','postmatch_background_path') else 'logos' if field in ('team_logo','corner_logo') else 'images'
        key=f'assets/imported/{folder}/{digest}{suffix}'
        record[field]=seen[cache]=key
        if key not in sources:
            sources[key]=source;manifest_files[key]=dict(sha256=digest,size=size);total+=size
        if total>MAX_TOTAL:raise ValueError('备份素材总量超过8GiB')
    documents={};matches=[]
    if kind=='event':
        if data.get('version') not in (5,6) or not isinstance(data.get('state'),dict):raise ValueError('请选择完整赛事 JSON')
        state=data.pop('state');archive=state.get('broadcast',{}).pop('matches',[])
        if not isinstance(archive,list) or len(archive)>10000:raise ValueError('半庄归档数量或格式无效')
        for index,match in enumerate(archive):
            name=f'matches/{index+1:06d}.json';documents[name]=json_bytes(match);matches.append(name)
        documents['live.json']=json_bytes(state)
    elif kind!='settings':raise ValueError('备份类型无效')
    documents['event.json']=json_bytes(data)
    if sum(map(len,documents.values()))>MAX_METADATA:raise ValueError('赛事文本数据超过256MiB')
    for name,raw in documents.items():manifest_files[name]=dict(sha256=hashlib.sha256(raw).hexdigest(),size=len(raw))
    if len(manifest_files)>MAX_FILES:raise ValueError('备份文件数量过多')
    manifest=dict(format=FORMAT,format_version=1,application_version=VERSION,kind=kind,
                  created_at=datetime.now(timezone.utc).isoformat(),matches=matches,files=manifest_files)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.backup-',suffix='.tmp',dir=target.parent);os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
            for name,raw in documents.items():z.writestr(name,raw)
            for name,source in sources.items():
                sha=hashlib.sha256();size=0
                with stream(source) as src,z.open(name,'w',force_zip64=True) as dst:
                    while block:=src.read(1024*1024):dst.write(block);sha.update(block);size+=len(block)
                if dict(sha256=sha.hexdigest(),size=size)!=manifest_files[name]:raise ValueError('素材在备份期间改变，请重新导出')
            z.writestr('manifest.json',json_bytes(manifest))
        with open(temporary,'r+b') as f:f.flush();os.fsync(f.fileno())
        if overwrite:os.replace(temporary,target)
        elif os.name=='nt':os.rename(temporary,target)
        else:os.link(temporary,target);os.unlink(temporary)
    finally:Path(temporary).unlink(missing_ok=True)
    return target


class PackageAssets(Mapping):
    """Read one checked asset at a time instead of retaining every audio file."""
    def __init__(self,filename,names):self.filename=Path(filename);self.names=frozenset(names)
    def __len__(self):return len(self.names)
    def __iter__(self):return iter(self.names)
    def __getitem__(self,key):
        if key not in self.names:raise KeyError(key)
        with zipfile.ZipFile(self.filename) as z:raw=z.read(key)
        if hashlib.sha256(raw).hexdigest()!=Path(key).stem:raise ValueError('备份素材在校验后改变')
        return raw


def read_package(filename):
    source=Path(filename)
    if source.suffix.lower()!=EXTENSION:raise ValueError('新版只导入 .mjbackup 备份包；请先用转换工具转换旧 JSON')
    try:
        with zipfile.ZipFile(source) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(infos)>MAX_FILES+1 or len(names)!=len(set(names)):raise ValueError('备份存在重复文件或文件数量过多')
            if 'manifest.json' not in names or z.getinfo('manifest.json').file_size>8*1024**2:raise ValueError('备份清单缺失或过大')
            manifest=parse_json(z.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format')!=FORMAT or type(manifest.get('format_version')) is not int or manifest['format_version']!=1:
                raise ValueError('备份格式或版本不受支持')
            files=manifest.get('files');matches=manifest.get('matches');kind=manifest.get('kind')
            if not isinstance(files,dict) or not isinstance(matches,list) or kind not in ('event','settings'):
                raise ValueError('备份清单结构无效')
            if len(matches)>10000 or any(not isinstance(n,str) or not re.fullmatch(r'matches/[0-9]{6}\.json',n) for n in matches) or len(set(matches))!=len(matches):
                raise ValueError('半庄清单无效')
            documents={'event.json',*matches}
            if kind=='event':documents.add('live.json')
            elif matches:raise ValueError('设置包不能包含半庄记录')
            assets=set(files)-documents
            if any(not isinstance(n,str) or not ASSET_KEY.fullmatch(n) for n in assets):raise ValueError('备份文件路径无效')
            if set(names)!={'manifest.json',*files} or not documents<=files.keys():raise ValueError('备份文件缺失或清单不一致')
            total=metadata=0
            for name,entry in files.items():
                info=z.getinfo(name)
                if not isinstance(entry,dict) or type(entry.get('size')) is not int or entry['size']!=info.file_size or not isinstance(entry.get('sha256'),str):raise ValueError('备份大小校验失败')
                if info.flag_bits&1 or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):raise ValueError('备份压缩方式不受支持')
                limit=MAX_METADATA if name in documents else MAX_MUSIC if '/music/' in name else MAX_IMAGE
                if not 0<=info.file_size<=limit:raise ValueError('备份文件超出大小限制')
                total+=info.file_size
                if name in documents:metadata+=info.file_size
                if total>MAX_TOTAL+MAX_METADATA or metadata>MAX_METADATA:raise ValueError('备份内容超出大小限制')
                sha=hashlib.sha256()
                with z.open(info) as f:
                    while block:=f.read(1024*1024):sha.update(block)
                if sha.hexdigest()!=entry['sha256'] or name in assets and Path(name).stem!=entry['sha256']:raise ValueError('备份校验失败：'+name)
            data=parse_json(z.read('event.json'))
            if not isinstance(data,dict):raise ValueError('赛事信息格式无效')
            if kind=='event':
                state=parse_json(z.read('live.json'))
                if not isinstance(state,dict) or not isinstance(state.get('broadcast'),dict):raise ValueError('实时对局格式无效')
                state['broadcast']['matches']=[parse_json(z.read(n)) for n in matches];data['state']=state
            for record,field in media_records(data):
                if record[field] not in assets:raise ValueError('备份引用的素材缺失：'+record[field])
        return data,PackageAssets(source,assets),kind
    except (zipfile.BadZipFile,KeyError,EOFError,TypeError,RuntimeError) as e:raise ValueError('备份包损坏或结构无效：'+str(e)) from e


def convert_legacy(filename,output=None,assets_root=None):
    source=Path(filename).resolve()
    if source.suffix.lower()!='.json' or source.stat().st_size>MAX_METADATA:raise ValueError('请选择不超过256MiB的旧 JSON 文件')
    data=parse_json(source.read_bytes())
    if not isinstance(data,dict):raise ValueError('旧 JSON 格式无效')
    kind='settings' if data.get('format')=='MahjongOverlayStudioEventSettings' else 'event'
    if kind=='event' and (data.get('version') not in (5,6) or not isinstance(data.get('state'),dict)):
        raise ValueError('此文件不是完整赛事存档；请选择赛事备份 JSON 或 session.json')
    target=Path(output) if output else source.with_suffix(EXTENSION)
    return write_package(target,data,storage=source.parent,source_base=assets_root or source.parent,kind=kind)
