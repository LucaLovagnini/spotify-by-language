import json
import os
import re
from collections import Counter

import config

# Anchored patterns for record-label "alternate version" markers in track names.
# These are structural metadata, not language signals — only matched at end of title.
INSTRUMENTAL_TITLE_RX = re.compile(
    r"-\s+(Instrumental|Karaoke)(\s+Version)?\s*$"
    r"|\(\s*(Instrumental|Karaoke)(\s+Version)?\s*\)\s*$"
    r"|\(\s*[A-Za-z ]+\s+Piano Version\s*\)\s*$",
    re.I,
)


def looks_instrumental_by_title(track):
    return bool(INSTRUMENTAL_TITLE_RX.search(track.get("name") or ""))


def tag_instrumentals(input_file=config.SPOTIFY_TRACKS, output_file=config.TRACKS_TAGGED):
    with open(input_file, "r", encoding="utf-8") as f:
        tracks = json.load(f)

    results = []
    for track in tracks:
        if looks_instrumental_by_title(track):
            results.append({
                **track,
                "final_language": "instrumental",
                "source": "title_marker",
            })
        else:
            results.append(dict(track))

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    tagged = sum(1 for r in results if r.get("final_language") == "instrumental")
    print(f"✅ Tagged {tagged}/{len(results)} tracks as instrumental → {output_file}")
    return output_file


if __name__ == "__main__":
    tag_instrumentals()
