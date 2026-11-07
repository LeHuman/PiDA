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
if [ -n "${PIDA_WHEEL_PLATFORMS:-}" ]; then
    # Cross-vendor from CI (x86_64) for the arm64 target host: build the
    # project wheel (pure Python), then fetch dependency wheels for the
    # target platform. pip needs exact platform tags, so pass every
    # manylinux glibc tag up to the target's (Raspberry Pi OS: 2.36).
    python3 -m pip wheel . --no-deps --wheel-dir "$STAGE/opt/pida/wheels" -q
    platforms=""
    for p in $PIDA_WHEEL_PLATFORMS; do platforms="$platforms --platform $p"; done
    # shellcheck disable=SC2086
    python3 -m pip download . --dest "$STAGE/opt/pida/wheels" \
        $platforms \
        --python-version "${PIDA_WHEEL_PY:-3.11}" \
        --implementation cp \
        --abi "${PIDA_WHEEL_ABI:-cp311}" \
        --only-binary=:all: --no-cache-dir -q
else
    python3 -m pip wheel . --wheel-dir "$STAGE/opt/pida/wheels" \
        --only-binary=:all: -q
fi

echo "== building =="
dpkg-deb --root-owner-group --build "$STAGE" "$OUT"
echo "== built: $(pwd)/$OUT =="
dpkg-deb --info "$OUT" | head -n 12
