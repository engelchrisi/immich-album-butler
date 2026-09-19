#!/bin/sh
# Install immich-album-butler on a Linux host with systemd.
#
# No pip, no venv, no Docker: the package is pure standard library, so it is
# simply copied where Python can find it. That is what makes it installable on
# a minimal container with no route to PyPI.
#
# Run as root from a checkout:   sudo deploy/install.sh
set -eu

PREFIX=${PREFIX:-/opt/immich-album-butler}
CONFIG_DIR=${CONFIG_DIR:-/etc/immich-album-butler}
STATE_DIR=${STATE_DIR:-/var/lib/immich-album-butler}
USER_NAME=${USER_NAME:-immich-album-butler}

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

python3 - <<'PY' || { echo "Python 3.11 or newer is required" >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
    echo "created system user $USER_NAME"
fi

install -d -m 755 "$PREFIX"
rm -rf "$PREFIX/immich_album_butler"
cp -r "$source_dir/immich_album_butler" "$PREFIX/"
find "$PREFIX" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

install -d -m 750 -o root -g "$USER_NAME" "$CONFIG_DIR"
install -d -m 750 -o "$USER_NAME" -g "$USER_NAME" "$STATE_DIR"

if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    install -m 640 -o root -g "$USER_NAME" \
        "$source_dir/examples/config.toml" "$CONFIG_DIR/config.toml"
    echo "installed a starter config.toml -- the whole configuration lives there"
fi

if [ ! -f "$CONFIG_DIR/immich-album-butler.env" ]; then
    install -m 640 -o root -g "$USER_NAME" \
        "$source_dir/deploy/immich-album-butler.env.example" \
        "$CONFIG_DIR/immich-album-butler.env"
    echo "installed an empty env file -- put your IMMICH_KEY in it"
fi

install -m 644 "$source_dir/deploy/immich-album-butler.service" \
    "$source_dir/deploy/immich-album-butler-design.service" \
    /etc/systemd/system/
systemctl daemon-reload

cat <<EOF

Installed. Next:
  1. edit $CONFIG_DIR/config.toml         (set 'server'; albums go here too)
  2. edit $CONFIG_DIR/immich-album-butler.env   (set IMMICH_KEY), and add a
     design-mode login with:  immich-album-butler passwd <name>
  3. sudo -u $USER_NAME env IMMICH_KEY=... python3 -m immich_album_butler \\
         --config-dir $CONFIG_DIR --state-dir $STATE_DIR check
  4. systemctl enable --now immich-album-butler

Design mode is not enabled on purpose. Start it only when you need it:
  systemctl start immich-album-butler-design
EOF
