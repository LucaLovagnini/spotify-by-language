import sys

from create_playlist import create_playlists_from_file
from detect_language import apply_metadata_fallback
from fetch_genius_lyrics import augment_with_genius
from fetch_lrclib import classify_with_lrclib
from save_spotify_tracks import fetch_saved_tracks
from tag_instrumentals import tag_instrumentals


def main():
    args = sys.argv[1:]
    reconcile = False
    if "--reconcile" in args:
        reconcile = True
        args.remove("--reconcile")

    if len(args) < 1:
        print("Usage: python orchestrator.py <lang1,lang2,...> [min_songs] [--reconcile]")
        sys.exit(1)

    languages = args[0].split(",")
    min_songs = int(args[1]) if len(args) > 1 else 10

    print("\n🎵 Stage 1: Fetching Spotify tracks...")
    spotify_file = fetch_saved_tracks()

    print("\n🏷️  Stage 2: Tagging instrumentals by title heuristic...")
    tagged_file = tag_instrumentals(input_file=spotify_file)

    print("\n🎼 Stage 3: Classifying via LRCLib (instrumental field + full lyrics)...")
    lrclib_file = classify_with_lrclib(input_file=tagged_file)

    print("\n📝 Stage 4: Genius fallback for tracks LRCLib didn't classify...")
    genius_file = augment_with_genius(input_file=lrclib_file)

    print("\n🗣️  Stage 5: Metadata fallback for unclassified tracks...")
    final_file = apply_metadata_fallback(input_file=genius_file)

    print("\n📀 Stage 6: Creating playlists...")
    create_playlists_from_file(
        input_file=final_file,
        languages=languages,
        min_songs=min_songs,
        reconcile=reconcile,
    )

    print("✅ Orchestration complete!")

if __name__ == "__main__":
    main()
