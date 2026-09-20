#!/usr/bin/env python3
"""Build a jy14-headless-plan/v1 for an AI-image narrated documentary video.

Reusable template distilled from the 康熙红票 project: still-image slideshow
(Ken Burns pan/zoom) synced to pre-recorded narration by real ASR alignment,
not guessed reading speed. House style is a restrained, "成熟历史纪录片"
look - see HOUSE_STYLE below and references/ai-narrated-image-video.md for
the full workflow this script is one step of.

Usage:
    python3 build_narrated_image_plan.py --spec spec.json --out plan.json
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from align_audio import line_boundary_times, keyword_span  # noqa: E402

FPS_DEFAULT = 30

# House style, fixed by explicit user request - do not loosen these without
# being asked again:
#   - restrained "mature documentary" motion only: slow push-in, slow
#     pull-out, slow left<->right pan, alternated - no diagonal/vertical
#     drift, no fast/flashy movement.
#   - every adjacent pair crossfades (no hard cuts) at a fixed duration.
#   - no filters, visual effects, or text-effects: Ken Burns + dissolve only.
#   - subtitles (when enabled): white, dark stroke, positioned low but not
#     hugging the bottom edge, max ~2 lines per cue. The engine has no bold/
#     font-weight field (checked engine/jy14_headless.py and
#     native_motion.py) - "bold" is approximated with a heavier border_width
#     and slightly larger size, not true font weight.
#   - BGM (when enabled) is volume-ducked below narration automatically.
HOUSE_STYLE = {
    'transition_seconds': 0.25,
    'subtitle': {
        'color': '#FFFFFF', 'border_color': '#000000', 'border_width': 0.09,
        'size': 8, 'y': -0.72, 'max_lines': 2,
    },
    'bgm_volume_ratio': 0.18,  # BGM volume = narration volume * this ratio, unless spec overrides
    'title_card': {
        # Fixed opening title card: a small series tag above a larger episode
        # title, both centered in the upper-middle of frame, held static
        # (no pop) over the first scene(s). Gold-on-dark, matching a
        # reference screenshot the user provided.
        'duration_us': 3_500_000,
        'series_size': 8, 'series_y': 0.30, 'series_color': '#D9B36C',
        'series_border_color': '#000000', 'series_border_width': 0.06,
        'title_size': 18, 'title_y': 0.16, 'title_color': '#F0CE80',
        'title_border_color': '#000000', 'title_border_width': 0.08,
    },
    'callout_pop': {
        # Keyword callout (separate "关键词" text track, not the subtitle
        # track). Text keyframes only support x/y/scale/rotation (no
        # opacity), so callouts can't fade - they appear via a motion pop and
        # disappear by simply ending when their segment's duration_us is up.
        # Multiple distinct styles cycle by default so repeated callouts
        # don't all look the same; "style" can be forced per-entry in spec.
        'default_hold_us': 1_200_000,
        'size': 14, 'x': 0.0, 'y': 0.08,
        'color': '#FFD24C', 'border_color': '#000000', 'border_width': 0.1,
        'style_cycle': ['stamp', 'swipe_in', 'wobble_rotate', 'drop_in'],
    },
}

# One keyframe-builder per callout style, tuned for a punchy "犀利锐评"
# commentary feel (fast, hard-hitting) rather than a cute UI bounce - this
# replaced an earlier single soft bounce style that read as too simple/toy-
# like for the tone. Each takes (pop_us, x, y) - the entry's resting x/y -
# and returns the keyframes dict. pop_us is the pop-in window; after it the
# callout holds at rest until the segment ends.
def _callout_keyframes(style, pop_us, x, y):
    if style == 'stamp':
        # hard, fast overshoot-and-settle - like a stamp slamming down
        q = pop_us / 4
        return {"scale": [
            {"at_us": 0, "value": 0.25},
            {"at_us": round(q), "value": 1.32},
            {"at_us": round(q * 2), "value": 0.94},
            {"at_us": round(q * 3), "value": 1.07},
            {"at_us": pop_us, "value": 1.0},
        ]}
    if style == 'swipe_in':
        # fast horizontal whip-in, like a title card swiping into frame
        return {
            "scale": [{"at_us": 0, "value": 0.9}, {"at_us": pop_us, "value": 1.0}],
            "x": [{"at_us": 0, "value": x - 0.3}, {"at_us": round(pop_us * 0.75), "value": x + 0.02},
                  {"at_us": pop_us, "value": x}],
        }
    if style == 'wobble_rotate':
        # snaps in slightly rotated then wobbles level - "knocked into place"
        q = pop_us / 3
        return {
            "scale": [{"at_us": 0, "value": 0.5}, {"at_us": round(q), "value": 1.15},
                      {"at_us": pop_us, "value": 1.0}],
            "rotation": [{"at_us": 0, "value": -10}, {"at_us": round(q), "value": 6},
                         {"at_us": round(q * 2), "value": -3}, {"at_us": pop_us, "value": 0}],
        }
    if style == 'drop_in':
        # drops from above with a hard landing bounce
        return {
            "scale": [{"at_us": 0, "value": 0.85}, {"at_us": round(pop_us * 0.7), "value": 1.1},
                      {"at_us": pop_us, "value": 1.0}],
            "y": [{"at_us": 0, "value": y + 0.16}, {"at_us": round(pop_us * 0.7), "value": y - 0.015},
                  {"at_us": pop_us, "value": y}],
        }
    raise SystemExit(f'Unknown callout style: {style!r} (known: stamp, swipe_in, wobble_rotate, drop_in)')


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


# Restrained documentary Ken Burns library: slow push-in, slow pull-out, and
# a left<->right pan, cycled in order. Magnitudes are deliberately subtle
# ("轻微慢推近/慢拉远/左右平移") - much gentler than a highlight-reel style.
# Pan magnitude is still kept well inside the scale headroom ((scale-1)/2) on
# every entry so panning never reveals a black edge - this was a real bug
# (see references/ai-narrated-image-video.md) and the margin must be kept
# even when these values are retuned.
KEN_BURNS = [
    {"scale": [1.00, 1.08], "x": [0.00, 0.00], "y": [0.00, 0.00]},   # slow push-in
    {"scale": [1.08, 1.00], "x": [0.00, 0.00], "y": [0.00, 0.00]},   # slow pull-out
    {"scale": [1.12, 1.18], "x": [-0.05, 0.05], "y": [0.00, 0.00]},  # slow left -> right pan
    {"scale": [1.18, 1.12], "x": [0.05, -0.05], "y": [0.00, 0.00]},  # slow right -> left pan
]


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


def wrap_two_lines(text, max_chars_per_line=16):
    """Best-effort split of one subtitle cue into at most 2 lines. This is a
    character-count heuristic, not real font-metric line breaking - if a
    line is still too long to read comfortably at the chosen canvas/size,
    shorten the source narration line instead of trusting this blindly."""
    if len(text) <= max_chars_per_line:
        return text
    mid = len(text) // 2
    # break near the middle, preferring existing whitespace
    left_space = text.rfind(' ', 0, mid)
    right_space = text.find(' ', mid)
    if left_space != -1 or right_space != -1:
        cut = left_space if left_space != -1 and (mid - left_space) <= (right_space - mid if right_space != -1 else 999) else right_space
        return text[:cut].strip() + '\n' + text[cut:].strip()
    return text[:mid] + '\n' + text[mid:]


def build_plan(spec):
    materials_dir = spec['materials_dir']
    fps = spec.get('canvas', {}).get('fps', FPS_DEFAULT)
    canvas = spec.get('canvas', {'width': 1920, 'height': 1080, 'fps': fps})
    style = spec.get('style', HOUSE_STYLE)

    # 1. Load narration blocks and run/reuse whisper alignment for each.
    all_lines = []
    per_line_us = []
    for block in spec['blocks']:
        text_path = os.path.join(materials_dir, block['text_file']) if not os.path.isabs(block['text_file']) else block['text_file']
        wav_path = os.path.join(materials_dir, block['wav_file']) if not os.path.isabs(block['wav_file']) else block['wav_file']
        whisper_path = block.get('whisper_json') or (os.path.splitext(wav_path)[0] + '.whisper.json')
        block_lines = read_lines(text_path)
        raw_total_us = probe_duration_us(wav_path)
        # Default: play narration at its own natural pace (speed 1.0). This
        # engine has no "keep pitch" field for sped-up audio (documented in
        # references/ai-narrated-image-video.md), so reproducing Jianying's
        # own speed-compressed target_duration_us here audibly detunes the
        # voice on rebuild. Only pass target_duration_us if you specifically
        # want the narration sped up/slowed down and accept that risk.
        target_total_us = block.get('target_duration_us') or raw_total_us
        ensure_whisper_json(wav_path, whisper_path, model_name=spec.get('whisper_model', 'small'))
        us, coverage = durations_from_whisper(block_lines, whisper_path, raw_total_us, target_total_us, fps)
        print(f'[align] {block["text_file"]}: {len(block_lines)} lines, coverage {coverage:.1%}', file=sys.stderr)
        all_lines.extend(block_lines)
        per_line_us.extend(us)
        block['_wav_path'] = wav_path
        block['_raw_total_us'] = raw_total_us
        block['_target_total_us'] = target_total_us
        block['_whisper_path'] = whisper_path
        block['_ref_text'] = ''.join(block_lines)

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

    # 3. Placement. Two modes:
    #    - fully manual (house rule: match content, not file order): every
    #      image is a "special_images" entry with match_line(s), and there is
    #      no "generated_images" glob - remaining_idx must end up empty.
    #    - auto-bucket (legacy/quick mode): leftover lines not claimed by any
    #      special are distributed proportionally across a glob of images, in
    #      file order. Only use this when nobody has actually looked at what
    #      the generated images depict; prefer the manual mode otherwise.
    gen_pattern = spec.get('generated_images')
    if gen_pattern:
        gen_files = sorted(glob.glob(os.path.join(materials_dir, gen_pattern['glob'])),
                            key=numeric_sort_key if gen_pattern.get('sort') == 'numeric-suffix' else None)
        require(gen_files, 'No generated images matched ' + gen_pattern['glob'])
    else:
        gen_files = []

    if remaining_idx and not gen_files:
        require(False, f'{len(remaining_idx)} narration lines are not covered by any special_images '
                'entry, and no generated_images glob was given to auto-fill the rest. Either add '
                'match_line(s) entries for every line (preferred - match image content to what is '
                'actually being said), or provide a generated_images glob for the leftovers.')

    if gen_files:
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
            target_idx = idxs[0] if len(idxs) == 1 else idxs
            photo = os.path.join(materials_dir, s['image']) if not os.path.isabs(s['image']) else s['image']
            kf = s.get('keyframes')
            if isinstance(target_idx, list):
                insert_special(scenes, target_idx[0], photo, kf)
                for pos, (img, blines, k) in enumerate(scenes):
                    if img == photo and blines == [target_idx[0]]:
                        scenes[pos] = (photo, sorted(target_idx), k)
                        break
            else:
                insert_special(scenes, target_idx, photo, kf)
    else:
        # Fully manual placement: every image is a special_images entry: no
        # buckets to straddle, just sort by each entry's first matched line.
        entries = []
        for s in specials:
            idxs = sorted(special_line_indices[id(s)])
            photo = os.path.join(materials_dir, s['image']) if not os.path.isabs(s['image']) else s['image']
            entries.append((idxs[0], photo, idxs, s.get('keyframes')))
        entries.sort(key=lambda e: e[0])
        scenes = [(photo, idxs, kf) for _, photo, idxs, kf in entries]

    seen = sorted(li for _, blines, _ in scenes for li in blines)
    require(seen == list(range(len(all_lines))), 'Every narration line must map to exactly one scene')

    # 4. Assemble tracks: video (+ optional subtitles), narration audio (+ optional BGM).
    video_track = {"type": "video", "name": spec.get('video_track_name', '主视频'), "segments": []}
    audio_track = {"type": "audio", "name": spec.get('audio_track_name', '旁白'), "segments": []}
    subtitle_cfg = spec.get('subtitles')
    text_track = {"type": "text", "name": "字幕", "segments": []} if subtitle_cfg and subtitle_cfg.get('enabled') else None

    cursor = 0
    for block in spec['blocks']:
        block['_timeline_start_us'] = cursor
        audio_track['segments'].append({
            "source": block['_wav_path'], "start_us": cursor, "duration_us": block['_target_total_us'],
            "source_start_us": 0, "source_duration_us": block['_raw_total_us'],
            "speed": block['_raw_total_us'] / block['_target_total_us'], "volume": block.get('volume', 1.0),
        })
        cursor += block['_target_total_us']
    narration_volume = spec['blocks'][0].get('volume', 1.0) if spec['blocks'] else 1.0

    tracks = [video_track]

    title_cfg = spec.get('title_card')
    if title_cfg:
        tc = dict(style.get('title_card', HOUSE_STYLE['title_card']))
        tc.update(title_cfg)  # spec-level overrides win
        # Two segments starting at the same time can't share one track
        # (segments on a track must be ordered/nonoverlapping) - use two
        # separate text tracks instead.
        tracks.append({"type": "text", "name": spec.get('title_track_name', '标题-系列'), "segments": [
            {"text": tc['series'], "start_us": 0, "duration_us": tc['duration_us'],
             "size": tc['series_size'], "x": 0, "y": tc['series_y'],
             "color": tc['series_color'], "border_color": tc['series_border_color'],
             "border_width": tc['series_border_width']},
        ]})
        tracks.append({"type": "text", "name": spec.get('title_track_name2', '标题'), "segments": [
            {"text": tc['title'], "start_us": 0, "duration_us": tc['duration_us'],
             "size": tc['title_size'], "x": 0, "y": tc['title_y'],
             "color": tc['title_color'], "border_color": tc['title_border_color'],
             "border_width": tc['title_border_width']},
        ]})

    callout_cfg = spec.get('callouts', [])
    callout_track = None
    if callout_cfg:
        callout_track = {"type": "text", "name": spec.get('callout_track_name', '关键词'), "segments": []}
        pop = style.get('callout_pop', HOUSE_STYLE['callout_pop'])
        style_cycle = pop.get('style_cycle', ['bounce'])
        for ci, c in enumerate(callout_cfg):
            keyword = c['keyword']
            occurrence = c.get('occurrence', 1)
            block = None
            span = None
            for b in spec['blocks']:
                try:
                    span = keyword_span(b['_ref_text'], b['_whisper_path'], keyword, occurrence)
                    block = b
                    break
                except ValueError:
                    continue
            require(block is not None, f"Callout keyword {keyword!r} (occurrence {occurrence}) "
                    "was not found in any block's narration text")
            raw_start_s, raw_end_s = span
            speed = block['_raw_total_us'] / block['_target_total_us']
            spoken_start_us = block['_timeline_start_us'] + round(raw_start_s * 1_000_000 / speed)
            hold_us = c.get('duration_us', pop['default_hold_us'])
            pop_us = min(c.get('pop_duration_us', 350_000), hold_us)
            entry_x, entry_y = c.get('x', pop['x']), c.get('y', pop['y'])
            entry_style = c.get('style', style_cycle[ci % len(style_cycle)])
            kf = _callout_keyframes(entry_style, pop_us, entry_x, entry_y)
            # The engine requires any static x/y/scale/rotation field to equal
            # that channel's first keyframe value (animation-start value, not
            # the resting position) - see native_motion.py validate().
            seg_x = kf['x'][0]['value'] if 'x' in kf else entry_x
            seg_y = kf['y'][0]['value'] if 'y' in kf else entry_y
            callout_track['segments'].append({
                "text": c.get('text', keyword),
                "start_us": spoken_start_us, "duration_us": hold_us,
                "size": c.get('size', pop['size']), "x": seg_x, "y": seg_y,
                "color": c.get('color', pop['color']), "border_color": c.get('border_color', pop['border_color']),
                "border_width": c.get('border_width', pop['border_width']),
                "keyframes": kf,
            })
        callout_track['segments'].sort(key=lambda s: s['start_us'])
        for i in range(1, len(callout_track['segments'])):
            prev = callout_track['segments'][i - 1]
            cur = callout_track['segments'][i]
            if cur['start_us'] < prev['start_us'] + prev['duration_us']:
                prev['duration_us'] = cur['start_us'] - prev['start_us']  # trim overlap, keep the pop intact
                require(prev['duration_us'] > 0, 'Two callouts resolve to (almost) the same instant - '
                        'space out the keywords or shorten default_hold_us')

    bgm_cfg = spec.get('bgm')
    if bgm_cfg:
        bgm_path = os.path.join(materials_dir, bgm_cfg['source']) if not os.path.isabs(bgm_cfg['source']) else bgm_cfg['source']
        bgm_total_us = cursor  # narration total duration computed above
        bgm_volume = bgm_cfg.get('volume', narration_volume * style.get('bgm_volume_ratio', HOUSE_STYLE['bgm_volume_ratio']))
        bgm_source_us = probe_duration_us(bgm_path)
        bgm_track = {"type": "audio", "name": spec.get('bgm_track_name', 'BGM'), "segments": [{
            "source": bgm_path, "start_us": 0, "duration_us": min(bgm_total_us, bgm_source_us),
            "source_start_us": 0, "source_duration_us": min(bgm_total_us, bgm_source_us),
            "volume": bgm_volume,
        }]}
        require(bgm_source_us >= bgm_total_us,
                f'BGM track ({bgm_source_us/1e6:.1f}s) is shorter than the narration '
                f'({bgm_total_us/1e6:.1f}s); trimmed to BGM length - provide a longer track or loop it yourself')
        tracks.append(bgm_track)

    tracks.append(audio_track)
    if callout_track is not None:
        tracks.append(callout_track)
    if text_track is not None:
        tracks.append(text_track)

    cursor = 0
    n_scenes = len(scenes)
    trans_us = round(style.get('transition_seconds', HOUSE_STYLE['transition_seconds']) * fps)
    trans_us = round((trans_us if trans_us % 2 == 0 else trans_us + 1) * 1_000_000 / fps)  # even-frame snap
    sub_style = style.get('subtitle', HOUSE_STYLE['subtitle'])
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
            # house style: every adjacent pair dissolves, no hard cuts, no
            # mixed transition types (only 'dissolve' is a captured/verified
            # native resource in this repo - see native-resource-catalog.json)
            seg["transition_out"] = {"name": "dissolve", "duration_us": trans_us}
        video_track['segments'].append(seg)

        if text_track is not None:
            sub_cursor = cursor
            for li in blines:
                d = per_line_us[li]
                text_track['segments'].append({
                    "text": wrap_two_lines(all_lines[li], sub_style.get('max_chars_per_line', 16)),
                    "start_us": sub_cursor, "duration_us": d,
                    "size": sub_style['size'], "x": 0, "y": sub_style['y'],
                    "color": sub_style['color'], "border_color": sub_style['border_color'],
                    "border_width": sub_style['border_width'],
                })
                sub_cursor += d

        cursor += seg_dur

    plan = {
        "schema": "jy14-headless-plan/v1",
        "name": spec['name'],
        "canvas": canvas,
        "tracks": tracks,
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
