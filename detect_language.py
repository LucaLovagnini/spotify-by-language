import json
import os
from collections import Counter, defaultdict

from langdetect import detect_langs, DetectorFactory

import config

DetectorFactory.seed = 0

INPUT_FILE = config.TRACKS_GENIUS_CLASSIFIED
OUTPUT_FILE = config.LANGUAGE_IDENTIFIED_GENIUS
WEIGHTS = config.FIELD_WEIGHTS


def safe_top_lang(text):
    if not text or len(text.strip()) < config.MIN_TEXT_LEN:
        return "unknown", 0.0
    try:
        langs = detect_langs(text)
        top = langs[0] if langs else None
        return (top.lang, float(top.prob)) if top else ("unknown", 0.0)
    except Exception:
        return "unknown", 0.0


def detect_track_language(track):
    fields = [("name", track["name"], WEIGHTS["name"]),
              ("artist", track["artist"], WEIGHTS["artist"]),
              ("album", track["album"], WEIGHTS["album"])]

    scores = defaultdict(float)
    per_field = {}

    for fname, text, weight in fields:
        lang, prob = safe_top_lang(text)
        per_field[fname] = {"lang": lang, "prob": prob}
        if lang != "unknown":
            scores[lang] += prob * weight

    if not scores:
        return "unknown", 0.0, per_field

    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score >= config.LANGUAGE_CONF_THRESHOLD:
        return best_lang, best_score, per_field
    return "unknown", best_score, per_field


def apply_metadata_fallback(input_file=INPUT_FILE, output_file=OUTPUT_FILE):
    """Stage 4: fill in `final_language` for tracks no earlier stage could classify.

    Reads stage-3 output. For each track:
    - If `final_language` is already set (by stage 2 title marker or stage 3 Genius),
      pass through unchanged.
    - Otherwise, run weighted langdetect on name/artist/album, write `final_language`
      (a language code or "unknown") plus `source="metadata"`.
    """
    with open(input_file, "r", encoding="utf-8") as f:
        tracks = json.load(f)

    results = []
    for track in tracks:
        if track.get("final_language"):
            results.append(track)
            continue
        lang, conf, details = detect_track_language(track)
        # Only stamp source=metadata when no upstream stage recorded one.
        # Preserves genius_not_found / genius_empty provenance.
        merged = {
            **track,
            "final_language": lang,
            "confidence": round(conf, 4),
            "details": details,
        }
        if not merged.get("source"):
            merged["source"] = "metadata"
        results.append(merged)

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    counts = Counter(t["final_language"] for t in results)
    print("=== Final Language Summary ===")
    for lang, cnt in counts.most_common():
        print(f"  {lang}: {cnt}")
    print(f"✅ Saved {len(results)} tracks with language info → {output_file}")
    return output_file


if __name__ == "__main__":
    apply_metadata_fallback()
