# 🎵 Spotify Playlist by Language

Automatically organize your Spotify saved tracks into language-specific playlists using AI-powered language detection and lyrics analysis.

## ✨ Features

- **Smart Language Detection**: Uses lyrics (LRCLib first, Genius as fallback) and metadata (track names, artists, albums) to detect song languages
- **Multi-Language Support**: Create playlists for multiple languages simultaneously
- **Tiered Sources**: LRCLib → Genius → metadata, each falling back only when the previous tier can't classify the track
- **Batch Processing**: Efficiently handles large music libraries
- **Rate Limit Handling**: Built-in retry logic for API rate limits
- **Resume Support**: Can resume interrupted operations

## 🚀 How It Works

A 6-stage pipeline, each stage with a single responsibility:

1. **Fetch Tracks**: Retrieves all your saved Spotify tracks
2. **Tag Instrumentals**: Catches structural alternate-version markers in titles (`- Instrumental`, `(Karaoke Version)`, `(... Piano Version)`)
3. **LRCLib Classification**: Queries LRCLib for each remaining track; uses its `instrumental` boolean for instrumentals, otherwise runs language detection on the FULL lyrics body (free, no auth)
4. **Genius Fallback**: For tracks LRCLib didn't classify, searches Genius and scrapes the lyrics page (instrumental markers or `langdetect` on a 400-char snippet)
5. **Metadata Fallback**: For tracks neither LRCLib nor Genius could classify, falls back to weighted language detection on track name + artist + album
6. **Playlist Creation**: Creates one playlist per language code in your Spotify account

## 📋 Prerequisites

- Python 3.9+
- Spotify Premium account (recommended)
- Spotify Developer App credentials
- Genius API token (optional but recommended)

## 🛠️ Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/LucaLovagnini/spotifyByLanguage
   cd spotifyByLanguage
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your API credentials:
   ```
    # Spotify API credentials
    SPOTIPY_CLIENT_ID=your_spotify_client_id_here
    SPOTIPY_CLIENT_SECRET=your_spotify_client_secret_here
    SPOTIPY_REDIRECT_URI=http://127.0.0.1:7777/callback
    
    # Genius API (optional)
    GENIUS_ACCESS_TOKEN=your_genius_token_here
   ```

## 🔑 API Setup

### Spotify API
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Note down your `Client ID` and `Client Secret`
4. Add `http://127.0.0.1:7777/callback` to Redirect URIs
5. Under Which API/SDKs are you planning to use? select Web Api

### Genius API
1. Go to [Genius API](https://genius.com/api-clients)
2. Create a new API client
3. For App Website URL use https://localhost
4. Generate an access token

## 🎯 Usage

### Quick Start

```bash
python orchestratory.py en,es,it,instrumental
```

This creates playlists for English, Spanish, Italian, and instrumental songs. Use ISO 639-1 codes (`en`, not `eng`).

### Advanced Usage

```bash
python orchestratory.py en,es,fr,de,it,instrumental 15
```

Creates playlists for 6 buckets, requiring at least 15 songs per playlist.

```bash
python orchestratory.py en,es,fr,instrumental --reconcile
```

Adds `--reconcile`: also **removes** tracks from existing playlists whose classification has changed since the last run. Use after re-classifying to clean up stale entries. Off by default — pruning will also remove any tracks you manually added to a `My <LANG> Songs` playlist outside this tool.

### Individual Modules
You can also run individual stages:

```bash
# Stage 1: Fetch Spotify tracks
python save_spotify_tracks.py

# Stage 2: Tag instrumentals by title heuristic
python tag_instrumentals.py

# Stage 3: LRCLib classification (resumable, free, no auth)
python fetch_lrclib.py

# Stage 4: Genius fallback for tracks LRCLib missed (resumable)
python fetch_genius_lyrics.py

# Stage 5: Metadata fallback for tracks neither source could classify
python detect_language.py

# Stage 6: Create playlists
python create_playlist.py data/language_identified_genius.json en,es,fr,instrumental [--reconcile]
```

## 📊 Language Detection Algorithm

### How a track's `final_language` is decided

LRCLib is the primary lyrics source; Genius is a fallback when LRCLib doesn't have the track; metadata is the last resort. Each track's language is set **exactly once**, by whichever stage first classifies it:

1. **Title marker** (stage 2) → `instrumental`, e.g. `Still D.R.E. - Instrumental Version`
2. **LRCLib `instrumental: true`** (stage 3) → `instrumental` (real, data-model-enforced signal)
3. **LRCLib full lyrics** (stage 3) → language detected by running `langdetect` on the complete lyrics body (no snippet truncation)
4. **Genius "this song is an instrumental" marker** (stage 4) → `instrumental`, e.g. Brian Eno's *Lanzarote*
5. **Genius lyrics** (stage 4) → language detected by running `langdetect` on a 400-char snippet
6. **Metadata fallback** (stage 5) → weighted `langdetect` on name (0.5) + artist (0.3) + album (0.2), as a last resort when neither source helps

Stage 4 also filters its Genius hits: rejects translation pages (e.g. `(Traduction Française)`, `(Türkçe Çeviri)`) and rejects hits whose artist doesn't fuzzy-match the Spotify artist. This avoids cases where Genius's search returns a podcast review or a French translation as the top hit.

### 📋 Supported Languages
The system supports all languages detected by the `langdetect` library, including:
- **en** — English
- **es** — Spanish
- **fr** — French
- **de** — German
- **it** — Italian
- **pt** — Portuguese
- **ru** — Russian
- **ja** — Japanese
- **ko** — Korean
- **zh** — Chinese
- And the special bucket **instrumental** for tracks without lyrics
- And many more...

### 🚩 Known Limitations

- **Tracks neither LRCLib nor Genius indexes** fall back to metadata-based detection, which is a weak proxy. The `source` field in the output JSON shows which tier classified each track: `lrclib`, `lrclib_instrumental`, `genius`, `genius_instrumental`, or `metadata`.

- **Metadata-based language detection can be misleading** when title language differs from lyric language (e.g., *Madonna – La Isla Bonita* would be classified as `es` based on the title, even though the lyrics are primarily in English). The pipeline only falls back to metadata when both lyrics sources returned nothing — most tracks get lyrics-validated.

- **Filler-vocable-heavy songs can fool `langdetect`** even with full lyrics. Example: Lin Cortés *Novia Moderna* has 5 short lines of Spanish followed by a long tail of `Lele-lelé` syllables; langdetect on the full LRCLib lyrics returns `fr: 0.43, it: 0.42, ca: 0.15` — Spanish doesn't crack the top 3. Raising the confidence threshold and falling through to metadata is one mitigation; swapping to a more robust detector (fasttext, lingua-py) is another.

## ⚙️ Configuration
### Language Detection Settings
You can modify these in `config.py`:
- Minimum confidence for the metadata fallback (default: 0.80) — `LANGUAGE_CONF_THRESHOLD`
- Field weights for the metadata fallback — `FIELD_WEIGHTS`
- Minimum text length before `langdetect` runs — `MIN_TEXT_LEN`
- Flush interval (Spotify pagination) — `FLUSH_INTERVAL`

### Rate Limiting
Built-in rate limiting with retries:
- Spotify: 50 songs per page; stage 6 honors `Retry-After` on 429 and retries 5xx
- LRCLib: no throttle in practice (measured ~31 req/min with 0 errors); 429 backoff handler defensive only
- Genius: 1-second between requests, with linear backoff (10s × attempt) on 429

## 🔍 Troubleshooting
### Common Issues
**"No module named 'spotipy'"**
``` bash
pip install -r requirements.txt
```
**"Invalid client credentials"**
- Check your Spotify API credentials in `.env`
- Ensure redirect URI matches exactly

**"Rate limit exceeded"**
- For Genius, the script handles this automatically with retries and exponential backoff
- For Spotify, reduce the number of fetched songs per request through `FLUSH_INTERVAL`

**"Permission denied" for playlists**
- Ensure your Spotify app has the correct scopes (stage 1 needs `user-library-read`; stage 6 needs `playlist-read-private playlist-modify-public playlist-modify-private` — the read scope is required so the script can detect already-existing playlists and avoid creating duplicates)
- Re-authenticate by deleting file `.cache` (also required after any scope change)

**Duplicate "My &lt;LANG&gt; Songs" playlists appear after each run**
- This happens when `playlist-read-private` is missing from the scope: the script can't see its own previous (private) playlists and creates a new one every time. The scope is included by default now; if you upgraded from an older version, delete `.cache` to force a fresh auth with the new scope.

## ⚠️ Disclaimer
- This tool accesses your Spotify library and creates playlists
- Language detection accuracy varies by song metadata quality
- Genius lyrics fetching is rate-limited and optional
- Respect API terms of service and rate limits

## 🙏 Acknowledgments
- [Spotipy](https://spotipy.readthedocs.io/) — Spotify Web API wrapper
- [LRCLib](https://lrclib.net/) — Free synced-lyrics database with a reliable `instrumental` flag (no auth, no rate limit)
- [Genius API](https://docs.genius.com/) — Lyrics and song information (used as fallback)
- [langdetect](https://github.com/Mimino666/langdetect) — Language detection library
- [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) — Web scraping (Genius lyrics pages)

Made with ❤️ for music lovers who appreciate linguistic diversity in their playlists!
