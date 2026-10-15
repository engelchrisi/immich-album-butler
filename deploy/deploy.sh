#!/bin/sh
# Deploy a known source snapshot to a Linux host with systemd.
#
# Usage:
#   sh deploy/deploy.sh root@example-host
#   sh deploy/deploy.sh root@example-host --working-tree
#   sh deploy/deploy.sh root@example-host --design-only
#
# The default archives HEAD, so deployments are reproducible. --working-tree
# explicitly includes local changes instead, for an intentional test deploy.
set -eu

usage() {
    cat <<'EOF'
Usage: sh deploy/deploy.sh USER@HOST [--working-tree] [--design-only]

Deploys immich-album-butler to USER@HOST, then restarts both services.

  --working-tree  package the current working tree instead of committed HEAD
  --design-only   restart only immich-album-butler-design
EOF
}

target=${1:-}
case "$target" in
    -h|--help) usage; exit 0 ;;
esac
[ -n "$target" ] && [ "${target#-}" = "$target" ] || { usage >&2; exit 2; }
shift

source=HEAD
services='immich-album-butler immich-album-butler-design'
while [ "$#" -gt 0 ]; do
    case "$1" in
        --working-tree) source=working-tree ;;
        --design-only) services=immich-album-butler-design ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

for command in git scp ssh tar; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "deploy needs $command" >&2
        exit 1
    }
done

root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "run this from an immich-album-butler checkout" >&2
    exit 1
}
cd "$root"

archive=$(mktemp "${TMPDIR:-/tmp}/immich-album-butler-deploy.XXXXXX.tar.gz")
remote_archive=/tmp/immich-album-butler-deploy.tar.gz
remote_dir=/tmp/immich-album-butler-deploy
cleanup() { rm -f "$archive"; }
trap cleanup EXIT HUP INT TERM

case "$source" in
    HEAD)
        git archive --format=tar.gz --output="$archive" HEAD -- \
            immich_album_butler deploy examples pyproject.toml
        ;;
    working-tree)
        tar -czf "$archive" immich_album_butler deploy examples pyproject.toml
        ;;
esac

echo "Uploading $source to $target..."
scp "$archive" "$target:$remote_archive"

# The source tree may have passed through a Windows checkout. Normalise all
# deployment files before /bin/sh and systemd read them.
ssh "$target" "set -eu
rm -rf '$remote_dir'
mkdir -p '$remote_dir'
tar -xzf '$remote_archive' -C '$remote_dir'
find '$remote_dir/deploy' -type f -exec sed -i 's/\\r//' {} +
sh '$remote_dir/deploy/install.sh'
systemctl restart $services
systemctl is-active $services
rm -rf '$remote_dir' '$remote_archive'
"

echo "Deployment complete."
