"""Mix the ML programme: music around the live match, one SE per FIFO start.

The renderer's verification JSON is the timing authority. This tool never opens
an audio device and keeps the original encoded video stream unchanged.
"""
from __future__ import annotations

import argparse
from array import array
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
import postmatch_audio as audio
from riichi_audio import RIICHI_SOUND_PATH, prepare_riichi_sound
from programme_audio_tools import (RATE, CHANNELS, encoded_audio_metrics,
    file_sha, inspect_music_join, pcm_info, probe_video, read_pcm, run,
    video_stream_hash)


def validate_schedule(data):
    """Reject music in a live hand and duplicate/overlapping declarations."""
    duration = float(data['duration_seconds'])
    live = tuple(float(value) for value in data['live_interval'])
    post = float(data['postmatch_start_seconds'])
    intervals = [tuple(float(value) for value in span) for span in data['music_intervals']]
    cues = [dict(cue) for cue in data['riichi_effect_starts']]
    values = [duration, post, *live, *(value for span in intervals for value in span)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Non-finite programme timing.')
    if len(live) != 2 or not 0 <= live[0] < live[1] == post < duration:
        raise ValueError('Invalid live/post-match boundary.')
    if not intervals:
        raise ValueError('Opening/post-match music intervals are required.')
    previous = -1.
    for start, end in intervals:
        if not 0 <= start < end <= duration or start < previous:
            raise ValueError('Music intervals must be ordered, disjoint and inside the video.')
        if max(start, live[0]) < min(end, live[1]):
            raise ValueError('Background music cannot overlap the live match.')
        previous = end
    ids = set(); previous_end = live[0]
    for cue in cues:
        identity = cue['sequence_id']; start = float(cue['time_s'])
        length = float(cue.get('duration_seconds', 2.5))
        if isinstance(identity, bool) or not isinstance(identity, int) or identity <= 0 or identity in ids:
            raise ValueError('Each actual declaration must have a unique positive sequence ID.')
        if not math.isfinite(start) or length != 2.5 or start < previous_end-1e-8 or start+length > live[1]:
            raise ValueError('Declarations must follow the actual nonoverlapping FIFO starts.')
        ids.add(identity); previous_end = start+length
    chart, stats = float(data.get('chart_hold', 15.)), float(data.get('stats_hold', 15.))
    if post+audio.soundtrack_duration(chart, stats) > duration+1/RATE:
        raise ValueError('The post-match effect track exceeds the programme.')
    return dict(duration=duration, live=live, intervals=intervals, cues=cues,
                post=post, chart_hold=chart, stats_hold=stats)


def music_segments(intervals):
    return [dict(start=round(start*RATE), end=round(end*RATE),
                 attack=min(round(.4*RATE), round((end-start)*RATE)),
                 release=min(round((.5 if index == len(intervals)-1 else .4)*RATE),
                             round((end-start)*RATE)))
            for index, (start, end) in enumerate(intervals)]


def render_phase_mix(music, effects, riichi, output, schedule, ffmpeg=None):
    ffmpeg = ffmpeg or shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is required.')
    for path in (music, effects, riichi): pcm_info(path)
    total = round(schedule['duration']*RATE)
    segments = music_segments(schedule['intervals']); cues = schedule['cues']
    filters = []; mixed = []
    filters.append('[0:a]asplit='+str(len(segments))+''.join(f'[ms{i}]' for i in range(len(segments))))
    for index, segment in enumerate(segments):
        length = segment['end']-segment['start']; label = f'm{index}'
        filters.append(f'[ms{index}]atrim=end_sample={length},asetpts=PTS-STARTPTS,'
            f'afade=t=in:ss=0:ns={segment["attack"]},'
            f'afade=t=out:ss={length-segment["release"]}:ns={segment["release"]},'
            f'adelay={segment["start"]}S:all=1[{label}]')
        mixed.append(label)
    filters.append(f'[1:a]adelay={round(schedule["post"]*RATE)}S:all=1[post]'); mixed.append('post')
    if cues:
        filters.append('[2:a]asplit='+str(len(cues))+''.join(f'[rs{i}]' for i in range(len(cues))))
        for index, cue in enumerate(cues):
            label = f'r{index}'
            filters.append(f'[rs{index}]adelay={round(cue["time_s"]*RATE)}S:all=1[{label}]')
            mixed.append(label)
    else:
        filters.append('[2:a]anullsink')
    filters.append(''.join(f'[{label}]' for label in mixed)+
        f'amix=inputs={len(mixed)}:duration=longest:dropout_transition=0:normalize=0,'
        f'apad=whole_len={total},atrim=end_sample={total}[mixed]')
    run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
         '-stream_loop', '-1', '-i', music, '-i', effects, '-i', riichi,
         '-filter_complex', ';'.join(filters), '-map', '[mixed]', '-c:a', 'pcm_s16le',
         '-ar', str(RATE), '-ac', '2', output], timeout=max(180, schedule['duration']))
    if pcm_info(output)['frames'] != total:
        raise RuntimeError('Mixed PCM duration differs from the video timeline.')
    return Path(output)


def inspect_phase_mix(mixed, music, effects, riichi, schedule):
    """Independently compare source samples, including every dry live sample."""
    info = pcm_info(mixed); loop = read_pcm(music); post = read_pcm(effects); declaration = read_pcm(riichi)
    segments = music_segments(schedule['intervals'])
    cue_starts = [round(cue['time_s']*RATE) for cue in schedule['cues']]
    post_start = round(schedule['post']*RATE)
    live_start, live_end = (round(value*RATE) for value in schedule['live'])
    offset = peak = clipped = maximum_error = compared = dry_samples = dry_peak = 0
    live_cue_samples = 0; sum_squares = 0
    with wave.open(str(mixed), 'rb') as stream:
        while payload := stream.readframes(RATE):
            samples = array('h'); samples.frombytes(payload)
            if sys.byteorder != 'little': samples.byteswap()
            for index, value in enumerate(samples):
                absolute = offset+index; frame, channel = divmod(absolute, CHANNELS)
                magnitude = abs(value); peak = max(peak, magnitude)
                clipped += magnitude >= 32767; sum_squares += value*value
                expected = 0; fading = False
                for segment in segments:
                    if segment['start'] <= frame < segment['end']:
                        local = frame-segment['start']
                        expected += loop[(local*CHANNELS+channel) % len(loop)]
                        fading = local < segment['attack'] or frame >= segment['end']-segment['release']
                local = (frame-post_start)*CHANNELS+channel
                if 0 <= local < len(post): expected += post[local]
                effect = 0
                for start in cue_starts:
                    local = (frame-start)*CHANNELS+channel
                    if 0 <= local < len(declaration): effect += declaration[local]
                expected += effect
                if not fading:
                    maximum_error = max(maximum_error, abs(value-expected)); compared += 1
                if live_start <= frame < live_end:
                    if effect == 0:
                        dry_samples += 1; dry_peak = max(dry_peak, magnitude)
                    else: live_cue_samples += 1
            offset += len(samples)
    rms = math.sqrt(sum_squares/max(1, offset))/32768.
    report = dict(frames=info['frames'], peak=peak/32768., clipped_samples=clipped,
        rms_dbfs=20*math.log10(rms) if rms else None,
        compared_source_samples_excluding_music_fades=compared,
        source_sum_max_error_lsb=maximum_error, dry_live_samples=dry_samples,
        dry_live_peak_pcm_lsb=dry_peak, audible_live_cue_samples=live_cue_samples,
        live_has_only_declared_riichi_effects=maximum_error <= 1 and dry_peak == 0,
        riichi_actual_start_count=len(cue_starts), no_repeated_or_extra_effects=maximum_error <= 1)
    if clipped or peak > round(.95*32768)+1 or maximum_error > 1 or dry_peak:
        raise RuntimeError('Programme PCM verification failed: '+json.dumps(report))
    return report


def mix_programme(source, output, timeline, proof_path=None, overwrite=False, *, music=None):
    source = Path(source).resolve(strict=True); output = Path(output).resolve()
    timeline = Path(timeline).resolve(strict=True)
    proof_path = Path(proof_path).resolve() if proof_path else output.parent/'analysis/programme-audio-proof.json'
    music_source = Path(music).resolve(strict=True) if music else None
    if source == output or output.suffix.lower() != '.mp4' or len({source, output, timeline, proof_path}) != 4:
        raise ValueError('Source, output, timeline and proof must be distinct files; output must be MP4.')
    if music_source in (output, proof_path):
        raise ValueError('Choose a different path for the background music.')
    if not overwrite and (output.exists() or proof_path.exists()):
        raise FileExistsError('An output already exists; use --overwrite deliberately.')
    data = json.loads(timeline.read_text(encoding='utf-8-sig')); schedule = validate_schedule(data)
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe: raise RuntimeError('ffmpeg and ffprobe are required.')
    source_probe, duration, fps = probe_video(source, ffprobe)
    if any(stream['codec_type'] == 'audio' for stream in source_probe['streams']):
        raise ValueError('The input must be the silent render.')
    if abs(duration-schedule['duration']) > 1/fps:
        raise ValueError('The verification timeline belongs to a different video.')
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.stem+'.'+uuid.uuid4().hex+'.tmp.mp4')
    try:
        with tempfile.TemporaryDirectory(prefix='ml-audio-', dir=output.parent) as temporary:
            folder = Path(temporary)
            player = audio.PostMatchAudio(cache_dir=folder/'post')
            player.prepare(chart_hold=schedule['chart_hold'], stats_hold=schedule['stats_hold'],
                           music_path=music_source, music_volume=.35, sfx_volume=.7)
            if player.error or player.path is None:
                raise RuntimeError(player.error or 'Missing post-match audio.')
            # Keep the same mixer inputs for effects-only renders.
            music_track = player.music_path or audio._write_pcm(folder/'silence.wav', [0] * RATE * CHANNELS)
            riichi = prepare_riichi_sound(.7, folder/'riichi')
            mixed = render_phase_mix(music_track, player.path, riichi, folder/'programme.wav', schedule, ffmpeg)
            pcm_proof = inspect_phase_mix(mixed, music_track, player.path, riichi, schedule)
            source_hash = video_stream_hash(source, ffmpeg)
            run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-i', source, '-i', mixed,
                 '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
                 '-b:a', '192k', '-ar', str(RATE), '-ac', '2', '-movflags', '+faststart', pending])
            output_hash = video_stream_hash(pending, ffmpeg)
            if source_hash != output_hash: raise RuntimeError('Muxing changed the encoded video.')
            run([ffmpeg, '-nostdin', '-v', 'error', '-i', pending, '-f', 'null', '-'], timeout=300)
            final_probe, final_duration, _ = probe_video(pending, ffprobe)
            tracks = [stream for stream in final_probe['streams'] if stream['codec_type'] == 'audio']
            if len(tracks) != 1 or tracks[0]['channels'] != 2 or int(tracks[0]['sample_rate']) != RATE:
                raise RuntimeError('Expected one 48 kHz stereo audio stream.')
            post_cues = [dict(name=cue.name, time_s=schedule['post']+cue.time, repeat_count=1)
                         for cue in audio.sound_cues(schedule['chart_hold'])]
            proof = dict(status='PASS', input_video=str(source), output_video=str(output),
                input_file_sha256=file_sha(source), timeline_sha256=file_sha(timeline),
                duration_seconds=schedule['duration'], output_duration_seconds=final_duration,
                music_intervals=data['music_intervals'] if music_source else [], live_interval=data['live_interval'],
                riichi_effect_starts=data['riichi_effect_starts'], postmatch_cues=post_cues,
                riichi_asset_sha256=file_sha(RIICHI_SOUND_PATH),
                riichi_source_note='Original synthesized cue.',
                riichi_source_metadata=json.loads((RIICHI_SOUND_PATH.parent/'sources.json').read_text(encoding='utf8')),
                music_label=music_source.name if music_source else None, pcm_verification=pcm_proof,
                music_loop_join_verification=inspect_music_join(music_track, music_source, .35) if music_source else None,
                programme_pcm=pcm_info(mixed), aac_decoded_metrics=encoded_audio_metrics(pending, ffmpeg),
                video_stream_unchanged=True, input_video_stream_sha256=source_hash,
                output_video_stream_sha256=output_hash, output_full_decode_passed=True,
                output_audio_stream=tracks[0],
                source_files={str(path.relative_to(ROOT)): file_sha(path) for path in
                    (Path(__file__), ROOT/'src/riichi_audio.py', ROOT/'src/postmatch_audio.py',
                     audio.ASSET_DIR/'sources.json')})
        os.replace(pending, output); proof['output_file_sha256'] = file_sha(output)
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        proof_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        return proof
    finally:
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeline', required=True, type=Path)
    parser.add_argument('--proof', type=Path)
    parser.add_argument('--music', type=Path, help='Optional local background track for opening and ending')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    result = mix_programme(args.input, args.output, args.timeline, args.proof, args.overwrite, music=args.music)
    print(json.dumps({key: result[key] for key in ('status', 'duration_seconds', 'video_stream_unchanged',
        'output_full_decode_passed', 'pcm_verification')}, ensure_ascii=False))


if __name__ == '__main__': main()
