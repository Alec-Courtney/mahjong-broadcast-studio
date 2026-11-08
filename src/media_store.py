"""One project-relative import library and self-contained JSON transfers."""
import base64
import hashlib
import re
from pathlib import Path
from PySide6.QtGui import QImage

ASSET_KEY = re.compile(r'(?:media/(?:portraits|team_logos|branding)/[0-9a-f]{64}\.(?:png|jpg|webp|bmp)|assets/imported/(?:images|logos|backgrounds)/[0-9a-f]{64}\.(?:png|jpg|webp|bmp)|assets/imported/music/[0-9a-f]{64}\.(?:wav|mp3|flac|ogg|m4a|aac))')
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_MUSIC_BYTES = 512 * 1024 * 1024
MAX_DOCUMENT_BYTES = 50_000_000
IMAGE_FIELDS = frozenset(('photo','team_logo','corner_logo','opening_title','postmatch_background_path'))
MEDIA_FIELDS = IMAGE_FIELDS | {'postmatch_music_path'}
_PROJECT_ROOTS = {}


def register_asset_root(storage, project_root):
    """A Model injects its root once; event subdirectories inherit it."""
    _PROJECT_ROOTS[Path(storage).resolve()] = Path(project_root).resolve()


def asset_project_root(storage):
    storage=Path(storage).resolve()
    for parent in (storage,*storage.parents):
        if parent in _PROJECT_ROOTS:return _PROJECT_ROOTS[parent]
    # Standalone library/review sessions stay isolated from the real project.
    for parent in (storage,*storage.parents):
        if (parent/'library.json').is_file():return parent
    return storage


def infer_asset_root(storage):
    """A newly injected data directory is isolated unless it belongs to a library."""
    storage=Path(storage).resolve()
    if storage in _PROJECT_ROOTS:return _PROJECT_ROOTS[storage]
    for parent in (storage,*storage.parents):
        if (parent/'library.json').is_file():return asset_project_root(parent)
    return storage


def resolve_media_path(storage, reference):
    path=Path(reference)
    if path.is_absolute():return path
    normalized=str(reference).replace('\\','/')
    base=asset_project_root(storage) if normalized.startswith('assets/imported/') else Path(storage)
    return base/path


def _atomic_bytes(target, raw):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.is_file() and target.stat().st_size == len(raw) and target.read_bytes() == raw:return
    # A same-name file can be truncated or externally replaced. Validated,
    # content-addressed import bytes must repair it rather than silently reuse it.
    from session_repository import atomic_bytes
    atomic_bytes(target, raw)


def archive_import_file(storage, source, category='data', *, event_owned=False):
    """Retain validated JSON or the unmodified source before portrait cropping."""
    source=Path(source)
    if category not in ('data','images'):raise ValueError('导入原档分类无效')
    limit=MAX_DOCUMENT_BYTES if category=='data' else MAX_TOTAL_BYTES
    if not source.is_file() or not 0<source.stat().st_size<=limit:raise ValueError('导入原档为空或超出大小限制')
    raw=source.read_bytes()
    suffix=source.suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}',suffix):suffix='.bin'
    if category=='data' and event_owned:
        key='imports/'+hashlib.sha256(raw).hexdigest()+suffix
        _atomic_bytes(Path(storage)/key,raw)
        return key
    folder='documents' if category=='data' else 'images/originals'
    key='assets/imported/'+folder+'/'+hashlib.sha256(raw).hexdigest()+suffix
    _atomic_bytes(asset_project_root(storage)/key,raw)
    return key


def import_media_file(storage, source, field):
    if field not in MEDIA_FIELDS:raise ValueError('导入素材用途无效')
    record={field:str(source)}
    manage_photos(record,storage,strict=True)
    return record[field]


def photo_records(value):
    if isinstance(value, dict):
        if isinstance(value.get('photo'), str) and value['photo']:
            yield value
        for child in value.values():
            yield from photo_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from photo_records(child)


def image_records(value):
    """Portraits, logos and selected backgrounds share the same transfer."""
    if isinstance(value, dict):
        for field in IMAGE_FIELDS:
            if isinstance(value.get(field), str) and value[field]:yield value, field
        for child in value.values():yield from image_records(child)
    elif isinstance(value, list):
        for child in value:yield from image_records(child)


def media_records(value):
    if isinstance(value,dict):
        for field in MEDIA_FIELDS:
            if isinstance(value.get(field),str) and value[field]:yield value,field
        for child in value.values():yield from media_records(child)
    elif isinstance(value,list):
        for child in value:yield from media_records(child)


def _checked_audio(raw, suffix):
    if not raw or len(raw)>MAX_MUSIC_BYTES:raise ValueError('音乐文件为空或超过512MiB')
    signatures={
        'wav':len(raw)>=12 and raw[:4]==b'RIFF' and raw[8:12]==b'WAVE',
        'mp3':raw[:3]==b'ID3' or (len(raw)>1 and raw[0]==255 and raw[1]&224==224),
        'flac':raw[:4]==b'fLaC','ogg':raw[:4]==b'OggS',
        'm4a':len(raw)>=12 and raw[4:8]==b'ftyp',
        'aac':len(raw)>1 and raw[0]==255 and raw[1]&246==240,
    }
    if not signatures.get(suffix,False):raise ValueError('音乐格式无效，请选择 WAV、MP3、FLAC、OGG、M4A 或 AAC')


def _checked_asset(key,raw):
    if key.startswith('assets/imported/music/'):_checked_audio(raw,Path(key).suffix[1:])
    else:_checked_image(raw)


def _checked_image(raw):
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('选手照片文件过大或为空')
    image = QImage.fromData(raw)
    if image.isNull() or image.width() > 10000 or image.height() > 10000 or image.width()*image.height() > 24000000:
        raise ValueError('选手照片格式或尺寸无效')
    return image


def decode_assets(assets):
    if not isinstance(assets, dict) or len(assets) > 512:
        raise ValueError('媒体素材表格式无效')
    decoded = {}
    total = 0
    for key, item in assets.items():
        if not isinstance(key, str) or not ASSET_KEY.fullmatch(key) or not isinstance(item, dict):
            raise ValueError('媒体素材路径无效')
        try:
            raw = base64.b64decode(item['data'], validate=True)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('媒体素材编码无效') from error
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('媒体素材总大小超过32MiB')
        _checked_asset(key,raw)
        digest = hashlib.sha256(raw).hexdigest()
        if Path(key).stem != digest or item.get('sha256') != digest:
            raise ValueError('媒体素材校验失败')
        decoded[key] = raw
    return decoded


def manage_photos(state, storage, source_base=None, embedded=None, strict=False):
    """Import selected media once; identities, events and snapshots share it."""
    storage = Path(storage)
    available = {} if embedded is None else embedded
    cache = {}
    from state_snapshots import FrozenDict, mutable_copy
    full_check = strict or source_base is not None or embedded is not None
    matches = state.get('broadcast',{}).get('matches',[]) if isinstance(state,dict) else []
    asset_root = str(asset_project_root(storage).resolve())
    for index, archive in enumerate(matches):
        if not isinstance(archive,FrozenDict):continue
        if full_check or (getattr(archive,'_managed_asset_root',None)!=asset_root and any(
                not reference.startswith('assets/imported/') or not ASSET_KEY.fullmatch(reference)
                for record,field in media_records(archive) for reference in [record[field]])):
            matches[index] = mutable_copy(archive)
    def records(value):
        if isinstance(value,dict):
            if not full_check and isinstance(value,FrozenDict) and getattr(value,'_managed_asset_root',None)==asset_root:return
            for field in MEDIA_FIELDS:
                if isinstance(value.get(field),str) and value[field]:yield value,field
            for child in value.values():yield from records(child)
        elif isinstance(value,list):
            for child in value:yield from records(child)
    for record, field in records(state):
        reference = record[field]
        cache_key = (field, reference)
        if cache_key in cache:
            record[field] = cache[cache_key]
            continue
        raw = available.get(reference)
        if raw is None:
            ref_path = Path(reference)
            if reference.startswith('assets/imported/') and ASSET_KEY.fullmatch(reference) and resolve_media_path(storage,reference).is_file():
                cache[cache_key] = reference
                continue
            candidates = [ref_path] if ref_path.is_absolute() else []
            if not ref_path.is_absolute():
                if source_base:candidates += [resolve_media_path(source_base,reference),Path(source_base)/ref_path]
                candidates += [resolve_media_path(storage,reference),storage/ref_path]
            source = next((candidate for candidate in candidates if candidate.is_file()), None)
            if source is None:
                if strict or embedded is not None:
                    label='音乐文件缺失：' if field=='postmatch_music_path' else '标题卡文件缺失：' if field=='opening_title' else '背景文件缺失：' if field=='postmatch_background_path' else 'Logo文件缺失：' if field=='corner_logo' else '队伍Logo文件缺失：' if field=='team_logo' else '照片文件缺失：'
                    raise ValueError(label+record.get('name', reference))
                continue
            limit=MAX_MUSIC_BYTES if field=='postmatch_music_path' else MAX_IMAGE_BYTES
            if source.stat().st_size > limit:raise ValueError('音乐文件超过512MiB' if field=='postmatch_music_path' else '图片文件超过8MiB')
            raw = source.read_bytes()
        if field=='postmatch_music_path':
            suffix=Path(reference).suffix.lower()[1:];_checked_audio(raw,suffix)
        else:
            _checked_image(raw)
            suffix = 'jpg' if raw.startswith(b'\xff\xd8') else 'webp' if raw[8:12] == b'WEBP' else 'bmp' if raw[:2] == b'BM' else 'png'
        # Convert exotic legacy images to PNG, keeping content and alpha.
        if field!='postmatch_music_path' and suffix == 'png' and not raw.startswith(b'\x89PNG'):
            from PySide6.QtCore import QByteArray,QBuffer,QIODevice
            buffer_data=QByteArray();buffer=QBuffer(buffer_data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            _checked_image(raw).save(buffer,'PNG');raw=bytes(buffer_data);buffer.close()
        folder='music' if field=='postmatch_music_path' else 'backgrounds' if field in ('postmatch_background_path','opening_title') else 'logos' if field in ('team_logo','corner_logo') else 'images'
        key = 'assets/imported/'+folder+'/'+hashlib.sha256(raw).hexdigest()+'.'+suffix
        _atomic_bytes(asset_project_root(storage)/key,raw)
        record[field] = key
        cache[cache_key] = key
    if not full_check:
        for archive in matches:
            if not isinstance(archive,FrozenDict) or getattr(archive,'_managed_asset_root',None)==asset_root:continue
            refs = (record[field] for record,field in media_records(archive))
            if all(reference.startswith('assets/imported/') and ASSET_KEY.fullmatch(reference)
                   and resolve_media_path(storage,reference).is_file() for reference in refs):
                archive._managed_asset_root = asset_root


def export_assets(state, storage):
    manage_photos(state, storage, strict=True)
    assets = {}
    total = 0
    for record, field in media_records(state):
        key = record[field]
        if key in assets:
            continue
        source=resolve_media_path(storage,key)
        if total+source.stat().st_size>MAX_TOTAL_BYTES:
            raise ValueError('媒体素材总大小超过32MiB，请减少图片或音乐大小后再导出；本地素材仍完整保留')
        raw = source.read_bytes()
        _checked_asset(key,raw)
        digest = hashlib.sha256(raw).hexdigest()
        if Path(key).stem != digest:
            raise ValueError('托管媒体素材校验失败，请重新导入原素材或完整赛事备份')
        total += len(raw)
        if total>MAX_TOTAL_BYTES:raise ValueError('媒体素材总大小超过32MiB，请减少图片或音乐大小后再导出')
        assets[key] = dict(sha256=digest, data=base64.b64encode(raw).decode('ascii'))
    return assets
