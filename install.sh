#!/bin/bash
#
# FileWhisper - one-line installer (macOS & Linux)
#
#   curl -fsSL https://raw.githubusercontent.com/ishankanodia/FileWhisper/main/install.sh | bash
#
# What it does:
#   1. Downloads FileWhisper into ~/.filewhisper/app
#   2. Builds an isolated Python environment (no PyTorch - stays small & fast)
#   3. Pre-downloads the local AI models so the first question is instant
#   4. Drops a double-click "FileWhisper" launcher on your Desktop
#
# After this, the user never needs the terminal again - they just double-click.
#
set -e

REPO="ishankanodia/FileWhisper"
BRANCH="${FILEWHISPER_BRANCH:-main}"
APP_DIR="$HOME/.filewhisper/app"
VENV="$APP_DIR/.venv"
OS="$(uname -s)"

echo ""
echo "=================================================="
echo "   Installing FileWhisper"
echo "=================================================="
echo ""

# 1. Make sure Python 3 is available.
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed but was not found."
  if [ "$OS" = "Darwin" ]; then
    echo "A macOS install window will open - click \"Install\", let it finish,"
    echo "then run this command again."
    xcode-select --install 2>/dev/null || true
  else
    echo "Please install it with your package manager, then run this again, e.g.:"
    echo "  Debian/Ubuntu:  sudo apt install python3 python3-venv python3-pip"
    echo "  Fedora:         sudo dnf install python3 python3-pip"
  fi
  exit 1
fi

# 2. Get the source. FILEWHISPER_SRC lets you install from a local copy (testing);
#    otherwise the latest version is downloaded from GitHub.
echo "-> Downloading FileWhisper..."
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
if [ -n "$FILEWHISPER_SRC" ]; then
  ( cd "$FILEWHISPER_SRC" && \
    find . -type d \( -name .git -o -name .venv -o -name node_modules -o -name dist -o -name target \) -prune -o -type f -print \
    | sed 's|^\./||' | while read -r f; do
        mkdir -p "$APP_DIR/$(dirname "$f")"
        cp "$FILEWHISPER_SRC/$f" "$APP_DIR/$f"
      done )
else
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" \
    | tar xz -C "$APP_DIR" --strip-components=1
fi

# 3. Build an isolated environment and install dependencies (no PyTorch).
echo "-> Setting up (downloads ~400 MB the first time, please wait)..."
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"

# 4. Pre-download the local AI models so the first question doesn't stall.
echo "-> Preparing the local AI models..."
"$VENV/bin/python" - <<'PY' || true
try:
    from fastembed import TextEmbedding
    list(TextEmbedding("sentence-transformers/all-MiniLM-L6-v2").embed(["warmup"]))
    print("   embeddings ready")
except Exception as e:
    print("   (embedding warmup skipped:", e, ")")
try:
    from rapidocr_onnxruntime import RapidOCR
    RapidOCR()
    print("   OCR ready")
except Exception as e:
    print("   (OCR warmup skipped:", e, ")")
PY

LOGO_PNG="$APP_DIR/filewhisper/static/logo.png"

if [ "$OS" = "Darwin" ]; then
  # 5a. macOS: build a proper .app on the Desktop (no Terminal window, logo icon).
  #     Generated locally, so macOS does not quarantine it -> it just opens.
  echo "-> Creating the FileWhisper app..."
  APP_BUNDLE="$HOME/Desktop/FileWhisper.app"
  rm -f "$HOME/Desktop/FileWhisper.command"
  rm -rf "$APP_BUNDLE"
  mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

  if [ -f "$LOGO_PNG" ]; then
    ICONSET="$(mktemp -d)/FileWhisper.iconset"
    mkdir -p "$ICONSET"
    for s in 16 32 128 256 512; do
      sips -z "$s" "$s"             "$LOGO_PNG" --out "$ICONSET/icon_${s}x${s}.png"     >/dev/null 2>&1
      sips -z "$((s*2))" "$((s*2))" "$LOGO_PNG" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/icon.icns" 2>/dev/null || true
  fi

  cat > "$APP_BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>FileWhisper</string>
  <key>CFBundleDisplayName</key><string>FileWhisper</string>
  <key>CFBundleIdentifier</key><string>com.ishankanodia.filewhisper</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>FileWhisper</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

  cat > "$APP_BUNDLE/Contents/MacOS/FileWhisper" <<EOF
#!/bin/bash
cd "$APP_DIR"
exec "$VENV/bin/python" -m filewhisper.server_launcher >> "$HOME/.filewhisper/filewhisper.log" 2>&1
EOF
  chmod +x "$APP_BUNDLE/Contents/MacOS/FileWhisper"
  touch "$APP_BUNDLE"
  # Register with LaunchServices so the custom icon shows right away.
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_BUNDLE" 2>/dev/null || true

  echo ""
  echo "=================================================="
  echo "   FileWhisper is installed!"
  echo "=================================================="
  echo ""
  echo "  Double-click  \"FileWhisper\"  on your Desktop to start."
  echo "  It opens automatically in your web browser - no Terminal window."
  echo "  To stop it: right-click the FileWhisper icon in the Dock -> Quit."
  echo ""

else
  # 5b. Linux: create a .desktop launcher (no terminal) + an app-menu entry.
  echo "-> Creating the FileWhisper launcher..."
  START_SH="$APP_DIR/filewhisper-start.sh"

  cat > "$START_SH" <<EOF
#!/bin/bash
cd "$APP_DIR"
exec "$VENV/bin/python" -m filewhisper.server_launcher >> "$HOME/.filewhisper/filewhisper.log" 2>&1
EOF
  chmod +x "$START_SH"

  # Remove a stale "Stop" launcher from older installs - quitting is now done
  # from inside the app, so there is only one FileWhisper launcher.
  rm -f "$HOME/Desktop/Stop FileWhisper.desktop"

  mkdir -p "$HOME/.local/share/applications" "$HOME/Desktop"
  write_desktop() {  # name, comment, exec, outfile
    cat > "$4" <<EOF
[Desktop Entry]
Type=Application
Name=$1
Comment=$2
Exec=$3
Path=$APP_DIR
Icon=$LOGO_PNG
Terminal=false
Categories=Utility;Office;
EOF
    chmod +x "$4" 2>/dev/null || true
    gio set "$4" metadata::trusted true 2>/dev/null || true
  }
  write_desktop "FileWhisper" "Chat with your local files" "$START_SH" "$HOME/.local/share/applications/filewhisper.desktop"
  write_desktop "FileWhisper" "Chat with your local files" "$START_SH" "$HOME/Desktop/FileWhisper.desktop"

  echo ""
  echo "=================================================="
  echo "   FileWhisper is installed!"
  echo "=================================================="
  echo ""
  echo "  Double-click  \"FileWhisper\"  on your Desktop to start."
  echo "  (On the first launch you may need to right-click -> Allow Launching.)"
  echo "  It opens in your web browser - no terminal window."
  echo "  To stop it, click  \"Quit FileWhisper\"  inside the app."
  echo ""
fi
