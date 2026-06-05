import json
import os
import sys
import time

import spotipy
from dotenv import load_dotenv
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

import config

load_dotenv()
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

SCOPE = "playlist-modify-public playlist-modify-private"

# -----------------------------
# Spotify Client
# -----------------------------
def get_spotify_client():
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=config.SPOTIFY_PLAYLIST_SCOPE
    ))

# -----------------------------
# Helpers
# -----------------------------
def chunked(iterable, n):
    for i in range(0, len(iterable), n):
        yield iterable[i:i + n]

def _retry_call(fn, max_retries=5):
    """Retry a single Spotify API call on 429/5xx; re-raise on other errors."""
    for _ in range(max_retries):
        try:
            return fn()
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", "5"))
                print(f"⏱ Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
            elif 500 <= e.http_status < 600:
                print(f"⚠️ Server error {e.http_status}, retrying in 2s...")
                time.sleep(2)
            else:
                raise
    raise RuntimeError(f"Spotify call failed after {max_retries} retries")

def find_playlist_by_name(sp, user_id, name):
    results = sp.current_user_playlists(limit=50)
    while results:
        for pl in results["items"]:
            if pl and pl.get("owner", {}).get("id") == user_id and pl.get("name") == name:
                return pl
        if results.get("next"):
            results = sp.next(results)
        else:
            return None
    return None

def get_playlist_track_ids(sp, playlist_id):
    ids = set()
    results = sp.playlist_items(
        playlist_id, fields="items(track(id)),next", additional_types=["track"]
    )
    while results:
        for item in results["items"]:
            t = item.get("track") if item else None
            if t and t.get("id"):
                ids.add(t["id"])
        if results.get("next"):
            results = sp.next(results)
        else:
            break
    return ids

# -----------------------------
# Create Playlist for One Language
# -----------------------------
def create_playlist(input_file, language, min_songs=10, sp=None, max_retries=5, reconcile=False):
    with open(input_file, "r", encoding="utf-8") as f:
        tracks = json.load(f)

    lang_tracks = [t["id"] for t in tracks if t.get("final_language") == language]

    if sp is None:
        sp = get_spotify_client()

    total_tracks = len(lang_tracks)
    if total_tracks < min_songs:
        print(f"⚠️ Skipping {language}: only {total_tracks} tracks (need at least {min_songs})")
        return

    user_id = sp.me()["id"]
    playlist_name = f"My {language.upper()} Songs"
    expected_ids = set(lang_tracks)

    existing = _retry_call(
        lambda: find_playlist_by_name(sp, user_id, playlist_name),
        max_retries=max_retries,
    )
    if existing:
        playlist_id = existing["id"]
        existing_ids = _retry_call(
            lambda: get_playlist_track_ids(sp, playlist_id),
            max_retries=max_retries,
        )
        to_add = [tid for tid in lang_tracks if tid not in existing_ids]
        to_remove = (
            [tid for tid in existing_ids if tid not in expected_ids] if reconcile else []
        )
        print(
            f"♻️ Reusing playlist '{playlist_name}' "
            f"({len(existing_ids)} existing, {len(to_add)} to add"
            f"{', ' + str(len(to_remove)) + ' to remove' if reconcile else ''})"
        )
    else:
        playlist = _retry_call(
            lambda: sp.user_playlist_create(user_id, playlist_name, public=False),
            max_retries=max_retries,
        )
        playlist_id = playlist["id"]
        to_add = lang_tracks
        to_remove = []
        print(f"🎶 Created playlist '{playlist_name}' ({len(to_add)} tracks to add)")
        time.sleep(1)

    if not to_add and not to_remove:
        print(f"✅ Playlist '{playlist_name}' is already up to date.")
        return

    removed = 0
    for batch in chunked(to_remove, 100):
        _retry_call(
            lambda b=batch: sp.playlist_remove_all_occurrences_of_items(playlist_id, b),
            max_retries=max_retries,
        )
        removed += len(batch)
        print(f"   ✂ Removed {removed}/{len(to_remove)} stale tracks...")
        time.sleep(1)

    added = 0
    for batch in chunked(to_add, 100):
        _retry_call(
            lambda b=batch: sp.playlist_add_items(playlist_id, b),
            max_retries=max_retries,
        )
        added += len(batch)
        print(f"   → Added {added}/{len(to_add)} tracks...")
        time.sleep(1)

    print(
        f"✅ Finished playlist '{playlist_name}' "
        f"(+{added} / -{removed}, {total_tracks} total candidates)."
    )

# -----------------------------
# Create Playlists for Many Languages
# -----------------------------
def create_playlists_from_file(input_file, languages, min_songs=10, sp=None, reconcile=False):
    if sp is None:
        sp = get_spotify_client()

    for lang in languages:
        try:
            create_playlist(input_file, lang, min_songs=min_songs, sp=sp, reconcile=reconcile)
        except Exception as e:
            print(f"❌ Failed to create playlist for {lang}: {e}")

# -----------------------------
# CLI
# -----------------------------
def main():
    args = sys.argv[1:]
    reconcile = False
    if "--reconcile" in args:
        reconcile = True
        args.remove("--reconcile")

    if len(args) < 2:
        print("Usage: python create_playlist.py <tracks_file.json> <lang1,lang2,...> [min_songs] [--reconcile]")
        sys.exit(1)

    input_file = args[0]
    languages = args[1].split(",")
    min_songs = int(args[2]) if len(args) > 2 else 10

    sp = get_spotify_client()
    create_playlists_from_file(input_file, languages, min_songs, sp=sp, reconcile=reconcile)

if __name__ == "__main__":
    main()
