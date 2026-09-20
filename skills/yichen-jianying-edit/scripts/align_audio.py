# -*- coding: utf-8 -*-
import json, sys

def extract_char_timeline(whisper_json_path):
    d = json.load(open(whisper_json_path, encoding='utf-8'))
    chars = []  # list of (char, start, end)
    for seg in d['segments']:
        for w in seg.get('words', []):
            word = w['word']
            start, end = w['start'], w['end']
            # strip whitespace/punctuation-only tokens are still useful as timing anchors,
            # but we keep punctuation chars too since they don't appear in our reference text
            # (they'll just fail to match and be treated as insertions).
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
    # DP with O(n*m) memory is fine for ~600x700 chars
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
    # backtrack
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

def line_boundary_times(lines, whisper_json_path):
    chars = extract_char_timeline(whisper_json_path)
    rec_text = ''.join(c for c, s, e in chars)
    ref_text = ''.join(lines)
    pairs = needleman_wunsch(ref_text, rec_text)
    # ref_index -> rec_index (best-effort; None for gaps)
    ref_to_rec = {}
    for ri, rj in pairs:
        if ri is not None and rj is not None:
            ref_to_rec[ri] = rj

    cum = 0
    boundaries_ref_idx = []
    for line in lines[:-1]:
        cum += len(line)
        boundaries_ref_idx.append(cum)  # index of first char of NEXT line in ref_text

    def time_at_ref_index(ref_idx):
        # find nearest matched ref index at or after ref_idx, else before
        for k in range(ref_idx, len(ref_text)):
            if k in ref_to_rec:
                return chars[ref_to_rec[k]][1]  # start time
        for k in range(ref_idx - 1, -1, -1):
            if k in ref_to_rec:
                return chars[ref_to_rec[k]][2]  # end time
        return None

    times = [time_at_ref_index(bi) for bi in boundaries_ref_idx]
    return times, ref_text, rec_text, len(ref_to_rec)

if __name__ == '__main__':
    pass
