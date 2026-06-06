# Desktop Release Notes

## Recommended Product Shape

For the simplest consumer experience:

1. Ship a desktop app for Mac and Windows.
2. Keep folder indexing local.
3. Use one of these LLM strategies:
   - Hosted backend with your API key, auth, and usage caps.
   - User-provided API key stored locally.
   - Local model mode with Ollama/llama.cpp.

The current scaffold runs the FastAPI backend locally and opens it in a Tauri window.

## macOS

Build on macOS:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
npm install
pyinstaller filewhisper-backend.spec
npm run desktop:build
```

For public distribution, plan for:

- Apple Developer account
- code signing
- notarization
- DMG or app bundle distribution

Unsigned GitHub Actions builds may show `"FileWhisper" is damaged and can't be opened.` For local testing only, remove quarantine after dragging the app to Applications:

```bash
xattr -dr com.apple.quarantine /Applications/FileWhisper.app
```

This is not a substitute for proper public distribution. A consumer-facing macOS release should be signed with a Developer ID certificate and notarized by Apple.

## Windows

Build on Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
npm install
pyinstaller filewhisper-backend.spec
npm run desktop:build
```

For public distribution, plan for:

- code signing certificate
- MSI/NSIS installer
- Windows Defender reputation warmup

## CI

Use separate GitHub Actions jobs:

- `macos-latest` for `.app` / `.dmg`
- `windows-latest` for `.exe` / `.msi`

Each job should install Python, Node, Rust, dependencies, build the backend executable, then run `npm run desktop:build`.
