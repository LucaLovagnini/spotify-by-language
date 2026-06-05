"""Measure LRCLib as a potential replacement for Genius.

Two cohorts, both drawn from the current classified output:
  A) All 530 tracks the current pipeline flagged as `genius_instrumental`.
     For each, ask LRCLib and check:
       - did LRCLib find a match?
       - is LRCLib's `instrumental` field true?
       - or does it return lyrics anyway (false positive)?
  B) A 100-track random sample from `source=="genius"` (vocal tracks that
     currently have a detected language via langdetect on a 400-char snippet).
     For each, ask LRCLib and check:
       - did LRCLib find a match?
       - did it return full plainLyrics?
       - if yes, what does langdetect say on the FULL lyrics vs the snippet?
       - does it agree with the current classification?

Uses a requests.Session for connection pooling. No throttle between calls.
"""
import json
import random
import sys
import time
from collections import Counter

import requests
from langdetect import DetectorFactory, detect_langs

DetectorFactory.seed = 0

API_URL = "https://lrclib.net/api/get"
INPUT = "data/tracks_genius_classified.json"
OUTPUT = "data/measure_lrclib.json"

session = requests.Session()
session.headers["User-Agent"] = "spotifyByLanguage-measurement/0.1 (one-off research)"


def lrclib_get(artist, track, album=None, duration=None):
    """Returns (json_dict_or_none, status_code)."""
    params = {"artist_name": artist, "track_name": track}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = duration
    try:
        r = session.get(API_URL, params=params, timeout=15)
        if r.status_code == 200:
            return r.json(), 200
        if r.status_code == 404:
            return None, 404
        return None, r.status_code
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)
        return None, -1


def lang_of(text):
    if not text or len(text.strip()) < 20:
        return None, 0.0
    try:
        langs = detect_langs(text)
        return langs[0].lang, langs[0].prob
    except Exception:
        return None, 0.0


def main():
    tracks = json.load(open(INPUT))
    instrumentals = [t for t in tracks if t.get("source") == "genius_instrumental"]
    vocals_all = [t for t in tracks if t.get("source") == "genius"]
    random.seed(42)
    vocals = random.sample(vocals_all, k=min(100, len(vocals_all)))

    print(f"Cohort A: {len(instrumentals)} known-instrumentals", flush=True)
    print(f"Cohort B: {len(vocals)} random vocal tracks (from {len(vocals_all)} total)", flush=True)
    print(f"Total LRCLib calls: ~{len(instrumentals) + len(vocals)}\n", flush=True)

    results = {"instrumentals": [], "vocals": []}
    t0 = time.time()
    n_calls = 0
    n_404 = 0
    n_errors = 0

    # ---- COHORT A ----
    print("=== COHORT A: instrumentals ===", flush=True)
    for i, t in enumerate(instrumentals, 1):
        data, status = lrclib_get(t["artist"], t["name"])
        n_calls += 1
        if status == 404:
            n_404 += 1
            results["instrumentals"].append({"artist": t["artist"], "name": t["name"], "found": False})
        elif data is None:
            n_errors += 1
            results["instrumentals"].append({"artist": t["artist"], "name": t["name"], "error": status})
        else:
            results["instrumentals"].append({
                "artist": t["artist"],
                "name": t["name"],
                "found": True,
                "lrclib_instrumental": data.get("instrumental", False),
                "has_plain_lyrics": bool(data.get("plainLyrics")),
                "lyrics_len": len(data.get("plainLyrics") or ""),
            })
        if i % 50 == 0:
            elapsed = time.time() - t0
            rpm = n_calls / (elapsed / 60) if elapsed > 0 else 0
            print(f"  [{i}/{len(instrumentals)}] {rpm:.1f} req/min, {n_404} 404s, {n_errors} errors", flush=True)

    # ---- COHORT B ----
    print("\n=== COHORT B: vocal tracks ===", flush=True)
    for i, t in enumerate(vocals, 1):
        data, status = lrclib_get(t["artist"], t["name"])
        n_calls += 1
        record = {
            "artist": t["artist"],
            "name": t["name"],
            "current_final_language": t.get("final_language"),
            "snippet_excerpt": (t.get("lyrics_snippet") or "")[:80],
        }
        if status == 404:
            n_404 += 1
            record["found"] = False
        elif data is None:
            n_errors += 1
            record["error"] = status
        else:
            full_lyrics = data.get("plainLyrics") or ""
            full_lang, full_conf = lang_of(full_lyrics)
            snippet_lang, snippet_conf = lang_of(t.get("lyrics_snippet") or "")
            record.update({
                "found": True,
                "lrclib_instrumental": data.get("instrumental", False),
                "lyrics_len": len(full_lyrics),
                "lang_on_full_lyrics": full_lang,
                "conf_on_full_lyrics": round(full_conf, 3),
                "lang_on_snippet": snippet_lang,
                "conf_on_snippet": round(snippet_conf, 3),
                "agrees_with_current": (full_lang == t.get("final_language")) if full_lang else None,
            })
        results["vocals"].append(record)
        if i % 25 == 0:
            elapsed = time.time() - t0
            rpm = n_calls / (elapsed / 60) if elapsed > 0 else 0
            print(f"  [{i}/{len(vocals)}] {rpm:.1f} req/min, {n_404} 404s, {n_errors} errors", flush=True)

    elapsed = time.time() - t0
    rpm = n_calls / (elapsed / 60)
    print(f"\nWall time: {elapsed:.1f}s ({elapsed/60:.1f} min)", flush=True)
    print(f"Total calls: {n_calls}  →  {rpm:.1f} req/min  ({n_404} 404s, {n_errors} errors)\n", flush=True)

    # ---- REPORT: cohort A ----
    A = results["instrumentals"]
    found_A = [r for r in A if r.get("found")]
    inst_true = [r for r in found_A if r.get("lrclib_instrumental")]
    has_lyrics = [r for r in found_A if r.get("has_plain_lyrics")]
    print("=" * 60, flush=True)
    print("COHORT A — 530 known instrumentals", flush=True)
    print(f"  found on LRCLib:           {len(found_A):>4} / {len(A)} ({100*len(found_A)/len(A):.1f}%)", flush=True)
    print(f"  marked instrumental=true:  {len(inst_true):>4} (true positives)", flush=True)
    print(f"  has plain lyrics:          {len(has_lyrics):>4} (potential false positives)", flush=True)
    # show some false positives
    fp = [r for r in has_lyrics if not r.get("lrclib_instrumental")]
    print(f"  found AND has lyrics:      {len(fp):>4} (suspected misclassification by LRCLib OR our pipeline)", flush=True)
    if fp:
        print("\n  Sample of 'has lyrics' instrumentals (first 10):", flush=True)
        for r in fp[:10]:
            print(f"    {r['artist']!r:35} {r['name']!r:40} lyrics_len={r['lyrics_len']}", flush=True)

    # ---- REPORT: cohort B ----
    B = results["vocals"]
    found_B = [r for r in B if r.get("found")]
    with_lyrics_B = [r for r in found_B if r.get("lyrics_len", 0) > 50]
    agree = [r for r in with_lyrics_B if r.get("agrees_with_current")]
    disagree = [r for r in with_lyrics_B if r.get("agrees_with_current") is False]
    print("\n" + "=" * 60, flush=True)
    print(f"COHORT B — {len(B)} random vocal tracks", flush=True)
    print(f"  found on LRCLib:           {len(found_B):>4} / {len(B)} ({100*len(found_B)/len(B):.1f}%)", flush=True)
    print(f"  with non-trivial lyrics:   {len(with_lyrics_B):>4}", flush=True)
    print(f"  langdetect on full lyrics agrees with current: {len(agree):>4}", flush=True)
    print(f"  langdetect on full lyrics disagrees:           {len(disagree):>4}", flush=True)
    if disagree:
        print("\n  Disagreements (current → full-lyrics langdetect):", flush=True)
        for r in disagree[:15]:
            print(f"    {r['artist']!r:30} {r['name']!r:35} {r['current_final_language']!r} → {r['lang_on_full_lyrics']!r} (conf {r['conf_on_full_lyrics']})", flush=True)

    json.dump(results, open(OUTPUT, "w"), indent=2, ensure_ascii=False)
    print(f"\nFull results written to {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
