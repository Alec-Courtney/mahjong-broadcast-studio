"""Build the portable chapter player for the condensed, complete ML programme."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.programme_viewer_tools import (
    JAVASCRIPT, STYLE, LocalLinks, local_file, normalize, probe_video, timestamp,
)

DEFAULT_OUT = ROOT / 'previews/latest/programme'
PAGE = '打开完整演示.html'


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def build(folder):
    folder = Path(folder).resolve()
    metadata = json.loads((folder / 'chapters.json').read_text(encoding='utf-8-sig'))
    verification = json.loads((folder / 'verification.json').read_text(encoding='utf-8-sig'))
    if verification.get('video_rendered') is not True:
        raise ValueError('最终画面尚未完成')
    video = local_file(folder, metadata['video'], '演示视频')
    media = probe_video(folder / video)
    if not media['audio_present']:
        raise ValueError('最终音轨尚未合入')
    if (media['width'], media['height']) != (1920, 1080) or abs(media['fps'] - 60) > .01:
        raise ValueError('最终视频规格与演示不一致')
    if abs(media['duration_seconds'] - float(metadata['duration_seconds'])) >= 1 / media['fps']:
        raise ValueError('最终时长与章节记录不一致')
    if verification.get('real_ml_identity_data') is not True or verification.get('no_music_in_live_match') is not True:
        raise ValueError('缺少本地ML资料或对局配乐区间记录')
    chapters, coverage = normalize(metadata, media, folder)
    if not chapters or chapters[0]['start'] != 0:
        raise ValueError('章节必须从开场开始')
    poster = next((chapter['keyframe'] for chapter in chapters
                   if chapter['id'] == 'opening-title' and chapter.get('keyframe')), None)
    if not poster:
        raise ValueError('缺少赛季标题卡预览')
    esc = html.escape
    title = str(metadata.get('title', 'ML赛事 · 开场至结束（中段节选）'))
    live = verification['live_interval']
    cues = len(verification['riichi_effect_starts'])
    data_note = ('使用项目内置ML选手姓名、照片、队名与队色；对局操作及点数为模拟演示。'
                 '资料没有队徽时显示本队队名字标。')
    summary = '保留完整开场、代表性对局、终局结算和赛后展示；中段六局省略画面，仍按规则逐局结算。'
    audio_note = (f'{live[0]:g}–{live[1]:g}秒对局阶段没有背景音乐；'
                  f'{cues}项立直声明各配一次音效。开场及赛后配乐，赛后两张图各完整停留15秒。')
    buttons = []
    rows = []
    gallery = []
    for index, chapter in enumerate(chapters):
        buttons.append(f'<button class="chapter" data-chapter="{index}" aria-current="false">'
                       f'<span class="chapter-time">{timestamp(chapter["start"])}</span>'
                       f'{esc(chapter["title"])}<small>{esc(chapter["description"])}</small></button>')
        if chapter.get('keyframe'):
            keyframe = esc(chapter['keyframe'], quote=True)
            gallery.append(f'<figure><a href="{keyframe}"><img src="{keyframe}" loading="lazy" '
                           f'alt="{esc(chapter["title"], quote=True)}"></a><figcaption>'
                           f'{timestamp(chapter["start"])} · {esc(chapter["title"])}</figcaption></figure>')
    for entry in coverage:
        chapter = chapters[entry['chapter']]
        rows.append(f'<tr><td>{esc(entry["feature"])}</td><td><button class="jump" '
                    f'data-chapter="{entry["chapter"]}">{timestamp(chapter["start"])} · '
                    f'{esc(chapter["title"])}</button></td><td>{esc(entry["note"])}</td></tr>')
    payload = json.dumps(dict(chapters=chapters, media=media), ensure_ascii=False).replace('<', '\\u003c')
    javascript = JAVASCRIPT + r'''
const sound=document.getElementById('sound');
function soundLabel(){sound.textContent=film.muted?'开启声音并播放':'静音'}
sound.addEventListener('click',()=>{film.muted=!film.muted;if(!film.muted)film.play().catch(()=>{});soundLabel()});
film.addEventListener('volumechange',soundLabel);
film.muted=true;
film.play().catch(()=>{});
soundLabel();
'''
    page = f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{STYLE}</style>
<main><h1>{esc(title)}</h1><p>{esc(summary)}</p>
<p class="metadata">{media['width']} × {media['height']} · {media['fps']:g}fps · {timestamp(media['duration_seconds'])} · 正常动画速度</p>
<p class="hint">{esc(data_note)}<br>{esc(audio_note)}</p>
<div class="player"><section aria-label="完整演示播放器">
<video id="film" controls playsinline autoplay muted preload="metadata" src="{esc(video, quote=True)}" poster="{esc(poster, quote=True)}"></video>
<div class="controls"><button id="play">播放</button><button id="sound">开启声音并播放</button>
<button id="previous-frame" title="快捷键 [">上一帧</button><button id="next-frame" title="快捷键 ]">下一帧</button>
<label>速度 <select id="speed" aria-label="播放速度"><option value="0.5">0.5 倍</option><option value="1" selected>1 倍</option><option value="1.5">1.5 倍</option></select></label>
<output id="time" class="timeline">00:00.00</output></div>
<p class="hint">默认静音自动播放，点击“开启声音并播放”试听。暂停后可逐帧查看；键盘 <kbd>[</kbd>／<kbd>]</kbd> 切换上一帧／下一帧。浏览器未自动播放时，点击播放即可。</p>
<p id="media-error" class="warning" hidden>视频未能读取。请保持播放页与视频的相对位置，或直接打开下方的视频文件。</p>
</section><aside class="chapters" aria-label="演示章节"><h2>按章节查看</h2>{''.join(buttons)}</aside></div>
<div class="footer-links"><a href="{esc(video, quote=True)}">单独打开视频</a><a href="chapters.json">章节记录</a><a href="verification.json">生成记录</a><a href="ML_演示数据.json">完整演示赛事数据</a></div>
<details><summary>功能覆盖（{len(coverage)} 项）</summary><table><thead><tr><th>功能</th><th>所在章节</th><th>说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></details>
<details><summary>关键帧</summary><div class="gallery">{''.join(gallery)}</div></details>
<p class="hint">立直音效由原参考录音保留扫掠声和瞬态制成，是原录音衍生近似，不是官方独立音轨。复制整个文件夹可继续离线播放。</p>
</main><script id="chapter-data" type="application/json">{payload}</script><script>{javascript}</script></html>
'''
    parser = LocalLinks()
    parser.feed(page)
    for link in parser.links:
        local_file(folder, link, '播放页链接')
    if len(parser.ids) != len(set(parser.ids)):
        raise ValueError('播放页含重复控件编号')
    (folder / PAGE).write_text(page, encoding='utf-8')
    readme = [f'# {title}', '', f'[打开章节播放器]({PAGE}) · [直接播放视频]({video})', '',
              summary, '', f'{media["width"]} × {media["height"]}，{media["fps"]:g}fps，{timestamp(media["duration_seconds"])}。', '',
              data_note, '', audio_note, '',
              '听牌余枚只演示一次5→4；收支数字在身份渐变区域垂直、等尺寸汇入。', '',
              '播放器默认静音自动播放，点击“开启声音并播放”试听；支持全部章节、0.5／1／1.5倍与逐帧。', '',
              '立直音效来自原参考录音的扫掠声及瞬态，是原录音衍生近似，仍不称为官方独立音轨。', '',
              '## 章节', '', '| 起点 | 内容 |', '| --- | --- |']
    readme.extend(f'| {timestamp(chapter["start"])} | {chapter["title"].replace("|", "／")} |' for chapter in chapters)
    readme.extend(['', '完整赛事JSON内含本片使用的素材，可在独立项目导入；正式赛事数据未被修改。', '',
                   '复制时保留视频、播放页、JSON及关键帧的相对位置。核验记录在 `analysis/`。', ''])
    (folder / 'README.md').write_text('\n'.join(readme), encoding='utf-8')
    report = dict(status='PASS', video=video, video_sha256=sha(folder / video), media=media,
                  chapters=len(chapters), chapter_starts=[chapter['start'] for chapter in chapters],
                  coverage_items=len(coverage), local_links=len(parser.links), missing_links=[],
                  duplicate_ids=[], poster=poster, embedded_metadata=True, local_fetch_required=False,
                  external_dependencies=[], muted_autoplay=True, one_click_sound=True,
                  input_sha256={name: sha(folder / name) for name in ('chapters.json', 'verification.json')})
    (folder / 'analysis').mkdir(exist_ok=True)
    (folder / 'analysis/viewer-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = build(args.output_dir)
    print(json.dumps(result, ensure_ascii=True))
