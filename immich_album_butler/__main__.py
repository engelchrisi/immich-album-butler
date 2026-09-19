"""Allows `python3 -m immich_album_butler`, which is how it runs where pip is absent."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
