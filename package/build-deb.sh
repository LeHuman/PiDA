#!/bin/bash
# Build the pida .deb. Runs on the Pi (or any arm64/host with python3 +
# dpkg-deb); wheels are fetched fresh so the deb installs fully offline.
#   ./package/build-deb.sh [version]     (default 1.0.0)
set -euo pipefail
cd "$(dirname "$0")/.."

VER=${1:-1.0.0}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
OUT=pida_${VER}_all.deb

echo "== staging =="
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/opt/pida/wheels" \
         "$STAGE/opt/pida/src" \
         "$STAGE/opt/pida/scripts" \
         "$STAGE/lib/systemd/system"

install -m 644 src/pipeline.py src/server.py src/pida.schema.json "$STAGE/opt/pida/src/"
install -m 644 pyproject.toml README.md "$STAGE/opt/pida/"
install -m 644 scripts/cli.py "$STAGE/opt/pida/scripts/cli.py"
install -m 644 package/pi-api.service "$STAGE/lib/systemd/system/pi-api.service"
install -m 644 package/README.icons package/README.library "$STAGE/usr/share/pida/"
install -m 644 package/control "$STAGE/DEBIAN/control"
sed -i "s/^Version: .*/Version: $VER/" "$STAGE/DEBIAN/control"
install -m 755 package/postinst package/prerm package/postrm "$STAGE/DEBIAN/"

echo "== vendoring wheels (offline install payload) =="
python3 -m pip wheel . --wheel-dir "$STAGE/opt/pida/wheels" \
    --only-binary=:all: -q

echo "== building =="
dpkg-deb --root-owner-group --build "$STAGE" "$OUT"
echo "== built: $(pwd)/$OUT =="
dpkg-deb --info "$OUT" | head -n 12
