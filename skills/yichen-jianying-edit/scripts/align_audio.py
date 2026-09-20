# -*- coding: utf-8 -*-
"""Forced-alignment helpers: match a known reference text to a Whisper
word-timestamp transcript of the audio that (supposedly) reads it aloud,
via a simple character-level Needleman-Wunsch alignment. Whisper's own
segmentation doesn't know our line/keyword boundaries, so this recovers
precise timing for arbitrary spans of the reference text, not just
Whisper's own segment boundaries.
"""
import json


def extract_char_timeline(whisper_json_path):
    d = json.load(open(whisper_json_path, encoding='utf-8'))
    chars = []  # list of (char, start, end)
    for seg in d['segments']:
        for w in seg.get('words', []):
            word = w['word']
            start, end = w['start'], w['end']
            n = len(word)
            if n == 0:
                continue
            span = (end - start) / n
            for i, c in enumerate(word):
                cs = start + i * span
                ce = start + (i + 1) * span
                chars.append((c, cs, ce))
    return chars


def needleman_wunsch(ref, rec):
    # ref, rec: strings. Returns list of (ref_index_or_None, rec_index_or_None) alignment pairs.
    n, m = len(ref), len(rec)
    MATCH, MISMATCH, GAP = 2, -1, -1
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + GAP
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + GAP
    for i in range(1, n + 1):
        ri = ref[i - 1]
        row = dp[i]
        prow = dp[i - 1]
        for j in range(1, m + 1):
            score = MATCH if ri == rec[j - 1] else MISMATCH
            diag = prow[j - 1] + score
            up = prow[j] + GAP
            left = row[j - 1] + GAP
            row[j] = max(diag, up, left)
    i, j = n, m
    pairs = []
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            score = MATCH if ref[i - 1] == rec[j - 1] else MISMATCH
            if dp[i][j] == dp[i - 1][j - 1] + score:
                pairs.append((i - 1, j - 1))
                i -= 1
                j -= 1
                continue
        if i > 0 and dp[i][j] == dp[i - 1][j] + GAP:
            pairs.append((i - 1, None))
            i -= 1
            continue
        pairs.append((None, j - 1))
        j -= 1
    pairs.reverse()
    return pairs


def align_reference(ref_text, whisper_json_path):
    """Returns (chars, ref_to_rec, rec_text). chars[k] = (char, start_s, end_s)
    for the k-th recognized character; ref_to_rec[i] = k means ref_text[i]
    aligned to chars[k] (gaps/mismatches with no timed counterpart are
    simply absent from ref_to_rec)."""
    chars = extract_char_timeline(whisper_json_path)
    rec_text = ''.join(c for c, s, e in chars)
    pairs = needleman_wunsch(ref_text, rec_text)
    ref_to_rec = {ri: rj for ri, rj in pairs if ri is not None and rj is not None}
    return chars, ref_to_rec, rec_text


def _time_at_ref_index(ref_text, chars, ref_to_rec, ref_idx, prefer='start'):
    for k in range(ref_idx, len(ref_text)):
        if k in ref_to_rec:
            return chars[ref_to_rec[k]][1 if prefer == 'start' else 2]
    for k in range(ref_idx - 1, -1, -1):
        if k in ref_to_rec:
            return chars[ref_to_rec[k]][2 if prefer == 'start' else 1]
    return None


def line_boundary_times(lines, whisper_json_path):
    ref_text = ''.join(lines)
    chars, ref_to_rec, rec_text = align_reference(ref_text, whisper_json_path)

    cum = 0
    boundaries_ref_idx = []
    for line in lines[:-1]:
        cum += len(line)
        boundaries_ref_idx.append(cum)

    times = [_time_at_ref_index(ref_text, chars, ref_to_rec, bi, 'start') for bi in boundaries_ref_idx]
    return times, ref_text, rec_text, len(ref_to_rec)


def keyword_span(ref_text, whisper_json_path, keyword, occurrence=1):
    """Locate the (start_s, end_s) of the Nth occurrence (1-indexed) of
    `keyword` inside `ref_text`, using the same char-level forced alignment.
    Raises ValueError if the keyword doesn't occur that many times."""
    chars, ref_to_rec, rec_text = align_reference(ref_text, whisper_json_path)
    start = 0
    found_at = None
    for _ in range(occurrence):
        idx = ref_text.find(keyword, start)
        if idx == -1:
            raise ValueError(f'Occurrence {occurrence} of {keyword!r} not found in reference text '
                              f'(only {found_at is not None and occurrence - 1 or 0} found)')
        found_at = idx
        start = idx + 1
    span_start = _time_at_ref_index(ref_text, chars, ref_to_rec, found_at, 'start')
    span_end = _time_at_ref_index(ref_text, chars, ref_to_rec, found_at + len(keyword) - 1, 'end')
    if span_start is None or span_end is None:
        raise ValueError(f'Could not resolve timing for {keyword!r} (occurrence {occurrence}) - '
                          'no aligned characters nearby')
    return span_start, span_end


if __name__ == '__main__':
    pass
