"""One-off measurement: how often does Genius's API populate `language` for
tracks our pipeline flagged as instrumental? Also tests the unthrottled
request rate empirically.

Reads data/tracks_genius_classified.json, takes every track with
source=="genius_instrumental", re-runs /search + /songs/:id with NO sleep
between calls, and reports:
  - coverage of the `language` field on these instrumentals
  - per-language false-positive breakdown
  - effective requests/minute and number of 429s hit
"""
import json
import os
import time
from collections import Counter

import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ["GENIUS_ACCESS_TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

SEARCH_URL = "https://api.genius.com/search"
SONG_URL = "https://api.genius.com/songs/{id}"

INPUT = "data/tracks_genius_classified.json"
OUTPUT = "data/measure_genius_language.json"


def call(url, params=None):
    """Single GET with 429 backoff. Returns (json, num_429s_hit, total_wait_s)."""
    waits = 0.0
    retries = 0
    n429 = 0
    while retries < 6:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json(), n429, waits
        if resp.status_code == 429:
            n429 += 1
            wait = 5 * (retries + 1)
            print(f"  429 → sleeping {wait}s")
            time.sleep(wait)
            waits += wait
            retries += 1
            continue
        print(f"  HTTP {resp.status_code} on {url}")
        return None, n429, waits
    return None, n429, waits


def main():
    tracks = json.load(open(INPUT))
    instrumentals = [t for t in tracks if t.get("source") == "genius_instrumental"]
    print(f"Found {len(instrumentals)} tracks flagged as genius_instrumental")
    print("Probing /search + /songs/:id with NO sleep between calls...\n")

    results = []
    total_429 = 0
    total_wait = 0.0
    t0 = time.time()

    for i, t in enumerate(instrumentals, 1):
        query = f"{t['name']} {t['artist']}"
        search_json, n429a, wa = call(SEARCH_URL, params={"q": query})
        total_429 += n429a
        total_wait += wa
        if not search_json:
            results.append({"name": t["name"], "artist": t["artist"], "error": "search_failed"})
            continue
        hits = search_json.get("response", {}).get("hits", [])
        if not hits:
            results.append({"name": t["name"], "artist": t["artist"], "song_id": None, "language": None, "no_hits": True})
            continue
        sid = hits[0]["result"]["id"]
        song_json, n429b, wb = call(SONG_URL.format(id=sid))
        total_429 += n429b
        total_wait += wb
        if not song_json:
            results.append({"name": t["name"], "artist": t["artist"], "song_id": sid, "error": "song_failed"})
            continue
        song = song_json["response"]["song"]
        results.append({
            "name": t["name"],
            "artist": t["artist"],
            "song_id": sid,
            "language": song.get("language"),
            "lyrics_state": song.get("lyrics_state"),
            "instrumental": song.get("instrumental"),
        })
        if i % 25 == 0:
            elapsed = time.time() - t0
            calls_done = i * 2
            rpm = calls_done / (elapsed / 60) if elapsed > 0 else 0
            print(f"  [{i}/{len(instrumentals)}] {calls_done} calls in {elapsed:.0f}s → {rpm:.1f} req/min, {total_429} 429s")

    elapsed = time.time() - t0
    total_calls = sum(1 for r in results if "error" not in r and not r.get("no_hits")) * 2 + \
                  sum(1 for r in results if r.get("no_hits") or r.get("error") == "song_failed")
    # simpler: 2 calls per non-error track + 1 per error-on-song + 1 per no_hits
    effective_rpm = (len(instrumentals) * 2) / (elapsed / 60) if elapsed > 0 else 0

    # ---- report ----
    print("\n" + "=" * 60)
    print(f"WALL TIME: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"NOMINAL CALLS: {len(instrumentals)*2} (approx; ignores early exits)")
    print(f"EFFECTIVE REQ/MIN: {effective_rpm:.1f}")
    print(f"429s HIT: {total_429} (total backoff: {total_wait:.0f}s)")
    print()

    have_lang = [r for r in results if r.get("language")]
    null_lang = [r for r in results if "language" in r and r.get("language") is None]
    errors = [r for r in results if "error" in r]
    no_hits = [r for r in results if r.get("no_hits")]

    print(f"language POPULATED:    {len(have_lang):>4} / {len(instrumentals)} ({100*len(have_lang)/len(instrumentals):.1f}%)")
    print(f"language NULL:         {len(null_lang):>4}")
    print(f"no Genius hits:        {len(no_hits):>4}")
    print(f"errors:                {len(errors):>4}")
    print()

    by_lang = Counter(r["language"] for r in have_lang)
    print("Per-language false-positive count (these would be misclassified by new stage):")
    for lang, n in by_lang.most_common():
        print(f"  {lang}: {n}")
    print()

    inst_field_set = [r for r in results if r.get("instrumental")]
    print(f"`instrumental` field truthy in API response: {len(inst_field_set)}")
    print()

    print("Sample misclassifications (first 15):")
    for r in have_lang[:15]:
        print(f"  language={r['language']!r:6}  {r['artist']!r:35} {r['name']!r}")

    json.dump(results, open(OUTPUT, "w"), indent=2, ensure_ascii=False)
    print(f"\nFull results written to {OUTPUT}")


if __name__ == "__main__":
    main()
