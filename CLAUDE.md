# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Setup:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in SPOTIPY_* and GENIUS_ACCESS_TOKEN
```

Run the full pipeline (creates one playlist per language code; `min_songs` defaults to 10):
```bash
python orchestratory.py en,es,fr,instrumental [min_songs] [--reconcile]
```

Run a single stage standalone (each stage reads its predecessor's JSON):
```bash
python save_spotify_tracks.py     # stage 1
python tag_instrumentals.py       # stage 2
python fetch_lrclib.py            # stage 3 (resumable — see below)
python fetch_genius_lyrics.py     # stage 4 (resumable, fallback for LRCLib misses)
python detect_language.py         # stage 5 (metadata fallback)
python create_playlist.py data/language_identified_genius.json en,es,fr [min_songs] [--reconcile]  # stage 6
python lanuage_summary.py data/language_identified_genius.json  # stats only
```

Force Spotify re-authentication: delete `.cache` in the repo root.

No test suite, linter, or build step.

## Architecture

Six-stage JSON-on-disk pipeline. Each stage is a single-responsibility module that reads its predecessor's output and writes its own file. `orchestratory.py` chains them; `config.py` centralizes paths and tuning constants.

| # | Module | Job | Output |
|---|---|---|---|
| 1 | `save_spotify_tracks.py` | Fetch liked tracks via Spotipy (scope `user-library-read`), flushes every `FLUSH_INTERVAL` items so partial runs survive interruption. | `data/spotify_tracks.json` |
| 2 | `tag_instrumentals.py` | Title-marker regex (`INSTRUMENTAL_TITLE_RX`) for record-label alternate-version suffixes like `- Instrumental Version`, `(Karaoke)`, `(... Piano Version)`. Sets `final_language="instrumental"` + `source="title_marker"` on matches. Untouched tracks have no `final_language` key. Fast — pure regex, no API, always reprocesses. | `data/tracks_tagged.json` |
| 3 | `fetch_lrclib.py` | LRCLib classification. Skips tracks where `final_language` is already set. For the rest: GET `https://lrclib.net/api/get?artist_name=…&track_name=…`. `instrumental: true` → `final_language="instrumental"`. Non-empty `plainLyrics` → `langdetect` on the FULL lyrics body (not a snippet). Free, no auth, no documented rate limit (measured ~31 req/min with 0 errors). Writes after every track for resumability. | `data/tracks_lrclib.json` |
| 4 | `fetch_genius_lyrics.py` | Genius fallback for tracks LRCLib didn't classify. Skips tracks where `final_language` is already set. For the rest: search Genius, pick a hit via `_pick_genius_hit` (artist must fuzzy-match, translation pages rejected), scrape the lyrics page, detect instrumental markers, otherwise `langdetect` on the 400-char snippet. Writes after every track for resumability. | `data/tracks_genius_classified.json` |
| 5 | `detect_language.py` | Metadata fallback. For tracks where `final_language` is still unset (LRCLib + Genius both gave up), run weighted `langdetect` on name/artist/album. Sets `source="metadata"`. After this stage, every track has `final_language` set. | `data/language_identified_genius.json` |
| 6 | `create_playlist.py` | For each language code, create/update `My <LANG> Songs` playlist (scope `playlist-modify-public playlist-modify-private`). Idempotent: looks up existing playlist by name, only adds tracks not already present. With `--reconcile`, also removes tracks whose `final_language` no longer matches. | Spotify playlists |

### Data contract

A track's `final_language` is written **exactly once**, by whichever stage first classifies it. Stages 3, 4, and 5 check `if track.get("final_language")` and pass through unchanged when set. This is a deliberate architectural choice — see `memory/feedback_single_responsibility_stages.md`. Don't add fallback branches that check the upstream `source` value; data-driven handoffs only.

### `source` vocabulary (terminal — set once per track)

- `title_marker` — stage 2 matched a structural alternate-version regex
- `lrclib_instrumental` — stage 3 found a LRCLib record with `instrumental: true`
- `lrclib` — stage 3 got `plainLyrics`; language detected by `langdetect` on the full lyrics body
- `lrclib_empty` — LRCLib record exists but has no lyrics and is not marked instrumental; `final_language` left unset for stage 4
- `lrclib_not_found` — LRCLib returned 404 (or an error); `final_language` left unset for stage 4
- `genius_instrumental` — stage 4 detected `"This song is an instrumental"` in the Genius page HTML, or a `[Strumentale]`/`[Instrumental]`-only lyrics body
- `genius` — stage 4 got real lyrics; language detected by `langdetect` on a 400-char snippet
- `genius_empty` — Genius page exists but no lyrics and no instrumental marker; `final_language` left unset for stage 5
- `genius_not_found` — no Genius hit survived the artist-match + translation filter; `final_language` left unset for stage 5
- `metadata` — stage 5 set it via weighted langdetect on title/artist/album

### Why LRCLib before Genius

LRCLib was added after measuring Genius's failure modes on the corpus:
- Genius's API `language` field is unreliable for instrumentals (54% of 530 known-instrumental tracks have a `language` populated, almost all defaulted to `en` by community editors). Using it would silently misclassify hundreds of instrumentals as English vocal tracks.
- Genius's lyrics scrape returns only a 400-char snippet, which `langdetect` misreads when the snippet is dominated by filler vocables (e.g. `Le-ri, le-re-le…` in Spanish flamenco lyrics — see Novia Moderna case).

LRCLib avoids both:
- Its `instrumental` boolean is data-model-enforced (synced-lyrics tracks can't be instrumental), so it's actually reliable when populated.
- It returns full plain lyrics, not a snippet, so `langdetect` runs on enough text to be stable.

Measured against a sample of 530 known instrumentals + 100 random vocal tracks: 72% coverage on instrumentals (89% of which were correctly flagged `instrumental: true`), 96% coverage on vocal tracks, 99% agreement with prior classification — and the one disagreement was LRCLib correcting a prior langdetect false positive (`so` → `en` on a track in English).

### Stage 4 hit-selection logic (`fetch_genius_lyrics.py`)

`_pick_genius_hit` is load-bearing for accuracy. It iterates Genius search hits in rank order and rejects:
1. Hits whose title or primary artist matches `TRANSLATION_TITLE_RX` (translation pages — `Traduction`, `Türkçe Çeviri`, `Tradução`, `Übersetzung`, `Перевод`, etc. across 13 languages).
2. Hits whose `primary_artist.name` doesn't fuzzy-match the Spotify artist via `_artist_matches`. The matcher Unicode-normalizes (NFD + strip combining marks), drops punctuation, and accepts on exact equality, substring containment, or token-subset overlap. Handles `Frankie Chávez` ≡ `Frankie Chavez`, `Tycho` ≡ `Tycho (US)`.

Returns `None` (treated as `genius_not_found`) if no hit survives — **no blind fallback to `hits[0]`**. This was added after diagnosing two real false positives: Beatles `Carry That Weight - Remastered` returned a Brazilian Portuguese podcast review, and Milky Chance `Loveland - Bonus Track` returned a French translation page.

### Resume behavior

Stages 3 and 4 are the expensive stages. Both write after every track and on restart load their existing output, skipping tracks whose `source` is in their own `TERMINAL_SOURCES`. Interrupting and re-running is safe.

- Stage 3 (LRCLib): ~31 req/min sustained, no throttle needed. ~3 minutes for ~5000 tracks if cache is cold.
- Stage 4 (Genius): only runs on tracks LRCLib didn't classify (typically the residual after LRCLib covers ~70-95% depending on catalog). ~2s/track including search + page scrape + 1s throttle.

Stages 2 and 5 are fast (seconds); they always reprocess from scratch.

### Two Spotify OAuth scopes

Stage 1 uses `user-library-read`, stage 6 uses `playlist-modify-public playlist-modify-private`. They share `.cache` — switching between them may trigger a re-auth.

### `--reconcile` (stage 6)

Default off. When enabled, `create_playlist.py` computes both `to_add` (in classification, not in playlist) and `to_remove` (in playlist, not in classification) per language, then applies both. Off by default because pruning will also remove tracks the user manually added to a `My <LANG> Songs` playlist outside this tool. Use after re-classification to clean up stale entries.

## Notes for future edits

- Filename typos (`orchestratory.py`, `lanuage_summary.py`) are intentional in the current tree — don't rename without updating imports.
- `lanuage_summary.py` is a standalone reporting tool, not part of the pipeline.
- `config.LANGUAGE_IDENTIFIED` was removed in the refactor — don't reintroduce it. Use `TRACKS_TAGGED` (stage 2 output), `TRACKS_LRCLIB` (stage 3 output), `TRACKS_GENIUS_CLASSIFIED` (stage 4 output), or `LANGUAGE_IDENTIFIED_GENIUS` (stage 5 final output).
- The README's "Algorithm" section describes the pipeline; keep it in sync if stages change.
