#!/bin/bash
#
# FileWhisper — one-line installer (macOS)
#
#   curl -fsSL https://raw.githubusercontent.com/ishankanodia/server_rag/main/install.sh | bash
#
# What it does:
#   1. Downloads FileWhisper into ~/.filewhisper/app
#   2. Builds an isolated Python environment (no PyTorch — stays small & fast)
#   3. Pre-downloads the local AI models so the first question is instant
#   4. Drops a double-click "FileWhisper" launcher on your Desktop
#
# After this, the user never needs Terminal again — they just double-click.
#
set -e

REPO="ishankanodia/server_rag"
BRANCH="${FILEWHISPER_BRANCH:-main}"
APP_DIR="$HOME/.filewhisper/app"
VENV="$APP_DIR/.venv"
APP_BUNDLE="$HOME/Desktop/FileWhisper.app"

echo ""
echo "=================================================="
echo "   Installing FileWhisper"
echo "=================================================="
echo ""

# 1. Make sure Python 3 is available (macOS provides it via Command Line Tools).
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed but was not found."
  echo "A macOS install window will open — click \"Install\", let it finish,"
  echo "then run this command again."
  xcode-select --install 2>/dev/null || true
  exit 1
fi

# 2. Get the source. FILEWHISPER_SRC lets you install from a local copy (testing);
#    otherwise the latest version is downloaded from GitHub.
echo "-> Downloading FileWhisper..."
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
if [ -n "$FILEWHISPER_SRC" ]; then
  # Copy local source, excluding bulky/transient dirs.
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

# 5. Build a proper macOS app on the Desktop. It shows as "FileWhisper" with the
#    logo, launches with NO Terminal window, and quits from the Dock.
#    Generated locally, so macOS does not quarantine it -> it just opens.
echo "-> Creating the FileWhisper app..."
rm -f "$HOME/Desktop/FileWhisper.command"   # remove the older-style launcher if present
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"

# 5a. App icon, built from the logo with macOS' own sips + iconutil.
LOGO="$APP_DIR/filewhisper/static/logo.png"
if [ -f "$LOGO" ]; then
  ICONSET="$(mktemp -d)/FileWhisper.iconset"
  mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s"             "$LOGO" --out "$ICONSET/icon_${s}x${s}.png"     >/dev/null 2>&1
    sips -z "$((s*2))" "$((s*2))" "$LOGO" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
  done
  iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/icon.icns" 2>/dev/null || true
fi

# 5b. Info.plist gives the app its name and icon.
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

# 5c. The launcher the app runs. No Terminal window appears; logs go to a file.
cat > "$APP_BUNDLE/Contents/MacOS/FileWhisper" <<EOF
#!/bin/bash
cd "$APP_DIR"
exec "$VENV/bin/python" -m filewhisper.server_launcher >> "$HOME/.filewhisper/filewhisper.log" 2>&1
EOF
chmod +x "$APP_BUNDLE/Contents/MacOS/FileWhisper"

touch "$APP_BUNDLE"   # nudge Finder to pick up the new icon

echo ""
echo "=================================================="
echo "   FileWhisper is installed!"
echo "=================================================="
echo ""
echo "  Double-click  \"FileWhisper\"  on your Desktop to start."
echo "  It opens automatically in your web browser — no Terminal window."
echo "  To stop it: right-click the FileWhisper icon in the Dock -> Quit."
echo ""
