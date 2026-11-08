"""Portable chapter-player helpers, without a legacy demo generator."""
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import shutil
import subprocess
from urllib.parse import unquote, urlsplit


def number(value, label, default=None):
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(label + '必须是有效的非负秒数')
    return float(value)


def first(mapping, names, default=None):
    return next((mapping[name] for name in names if mapping.get(name) is not None), default)


def timestamp(seconds):
    minutes, seconds = divmod(max(0., float(seconds)), 60)
    return f'{int(minutes):02}:{seconds:05.2f}'


def local_file(folder, value, label):
    value = str(value)
    if urlsplit(value).scheme or '\\' in value:
        raise ValueError(label + '须使用相对路径')
    relative = Path(unquote(value))
    target = (folder / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(folder.resolve()) or target == folder.resolve():
        raise ValueError(label + '须位于演示文件夹内')
    if not target.is_file():
        raise ValueError(label + '不存在：' + value)
    return relative.as_posix()


def probe_video(path):
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise RuntimeError('未找到制作演示时使用的 ffprobe')
    result = subprocess.run([ffprobe, '-v', 'error', '-show_entries',
        'format=duration:stream=codec_type,width,height,avg_frame_rate', '-of', 'json', str(path)],
        check=True, capture_output=True, text=True, encoding='utf-8')
    payload = json.loads(result.stdout)
    stream = next((item for item in payload['streams'] if item.get('codec_type') == 'video'), None)
    if stream is None:
        raise ValueError('演示文件没有视频轨道')
    numerator, denominator = (float(value) for value in stream['avg_frame_rate'].split('/'))
    if denominator <= 0:
        raise ValueError('演示视频帧率无效')
    return dict(duration_seconds=float(payload['format']['duration']), fps=numerator / denominator,
                width=int(stream['width']), height=int(stream['height']),
                audio_present=any(item.get('codec_type') == 'audio' for item in payload['streams']))


def normalize(metadata, media, folder):
    raw = metadata if isinstance(metadata, list) else metadata.get('chapters', [])
    if not isinstance(raw, list) or not raw:
        raise ValueError('chapters.json 至少需要一个章节')
    chapters = []
    identities = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError('章节须为对象')
        start = number(first(entry, ('start', 'start_seconds', 'time_s', 'seconds', 'time')), '章节起点')
        if start >= media['duration_seconds'] or (chapters and start < chapters[-1]['start']):
            raise ValueError('章节须按起点顺序排列，且起点须早于视频结束')
        identity = str(entry.get('id') or f'chapter-{index + 1}')
        if identity in identities:
            raise ValueError('章节编号不能重复')
        identities.add(identity)
        title = str(entry.get('title') or entry.get('label') or f'章节 {index + 1}')
        features = entry.get('features', [])
        if isinstance(features, str):
            features = [features]
        if not isinstance(features, list):
            raise ValueError('章节功能须为列表')
        item = dict(id=identity, start=start, title=title,
                    description=str(entry.get('description') or entry.get('note') or ''),
                    features=[str(value) for value in features if str(value).strip()])
        if entry.get('keyframe'):
            item['keyframe'] = local_file(folder, entry['keyframe'], '章节关键帧')
        chapters.append(item)
    for index, chapter in enumerate(chapters):
        chapter['end'] = chapters[index + 1]['start'] if index + 1 < len(chapters) else media['duration_seconds']
    by_id = {chapter['id']: index for index, chapter in enumerate(chapters)}
    coverage = []
    declared = metadata.get('coverage', []) if isinstance(metadata, dict) else []
    if declared:
        if not isinstance(declared, list):
            raise ValueError('功能覆盖须为列表')
        for entry in declared:
            if not isinstance(entry, dict):
                raise ValueError('功能覆盖须为对象')
            identity = first(entry, ('chapter', 'chapter_id'))
            if identity not in by_id:
                raise ValueError('功能覆盖引用了不存在的章节')
            coverage.append(dict(feature=str(entry.get('feature') or entry.get('title') or ''),
                                 chapter=by_id[identity], note=str(entry.get('note') or '')))
    else:
        covered = set()
        for index, chapter in enumerate(chapters):
            for feature in chapter['features']:
                if feature not in covered:
                    coverage.append(dict(feature=feature, chapter=index, note=chapter['description']))
                    covered.add(feature)
    return chapters, coverage


STYLE = """
:root{color-scheme:dark;font-family:'Microsoft YaHei','Noto Sans CJK SC',sans-serif;background:#111a21;color:#eef4f6}
*{box-sizing:border-box}body{margin:0;padding:26px 20px 50px}main{max-width:1360px;margin:auto}
h1{font-size:27px;margin:0 0 12px}h2{font-size:19px;margin:0 0 14px}p{color:#b9c9d1;line-height:1.75;margin:8px 0}
a{color:#8edcd6;text-underline-offset:4px}button,select{font:inherit;color:inherit;background:#223441;border:1px solid #49616f;border-radius:5px;padding:8px 11px}
button{cursor:pointer}button:hover,button:focus-visible,select:focus-visible{background:#2d4856;outline:2px solid #86d8d0;outline-offset:2px}
button:disabled{opacity:.45;cursor:default}video{display:block;width:100%;max-height:76vh;aspect-ratio:16/9;background:#070d12;border-radius:6px}
.player{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:18px;margin-top:20px}.controls{display:flex;align-items:center;flex-wrap:wrap;gap:9px;margin-top:12px}
.timeline{font-variant-numeric:tabular-nums;color:#bdd5df;margin-left:auto}.chapters{max-height:calc(76vh + 62px);overflow:auto;background:#192833;border:1px solid #324957;border-radius:7px;padding:14px}
.chapter{display:block;width:100%;text-align:left;border:1px solid transparent;background:transparent;margin:3px 0;padding:10px 8px;line-height:1.5}
.chapter[aria-current='true']{border-color:#78cbc3;background:#25474b}.chapter small{display:block;color:#aec2cc;font-size:12px;margin:3px 0 0}
.chapter-time{color:#8edcd6;font-size:12px;font-variant-numeric:tabular-nums;display:block}.metadata{font-size:14px;color:#9fb6c2}.footer-links{display:flex;flex-wrap:wrap;gap:18px;margin:17px 0}
details{border:1px solid #324957;background:#192833;border-radius:7px;padding:16px;margin-top:20px}summary{cursor:pointer;font-size:17px}table{width:100%;border-collapse:collapse;margin-top:15px}
th,td{text-align:left;vertical-align:top;border-bottom:1px solid #324957;padding:11px 9px;font-size:14px;line-height:1.6}th{color:#d9e9ee}td:nth-child(2){width:33%}.jump{padding:5px 8px;text-align:left;font-size:13px}
.gallery{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;margin-top:18px}.gallery img{display:block;width:100%;height:auto;border-radius:4px}figure{margin:0}figcaption{font-size:13px;color:#aec2cc;line-height:1.6;margin-top:6px}
.hint{font-size:13px;color:#99afba}.warning{color:#e5c38c}kbd{font:inherit;color:#dae9ed;border:1px solid #48606e;border-radius:3px;padding:0 4px}
@media(max-width:1000px){.player{grid-template-columns:1fr}.chapters{max-height:340px}.gallery{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){body{padding:20px 10px 35px}h1{font-size:23px}.gallery{grid-template-columns:1fr}.timeline{width:100%;margin-left:0}th,td{padding:8px 5px}.controls button{padding:8px}}
"""


JAVASCRIPT = r"""
const data=JSON.parse(document.getElementById('chapter-data').textContent);
const film=document.getElementById('film');
const fps=data.media.fps;
const chapters=data.chapters;
const stamp=value=>{const n=Math.max(0,Number.isFinite(value)?value:0);return String(Math.floor(n/60)).padStart(2,'0')+':'+(n%60).toFixed(2).padStart(5,'0')};
const time=document.getElementById('time');
const play=document.getElementById('play');
const chapterButtons=[...document.querySelectorAll('.chapter')];
const duration=()=>Number.isFinite(film.duration)?film.duration:data.media.duration_seconds;
function update(){
  const now=film.currentTime||0;time.textContent=stamp(now)+' / '+stamp(duration());
  play.textContent=film.paused?'播放':'暂停';
  let active=0;for(let i=0;i<chapters.length;i++){if(now+1e-3>=chapters[i].start)active=i;else break}
  chapterButtons.forEach((button,index)=>button.setAttribute('aria-current',String(index===active)));
  document.getElementById('previous-frame').disabled=film.readyState===0||now<=0;
  document.getElementById('next-frame').disabled=film.readyState===0||now>=duration()-1/fps;
}
function jump(index){film.currentTime=chapters[index].start;update();film.focus()}
function step(direction){film.pause();const index=Math.round(film.currentTime*fps)+direction;film.currentTime=Math.max(0,Math.min(duration()-1/fps,index/fps));update()}
play.addEventListener('click',()=>{if(film.paused)film.play().catch(()=>{});else film.pause()});
document.getElementById('previous-frame').addEventListener('click',()=>step(-1));
document.getElementById('next-frame').addEventListener('click',()=>step(1));
document.getElementById('speed').addEventListener('change',event=>{film.playbackRate=Number(event.target.value)});
document.querySelectorAll('[data-chapter]').forEach(button=>button.addEventListener('click',()=>jump(Number(button.dataset.chapter))));
for(const event of ['timeupdate','loadedmetadata','seeked','play','pause','ended'])film.addEventListener(event,update);
film.addEventListener('error',()=>{document.getElementById('media-error').hidden=false});
document.addEventListener('keydown',event=>{
  if(event.target.closest('select,input,textarea')||event.ctrlKey||event.altKey||event.metaKey)return;
  if(event.key==='['||event.key===']'){event.preventDefault();step(event.key==='['?-1:1)}
});
update();
"""


class LocalLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.ids = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href', 'poster') and value and not value.startswith(('#', 'data:')):
                self.links.append(value)
            if key == 'id':
                self.ids.append(value)
