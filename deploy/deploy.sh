#!/bin/sh
# Identical entry point in PyImmichFrame, immich-album-butler and immich-dup-butler.
# Example from the repository root:  sh deploy/deploy.sh root@example-host
# Full usage and rollback behaviour: deploy/README.md
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/deploy.py" "$@"
