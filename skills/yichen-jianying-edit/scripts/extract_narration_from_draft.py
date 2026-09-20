#!/usr/bin/env python3
"""Extract narration text + audio blocks from a live Jianying draft.

Pulls out every 'text_to_audio' material in a draft's timeline (the result of
using 剪映's own "批量朗读" feature), in on-timeline order, and writes each
block's narration text and wav file into an output directory, plus a
spec.json 'blocks' skeleton ready to hand to build_narrated_image_plan.py.

This is step 2 of the workflow in
references/ai-narrated-image-video.md - step 1 (recording the narration in
Jianying) is manual; this script and build_narrated_image_plan.py (step 3)
are the automatable parts.

Usage:
    python3 extract_narration_from_draft.py --draft "/path/to/draft" --out-dir WORK/narration
"""
import argparse
import json
import os
import shutil
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ENGINE_DIR = os.path.join(PROJECT_ROOT, 'engine')
if os.environ.get('JIANYING_HEADLESS_ROOT'):
    ENGINE_DIR = os.path.join(os.environ['JIANYING_HEADLESS_ROOT'], 'engine')
sys.path.insert(0, ENGINE_DIR)

import headless_runtime as rt  # noqa: E402


def require(value, message):
    if not value:
        raise SystemExit(message)


def resolve_placeholder_path(draft_dir, raw_path):
    """Audio material 'path' fields use a '##_draftpath_placeholder_<id>_##/...'
    token standing in for the draft's own directory - substitute it."""
    if '_##/' in raw_path:
        relative = raw_path.split('_##/', 1)[1]
    else:
        relative = raw_path
    return os.path.join(draft_dir, relative)


def extract(draft_dir, out_dir):
    draft_dir = os.path.abspath(draft_dir)
    require(os.path.isdir(draft_dir), 'Draft directory not found: ' + draft_dir)
    h = rt.helper()
    timeline = h._decrypt_metadata_in_memory(os.path.join(draft_dir, 'draft_info.json'))
    materials = timeline['materials']
    texts_by_id = {t['id']: t for t in materials.get('texts', [])}
    audios = [a for a in materials.get('audios', []) if a.get('type') == 'text_to_audio']
    require(audios, 'No text_to_audio materials found in this draft - '
            'did you generate narration with 剪映\'s 批量朗读 feature?')

    # Order blocks by where their audio segment sits on the timeline, not by
    # material list order (which is not guaranteed to match).
    audio_track = next((t for t in timeline['tracks'] if t['type'] == 'audio'), None)
    require(audio_track, 'No audio track in this draft')
    seg_by_material = {s['material_id']: s for s in audio_track['segments']}
    ordered = sorted(audios, key=lambda a: seg_by_material.get(a['id'], {}).get('target_timerange', {}).get('start', 0))

    os.makedirs(out_dir, exist_ok=True)
    blocks_spec = []
    for i, a in enumerate(ordered, start=1):
        seg = seg_by_material.get(a['id'])
        require(seg, f'Audio material {a["id"]} has no timeline segment')

        text_material = texts_by_id.get(a.get('text_id'))
        require(text_material, f'Audio block {i} has no matching text material (text_id={a.get("text_id")!r})')
        content = text_material.get('content') or text_material.get('text')
        try:
            parsed = json.loads(content)
            narration_text = parsed['text']
        except (TypeError, ValueError, KeyError):
            narration_text = content
        require(narration_text, f'Audio block {i} text material had no text content')

        src_wav = resolve_placeholder_path(draft_dir, a['path'])
        require(os.path.isfile(src_wav), f'Audio block {i} wav not found: {src_wav}')

        text_path = os.path.join(out_dir, f'block{i}.txt')
        wav_path = os.path.join(out_dir, f'block{i}.wav')
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(narration_text.replace('\r\n', '\n'))
        shutil.copyfile(src_wav, wav_path)

        target_us = seg['target_timerange']['duration']
        blocks_spec.append({
            'text_file': text_path,
            'wav_file': wav_path,
            'target_duration_us': target_us,
        })
        print(f'[block {i}] {len(narration_text)} chars, target={target_us/1e6:.2f}s, '
              f'tone={a.get("tone_type")!r} -> {text_path}', file=sys.stderr)

    spec_skeleton = {
        'schema': 'jy14-narrated-image-video-spec/v1',
        'name': 'FILL ME IN',
        'canvas': {'width': 1920, 'height': 1080, 'fps': 30},
        'materials_dir': 'FILL ME IN (absolute path to your images folder)',
        'blocks': blocks_spec,
        'generated_images': {'glob': 'FILL ME IN (e.g. \"ChatGPT Image*.png\")', 'sort': 'numeric-suffix'},
        'special_images': [],
    }
    spec_path = os.path.join(out_dir, 'spec.skeleton.json')
    with open(spec_path, 'w', encoding='utf-8') as f:
        json.dump(spec_skeleton, f, ensure_ascii=False, indent=2)
    print(json.dumps({'status': 'extracted', 'blocks': len(blocks_spec), 'spec_skeleton': spec_path}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--draft', required=True, help='Absolute path to the live draft directory')
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args()
    extract(args.draft, args.out_dir)


if __name__ == '__main__':
    main()
