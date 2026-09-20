#!/usr/bin/env python3
"""Build a jy14-headless-plan/v1 for an AI-image narrated documentary video.

Reusable template distilled from the 康熙红票 project: still-image slideshow
(Ken Burns pan/zoom) synced to pre-recorded narration by real ASR alignment,
not guessed reading speed. See references/ai-narrated-image-video.md for the
full workflow this script is one step of.

Usage:
    python3 build_narrated_image_plan.py --spec spec.json --out plan.json

See references/ai-narrated-image-video.md for the spec.json schema and the
end-to-end workflow (recording narration, extracting it, running this script,
build/publish).
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from align_audio import line_boundary_times  # noqa: E402

FPS_DEFAULT = 30


def require(value, message):
    if not value:
        raise SystemExit(message)


def read_lines(path):
    with open(path, encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


def probe_duration_us(wav_path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of',
         'default=noprint_wrappers=1:nokey=1', wav_path],
        capture_output=True, text=True, check=True).stdout.strip()
    return round(float(out) * 1_000_000)


def ensure_whisper_json(wav_path, whisper_json_path, model_name='small', language='zh'):
    """Transcribe wav_path with word timestamps if whisper_json_path is missing."""
    if os.path.isfile(whisper_json_path):
        return
    import whisper  # local import: heavy dependency, only needed on cache miss
    model = whisper.load_model(model_name)
    result = model.transcribe(wav_path, language=language, word_timestamps=True, verbose=False)
    with open(whisper_json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def frame_round(us, fps):
    return round(round(us * fps / 1_000_000) * 1_000_000 / fps)


def durations_from_whisper(text_lines, whisper_json_path, raw_total_us, target_total_us, fps,
                            min_coverage=0.85):
    n_lines = len(text_lines)
    speed = raw_total_us / target_total_us
    times_s, ref_text, rec_text, matched = line_boundary_times(text_lines, whisper_json_path)
    coverage = matched / max(1, len(ref_text))
    require(coverage >= min_coverage,
            f'Whisper alignment coverage too low for {whisper_json_path}: {coverage:.1%}. '
            'The reference text probably does not match what was actually read aloud - '
            'use the exact narration text (including any ad-lib edits), not an earlier draft.')
    raw_bounds = [0] + [round(t * 1_000_000) for t in times_s] + [raw_total_us]
    require(len(raw_bounds) == n_lines + 1, 'Boundary count mismatch')
    target_bounds_frames = [round((b / speed) * fps / 1_000_000) for b in raw_bounds]
    target_bounds_frames[0] = 0
    target_bounds_frames[-1] = round(target_total_us * fps / 1_000_000)
    for i in range(1, len(target_bounds_frames)):
        if target_bounds_frames[i] <= target_bounds_frames[i - 1]:
            target_bounds_frames[i] = target_bounds_frames[i - 1] + 1
    cum_us = [round(f * 1_000_000 / fps) for f in target_bounds_frames]
    us = [cum_us[i + 1] - cum_us[i] for i in range(n_lines)]
    drift = target_total_us - sum(us)
    us[-1] += drift
    return us, coverage


# Ken Burns pattern library. Every pan magnitude here is paired with enough
# scale headroom that panning never reveals a black edge (margin = (scale-1)/2
# on the tightest side must stay comfortably above the pan magnitude - this
# was the exact bug reported and fixed on 康熙红票: small-scale pure pans
# showed letterboxing). Cycled by scene index; special images can override.
KEN_BURNS = [
    {"scale": [1.05, 1.20], "x": [0.00, -0.065], "y": [0.00, 0.00]},
    {"scale": [1.20, 1.05], "x": [-0.065, 0.00], "y": [0.00, 0.00]},
    {"scale": [1.25, 1.35], "x": [-0.09, 0.09], "y": [0.00, 0.00]},
    {"scale": [1.35, 1.25], "x": [0.09, -0.09], "y": [0.00, 0.00]},
    {"scale": [1.14, 1.26], "x": [0.00, 0.00], "y": [0.045, -0.045]},
    {"scale": [1.26, 1.14], "x": [0.00, 0.00], "y": [-0.045, 0.045]},
    {"scale": [1.18, 1.32], "x": [0.06, -0.06], "y": [0.03, -0.03]},
    {"scale": [1.32, 1.18], "x": [-0.06, 0.06], "y": [-0.03, 0.03]},
]

# Transition rhythm: only 'dissolve' is a captured/verified native resource in
# this repo (see engine/native-resource-catalog.json). Cut variety instead
# comes from alternating hard cuts with short/long dissolves.
TRANSITION_CYCLE = [None, ('dissolve', 10), ('dissolve', 16)]  # None = hard cut; frames


def numeric_sort_key(path):
    m = re.search(r'\((\d+)\)', os.path.basename(path))
    return int(m.group(1)) if m else 0


def insert_special(scene_list, target_idx, photo_path, keyframes=None):
    """Insert a special image at the scene covering target_idx, splitting a
    straddling bucket instead of relocating it whole (the exact bug that
    misplaced the 利玛窦/Clement_XI portraits before this was fixed)."""
    for pos, (img, blines, kf) in enumerate(scene_list):
        before = [li for li in blines if li < target_idx]
        after = [li for li in blines if li > target_idx]
        if before and after:
            scene_list[pos:pos + 1] = [(img, before, kf), (photo_path, [target_idx], keyframes), (img, after, kf)]
            return
        if blines and min(blines) > target_idx:
            scene_list.insert(pos, (photo_path, [target_idx], keyframes))
            return
    scene_list.append((photo_path, [target_idx], keyframes))


def build_plan(spec):
    materials_dir = spec['materials_dir']
    fps = spec.get('canvas', {}).get('fps', FPS_DEFAULT)
    canvas = spec.get('canvas', {'width': 1920, 'height': 1080, 'fps': fps})

    # 1. Load narration blocks and run/reuse whisper alignment for each.
    all_lines = []
    per_line_us = []
    for block in spec['blocks']:
        text_path = os.path.join(materials_dir, block['text_file']) if not os.path.isabs(block['text_file']) else block['text_file']
        wav_path = os.path.join(materials_dir, block['wav_file']) if not os.path.isabs(block['wav_file']) else block['wav_file']
        whisper_path = block.get('whisper_json') or (os.path.splitext(wav_path)[0] + '.whisper.json')
        block_lines = read_lines(text_path)
        raw_total_us = probe_duration_us(wav_path)
        target_total_us = block.get('target_duration_us')
        require(target_total_us, f'block for {wav_path} needs target_duration_us '
                '(the actual timeline duration Jianying gave this clip after applying its speed factor)')
        ensure_whisper_json(wav_path, whisper_path, model_name=spec.get('whisper_model', 'small'))
        us, coverage = durations_from_whisper(block_lines, whisper_path, raw_total_us, target_total_us, fps)
        print(f'[align] {block["text_file"]}: {len(block_lines)} lines, coverage {coverage:.1%}', file=sys.stderr)
        all_lines.extend(block_lines)
        per_line_us.extend(us)
        block['_wav_path'] = wav_path
        block['_raw_total_us'] = raw_total_us
        block['_target_total_us'] = target_total_us

    # 2. Resolve special (real-photo) insertions by exact text match.
    specials = spec.get('special_images', [])
    special_line_indices = {}
    for s in specials:
        match_lines = s.get('match_lines') or [s['match_line']]
        for ml in match_lines:
            idx = all_lines.index(ml)  # raises if not found - fail loudly, don't guess
            special_line_indices.setdefault(id(s), []).append(idx)

    excluded = set(i for idxs in special_line_indices.values() for i in idxs)
    remaining_idx = [i for i in range(len(all_lines)) if i not in excluded]

    # 3. Distribute remaining lines proportionally across the generated images.
    gen_pattern = spec['generated_images']
    gen_files = sorted(glob.glob(os.path.join(materials_dir, gen_pattern['glob'])),
                        key=numeric_sort_key if gen_pattern.get('sort') == 'numeric-suffix' else None)
    require(gen_files, 'No generated images matched ' + gen_pattern['glob'])
    n_gen = len(gen_files)
    buckets = [[] for _ in range(n_gen)]
    for pos, li in enumerate(remaining_idx):
        b = pos * n_gen // len(remaining_idx)
        buckets[b].append(li)
    for b in buckets:
        require(b, f'A generated image got zero lines - use fewer images or more narration text '
                '({n_gen} images for {len(remaining_idx)} lines)')

    scenes = [(gen_files[i], buckets[i], None) for i in range(n_gen)]

    for s in specials:
        idxs = special_line_indices[id(s)]
        target_idx = idxs[0] if len(idxs) == 1 else idxs  # multi-line specials keep contiguous group
        photo = os.path.join(materials_dir, s['image']) if not os.path.isabs(s['image']) else s['image']
        kf = s.get('keyframes')
        if isinstance(target_idx, list):
            # contiguous multi-line special (e.g. an opening artifact photo spanning 2 lines)
            insert_special(scenes, target_idx[0], photo, kf)
            # replace the just-inserted single-line entry with the full group
            for pos, (img, blines, k) in enumerate(scenes):
                if img == photo and blines == [target_idx[0]]:
                    scenes[pos] = (photo, sorted(target_idx), k)
                    break
        else:
            insert_special(scenes, target_idx, photo, kf)

    seen = sorted(li for _, blines, _ in scenes for li in blines)
    require(seen == list(range(len(all_lines))), 'Every narration line must map to exactly one scene')

    # 4. Assemble video + audio tracks.
    video_track = {"type": "video", "name": spec.get('video_track_name', '主视频'), "segments": []}
    audio_track = {"type": "audio", "name": spec.get('audio_track_name', '旁白'), "segments": []}

    cursor = 0
    for block in spec['blocks']:
        audio_track['segments'].append({
            "source": block['_wav_path'], "start_us": cursor, "duration_us": block['_target_total_us'],
            "source_start_us": 0, "source_duration_us": block['_raw_total_us'],
            "speed": block['_raw_total_us'] / block['_target_total_us'], "volume": block.get('volume', 1.0),
        })
        cursor += block['_target_total_us']

    cursor = 0
    n_scenes = len(scenes)
    for si, (img_path, blines, kf_override) in enumerate(scenes):
        seg_dur = sum(per_line_us[li] for li in blines)
        pattern = kf_override or KEN_BURNS[si % len(KEN_BURNS)]
        seg = {
            "source": img_path, "start_us": cursor, "duration_us": seg_dur,
            "keyframes": {
                "scale": [{"at_us": 0, "value": pattern["scale"][0]}, {"at_us": seg_dur, "value": pattern["scale"][1]}],
                "x": [{"at_us": 0, "value": pattern["x"][0]}, {"at_us": seg_dur, "value": pattern["x"][1]}],
                "y": [{"at_us": 0, "value": pattern["y"][0]}, {"at_us": seg_dur, "value": pattern["y"][1]}],
            },
        }
        if si < n_scenes - 1:
            style = TRANSITION_CYCLE[si % len(TRANSITION_CYCLE)]
            if style is not None:
                name, n_frames = style
                seg["transition_out"] = {"name": name, "duration_us": round(n_frames * 1_000_000 / fps)}
        video_track['segments'].append(seg)
        cursor += seg_dur

    plan = {
        "schema": "jy14-headless-plan/v1",
        "name": spec['name'],
        "canvas": canvas,
        "tracks": [video_track, audio_track],
    }
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    spec = json.load(open(args.spec, encoding='utf-8'))
    plan = build_plan(spec)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    v = plan['tracks'][0]['segments']
    print(json.dumps({
        'status': 'plan-written', 'path': args.out, 'scenes': len(v),
        'total_duration_s': round(sum(s['duration_us'] for s in v) / 1e6, 2),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
