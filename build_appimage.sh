#!/usr/bin/env bash
# Build a standalone Linux AppImage (dist/AuxAudioScrobbler-x86_64.AppImage)
# using PyInstaller + appimagetool.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: .venv not found. Create it first:"
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source ./.venv/bin/activate

# 1. Ensure PyInstaller is available inside .venv
if ! python3 -m PyInstaller --version >/dev/null 2>&1; then
    echo "Installing PyInstaller into .venv..."
    pip install pyinstaller
fi

# 2. Clean previous AppImage build artifacts
BUILD_DIR="$SCRIPT_DIR/build/appimage"
APPDIR="$BUILD_DIR/AppDir"
DIST_DIR="$SCRIPT_DIR/dist"
rm -rf "$BUILD_DIR"
mkdir -p "$APPDIR/usr/bin" "$DIST_DIR"

# 3. Locate libportaudio.so.2 on the host so the AppImage is self-contained
PORTAUDIO_LIB="$(ldconfig -p | awk '/libportaudio\.so\.2/ {print $NF; exit}')"
ADD_BIN_ARGS=()
if [ -n "$PORTAUDIO_LIB" ] && [ -f "$PORTAUDIO_LIB" ]; then
    echo "Bundling PortAudio: $PORTAUDIO_LIB"
    ADD_BIN_ARGS+=(--add-binary "$PORTAUDIO_LIB:.")
else
    echo "WARNING: libportaudio.so.2 not found via ldconfig; AppImage will rely on host libportaudio2."
fi

# 4. Bundle the application with PyInstaller (--onedir for fast AppImage startup)
echo "Running PyInstaller..."
python3 -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --windowed \
    --name aux-audio-scrobbler \
    --distpath "$BUILD_DIR/pyi_dist" \
    --workpath "$BUILD_DIR/pyi_work" \
    --specpath "$BUILD_DIR" \
    --collect-all shazamio \
    --collect-all shazamio_core \
    --collect-all sounddevice \
    --collect-all soundfile \
    --hidden-import PIL._tkinter_finder \
    "${ADD_BIN_ARGS[@]}" \
    gui.py

# 5. Populate AppDir
cp -a "$BUILD_DIR/pyi_dist/aux-audio-scrobbler/." "$APPDIR/usr/bin/"
cp "$SCRIPT_DIR/packaging/aux-audio-scrobbler.desktop" "$APPDIR/aux-audio-scrobbler.desktop"
cp "$SCRIPT_DIR/packaging/icon.svg" "$APPDIR/aux-audio-scrobbler.svg"

# Generate a 256x256 PNG icon as well (some desktop environments prefer PNG over SVG)
python3 - <<'EOF' "$APPDIR/aux-audio-scrobbler.png"
import sys
from PIL import Image, ImageDraw
img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([12, 12, 244, 244], radius=44, fill="#121212", outline="#2C2C2E", width=4)
d.ellipse([64, 64, 192, 192], outline="#1DB954", width=14)
d.ellipse([104, 104, 152, 152], fill="#1DB954")
img.save(sys.argv[1])
EOF

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
export LD_LIBRARY_PATH="$HERE/usr/bin:${LD_LIBRARY_PATH:-}"
exec "$HERE/usr/bin/aux-audio-scrobbler" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# 6. Obtain appimagetool if not already present
APPIMAGETOOL="$(command -v appimagetool || true)"
if [ -z "$APPIMAGETOOL" ]; then
    APPIMAGETOOL="$SCRIPT_DIR/build/appimagetool-x86_64.AppImage"
    if [ ! -x "$APPIMAGETOOL" ]; then
        echo "Downloading appimagetool-x86_64.AppImage..."
        curl -fsSL -o "$APPIMAGETOOL" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        chmod +x "$APPIMAGETOOL"
    fi
fi

# 7. Obtain type2-runtime if not cached, then build the final AppImage
RUNTIME_FILE="$SCRIPT_DIR/build/runtime-x86_64"
if [ ! -f "$RUNTIME_FILE" ]; then
    echo "Downloading AppImage type2-runtime..."
    curl -fsSL -o "$RUNTIME_FILE" \
        "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
fi

# --appimage-extract-and-run avoids requiring FUSE
OUTPUT_APPIMAGE="$DIST_DIR/AuxAudioScrobbler-x86_64.AppImage"
rm -f "$OUTPUT_APPIMAGE"
ARCH=x86_64 "$APPIMAGETOOL" --appimage-extract-and-run --runtime-file "$RUNTIME_FILE" "$APPDIR" "$OUTPUT_APPIMAGE"
chmod +x "$OUTPUT_APPIMAGE"

echo ""
echo "Built AppImage: $OUTPUT_APPIMAGE"
ls -lh "$OUTPUT_APPIMAGE"
