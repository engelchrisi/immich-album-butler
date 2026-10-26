#!/bin/sh
# Identical entry point in PyImmichFrame, immich-album-butler and immich-dup-butler.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/deploy.py" "$@"
