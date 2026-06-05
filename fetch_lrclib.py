import json
import os
import re
import time
from collections import Counter

import requests
from langdetect import DetectorFactory, detect_langs

import config

DetectorFactory.seed = 0

INPUT_FILE = config.TRACKS_TAGGED
OUTPUT_FILE = config.TRACKS_LRCLIB

# Resume: tracks already at one of these sources are not re-queried on restart.
TERMINAL_SOURCES = {
    "lrclib",
    "lrclib_instrumental",
    "lrclib_empty",
    "lrclib_not_found",
    "title_marker",
}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "spotifyByLanguage/0.1 (https://github.com/luca/spotifyByLanguage)"


def _clean_query(name):
    """Mirror fetch_genius_lyrics._clean_query: strip Spotify alternate-version
    suffixes like ' - Remastered 2015' so LRCLib matches the canonical track."""
    return re.split(r"\s+-\s+", name, maxsplit=1)[0]


def lrclib_get(artist, track_name):
    """Returns LRCLib's song record or None on 404/error. Retries 429 with backoff."""
    params = {"artist_name": artist, "track_name": _clean_query(track_name)}
    retries = 0
    while retries < 5:
        try:
            resp = SESSION.get(config.LRCLIB_API_URL, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                wait = 10 * (retries + 1)
                print(f"Rate limit hit. Waiting {wait}s...")
                time.sleep(wait)
                retries += 1
                continue
            return None
        except Exception as e:
            print(f"Request error: {e}")
            time.sleep(5)
            retries += 1
    return None


def detect_language_from_lyrics(lyrics):
    try:
        langs = detect_langs(lyrics)
        top = langs[0] if langs else None
        return (top.lang, float(top.prob)) if top else ("unknown", 0.0)
    except Exception:
        return "unknown", 0.0


def classify_with_lrclib(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    """Stage 3: query LRCLib for tracks not yet classified upstream.

    For each unclassified track:
      - instrumental=true        → final_language="instrumental", source="lrclib_instrumental"
      - non-empty plainLyrics    → langdetect on full lyrics, source="lrclib"
      - record exists but empty  → source="lrclib_empty", final_language unset
      - 404 / error              → source="lrclib_not_found", final_language unset

    Tracks left without final_language fall through to the Genius stage,
    then to the metadata stage — same data-driven handoff as before.
    """
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            final_tracks = {t["id"]: t for t in json.load(f)}
    else:
        final_tracks = {}

    with open(input_file, "r", encoding="utf-8") as f:
        input_tracks = json.load(f)

    for track in input_tracks:
        # Already classified upstream (stage 2 title marker) — pass through.
        if track.get("final_language"):
            final_tracks[track["id"]] = dict(track)
            continue

        # Resume: skip tracks a prior LRCLib pass already settled.
        existing = final_tracks.get(track["id"])
        if existing and existing.get("source") in TERMINAL_SOURCES:
            continue

        print(f"Fetching LRCLib data for: {track['name']} - {track['artist']}")
        data = lrclib_get(track["artist"], track["name"])

        if data is None:
            track.update({"source": "lrclib_not_found"})
        elif data.get("instrumental"):
            track.update({
                "final_language": "instrumental",
                "confidence": 1.0,
                "source": "lrclib_instrumental",
            })
        elif data.get("plainLyrics"):
            lyrics = data["plainLyrics"]
            lang, conf = detect_language_from_lyrics(lyrics)
            track.update({
                "final_language": lang,
                "confidence": round(conf, 4),
                "source": "lrclib",
                "lyrics_snippet": lyrics[:400],
            })
        else:
            track.update({"source": "lrclib_empty"})

        final_tracks[track["id"]] = track
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(list(final_tracks.values()), f, ensure_ascii=False, indent=2)

    # Final write so pass-through updates (title_marker tracks, or resume-cached
    # entries) after the last LRCLib fetch are persisted.
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(list(final_tracks.values()), f, ensure_ascii=False, indent=2)

    counts = Counter(t.get("source") for t in final_tracks.values())
    print("=== LRCLib Source Summary ===")
    for src, cnt in counts.most_common():
        print(f"  {src}: {cnt}")
    classified = sum(1 for t in final_tracks.values() if t.get("final_language"))
    print(f"✅ Saved {len(final_tracks)} tracks ({classified} classified by stages 2+3) → {output_file}")
    return output_file


if __name__ == "__main__":
    classify_with_lrclib()
