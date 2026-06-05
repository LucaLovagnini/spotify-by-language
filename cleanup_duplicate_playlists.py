"""One-off cleanup: list and delete all playlists matching '^My .* Songs$'
owned by the current user.

Spotify has no real "delete playlist" — `current_user_unfollow_playlist` is
the canonical way to remove your own playlist from your library (it unfollows
it; since you're the owner, the playlist becomes orphaned and disappears
from your view).

Run this once after the scope update to clean up duplicates accumulated by
previous runs that couldn't see private playlists.
"""
import re

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

import config

load_dotenv()

PLAYLIST_NAME_RX = re.compile(r"^My .* Songs$", re.I)


def main():
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(scope=config.SPOTIFY_PLAYLIST_SCOPE)
    )
    user_id = sp.me()["id"]
    print(f"Authenticated as: {user_id}")

    targets = []
    results = sp.current_user_playlists(limit=50)
    while results:
        for pl in results["items"]:
            if not pl:
                continue
            name = pl.get("name", "")
            owner_id = pl.get("owner", {}).get("id")
            if owner_id == user_id and PLAYLIST_NAME_RX.match(name):
                targets.append({
                    "id": pl["id"],
                    "name": name,
                    "tracks": pl.get("tracks", {}).get("total"),
                })
        if results.get("next"):
            results = sp.next(results)
        else:
            break

    print(f"\nFound {len(targets)} 'My * Songs' playlists owned by {user_id}:")
    for t in targets:
        print(f"  - {t['name']!r:35} tracks={t['tracks']:>5}  id={t['id']}")

    if not targets:
        print("Nothing to delete.")
        return

    print(f"\nDeleting {len(targets)} playlists...")
    for t in targets:
        sp.current_user_unfollow_playlist(t["id"])
        print(f"  ✓ deleted {t['name']!r}")
    print(f"\nDone. {len(targets)} playlists removed.")


if __name__ == "__main__":
    main()
