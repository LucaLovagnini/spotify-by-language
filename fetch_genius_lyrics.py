import json
import os
import re
import time
import unicodedata
from collections import Counter

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langdetect import detect_langs, DetectorFactory

import config

DetectorFactory.seed = 0

INPUT_FILE = config.TRACKS_LRCLIB
OUTPUT_FILE = config.TRACKS_GENIUS_CLASSIFIED

load_dotenv()
GENIUS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN")
HEADERS = {"Authorization": f"Bearer {GENIUS_TOKEN}"}

# Genius marks the lyrics box with this phrase for confirmed instrumentals
# whose page exists but has no lyrics container.
INSTRUMENTAL_PAGE_MARKER = re.compile(r"This song is an instrumental", re.I)
# Some pages render a single bracketed token instead of lyrics; common variants.
INSTRUMENTAL_BRACKETED_RX = re.compile(
    r"\[[^\]]*(?:Instrumental|Strumentale|Instrumentaal|Инструментал)[^\]]*\]",
    re.I,
)
# Source values that mean "Genius has been consulted and the result is terminal".
# Stage 3 skips any track already at one of these on resume.
TERMINAL_SOURCES = {
    "genius",
    "genius_instrumental",
    "genius_not_found",
    "genius_empty",
    "title_marker",
}

# Markers in a Genius hit title that indicate a user-submitted translation page,
# whose lyrics are in the target language, not the song's actual language.
TRANSLATION_TITLE_RX = re.compile(
    r"\b(traduction|tradução|tradurre|translation|"
    r"übersetzung|перевод|çeviri|tłumaczenie|"
    r"traducción|traducao|překlad|fordítás|dịch)\b",
    re.I,
)


def _normalize(s):
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _artist_matches(spotify_artist, genius_artist):
    """Fuzzy match: equal after normalization, one contains the other, or one's
    tokens are a subset of the other's. Handles accents, feat./& suffixes."""
    a, g = _normalize(spotify_artist), _normalize(genius_artist)
    if not a or not g:
        return False
    if a == g or a in g or g in a:
        return True
    ta, tg = set(a.split()), set(g.split())
    return bool(ta) and bool(tg) and (ta.issubset(tg) or tg.issubset(ta))


def _pick_genius_hit(hits, spotify_artist):
    """First hit whose primary artist matches and which isn't a translation page.
    Returns None if no acceptable hit exists — caller should treat as not found."""
    for hit in hits:
        result = hit.get("result") or {}
        title = result.get("title") or ""
        pa = (result.get("primary_artist") or {}).get("name") or ""
        if TRANSLATION_TITLE_RX.search(title) or TRANSLATION_TITLE_RX.search(pa):
            continue
        if _artist_matches(spotify_artist, pa):
            return hit
    return None


def _clean_query(name):
    """Strip Spotify's alternate-version suffixes that confuse Genius search.
    Splits at the first ' - ' and keeps the part before it. Examples:
      'Day Tripper - Remastered 2015' -> 'Day Tripper'
      'Sun Sun Damba - Original Mix'  -> 'Sun Sun Damba'
    The stored track['name'] is unchanged; only the search query is cleaned."""
    return re.split(r"\s+-\s+", name, maxsplit=1)[0]

def genius_search(song_name, artist_name):
    query = f"{_clean_query(song_name)} {artist_name}"
    params = {"q": query}
    retries = 0
    while retries < 5:
        try:
            resp = requests.get(config.GENIUS_SEARCH_URL, headers=HEADERS, params=params, timeout=10)
            if resp.status_code == 200:
                hits = resp.json().get("response", {}).get("hits", [])
                hit = _pick_genius_hit(hits, artist_name)
                if not hit:
                    return {"genius_known": False, "lyrics_snippet": None, "is_instrumental": False}
                url = hit["result"]["url"]
                lyrics, is_inst = scrape_lyrics(url)
                return {
                    "genius_known": True,
                    "lyrics_snippet": lyrics[:400] if lyrics else None,
                    "is_instrumental": is_inst,
                }
            elif resp.status_code == 429:
                wait = 10 * (retries + 1)
                print(f"Rate limit hit. Waiting {wait}s...")
                time.sleep(wait)
                retries += 1
            else:
                return {"genius_known": True, "lyrics_snippet": None, "is_instrumental": False}
        except Exception as e:
            print(f"Request error: {e}")
            time.sleep(5)
            retries += 1
    return {"genius_known": True, "lyrics_snippet": None, "is_instrumental": False}

def scrape_lyrics(url):
    """Return (lyrics_text, is_instrumental). is_instrumental flags pages Genius
    has labeled as instrumentals — either via the "This song is an instrumental"
    notice (no lyrics container) or via a short body whose only content is a
    bracketed [Instrumental]/[Strumentale]/... token.
    """
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        lyrics_divs = soup.find_all("div", {"data-lyrics-container": "true"})
        text = "\n".join(div.get_text(separator="\n") for div in lyrics_divs).strip()
        is_instrumental = bool(INSTRUMENTAL_PAGE_MARKER.search(html)) or (
            len(text) < 200 and bool(INSTRUMENTAL_BRACKETED_RX.search(text))
        )
        return text, is_instrumental
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return None, False

def detect_language_from_lyrics(lyrics):
    try:
        langs = detect_langs(lyrics)
        top = langs[0] if langs else None
        return (top.lang, float(top.prob)) if top else ("unknown", 0.0)
    except Exception:
        return "unknown", 0.0

def augment_with_genius(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    if not GENIUS_TOKEN:
        raise RuntimeError(
            "GENIUS_ACCESS_TOKEN is not set. Add it to .env (see .env.example) "
            "before running this stage."
        )

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

        # Resume: skip if a prior Genius pass already produced a terminal result.
        existing = final_tracks.get(track["id"])
        if existing and existing.get("source") in TERMINAL_SOURCES:
            continue

        print(f"Fetching Genius data for: {track['name']} - {track['artist']}")
        genius_result = genius_search(track["name"], track["artist"])

        if genius_result["is_instrumental"]:
            track.update({
                "final_language": "instrumental",
                "confidence": 1.0,
                "source": "genius_instrumental",
                "genius_known": True,
                "lyrics_snippet": genius_result["lyrics_snippet"],
            })
        elif genius_result["lyrics_snippet"]:
            lang, conf = detect_language_from_lyrics(genius_result["lyrics_snippet"])
            track.update({
                "final_language": lang,
                "confidence": round(conf, 4),
                "source": "genius",
                "genius_known": True,
                "lyrics_snippet": genius_result["lyrics_snippet"],
            })
        elif genius_result["genius_known"]:
            # Page exists but no lyrics and no instrumental marker.
            # Leave final_language unset — stage 4 (metadata fallback) fills it.
            track.update({
                "source": "genius_empty",
                "genius_known": True,
                "lyrics_snippet": None,
            })
        else:
            # No acceptable Genius hit. Same handoff to stage 4.
            track.update({
                "source": "genius_not_found",
                "genius_known": False,
                "lyrics_snippet": None,
            })

        final_tracks[track["id"]] = track
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(list(final_tracks.values()), f, ensure_ascii=False, indent=2)
        time.sleep(1)

    # Final write so pass-through updates after the last Genius search are persisted.
    # Without this, when all remaining tracks pass through (final_language already set
    # upstream), their in-memory updates would be discarded on function return.
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(list(final_tracks.values()), f, ensure_ascii=False, indent=2)

    counts = Counter(t.get("source") for t in final_tracks.values())
    print("=== Genius Source Summary ===")
    for src, cnt in counts.most_common():
        print(f"  {src}: {cnt}")
    classified = sum(1 for t in final_tracks.values() if t.get("final_language"))
    print(f"✅ Saved {len(final_tracks)} tracks ({classified} classified by stages 2+3) → {output_file}")
    return output_file

if __name__ == "__main__":
    augment_with_genius()
