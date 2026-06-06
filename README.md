# FileWhisper

Desktop-ready local RAG for asking questions over files on your computer.

The app indexes local documents, retrieves relevant chunks with FAISS, and answers questions using a configurable LLM provider.

## Features

- Browse and index local folders/files.
- Ask questions over indexed documents.
- Supports `.txt`, `.md`, `.pdf`, and common image formats.
- Extracts PDF text with PyMuPDF.
- Extracts image text with Tesseract OCR.
- Uses local FAISS storage for embeddings.
- Supports Groq, OpenAI, Claude, Gemini, and custom OpenAI-compatible APIs.
- Includes a Tauri desktop shell for Mac and Windows packaging.

## Distribution Options

### For Normal Users

Ship a desktop app.

Users should download a finished installer:

- macOS: `.dmg`
- Windows: `.exe`

They should not need Git, Python, Node, Rust, or Terminal.

Expected user flow:

1. Download and open the app.
2. Choose an LLM provider/model.
3. Paste an API key once.
4. Browse local files or folders.
5. Index selected files.
6. Ask questions.

API keys are saved on the user's own computer in local app config.

### For Developers

Developers can clone the repo and run the app locally from Terminal.

```bash
git clone https://github.com/ishankanodia/server_rag.git
cd server_rag
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python server_launcher.py
```

Open:

```text
http://localhost:8001
```

Developers can either edit `.env` or paste an API key in `LLM Settings`.

For image OCR on macOS:

```bash
brew install tesseract
```

## Desktop Development

Install Node dependencies:

```bash
npm install
```

Install Rust:

```text
https://rustup.rs
```

Run the desktop app in development:

```bash
npm run desktop:dev
```

This starts the Python backend locally and opens the Tauri desktop window.

## Build Installers

Builds must be produced on the target OS:

- Build macOS artifacts on macOS.
- Build Windows artifacts on Windows.

Install build dependencies:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
npm install
```

Build the Python backend executable:

```bash
pyinstaller filewhisper-backend.spec
```

Build the desktop app:

```bash
npm run desktop:build
```

Artifacts are generated under:

```text
src-tauri/target/release/bundle/
```

## Build With GitHub Actions

The repo includes:

```text
.github/workflows/desktop-build.yml
```

To create downloadable artifacts:

1. Open the repo on GitHub.
2. Go to `Actions`.
3. Select `Desktop Builds`.
4. Click `Run workflow`.
5. Download the artifacts when the workflow finishes.

Expected artifact names:

- `filewhisper-macos`
- `filewhisper-windows`

For public distribution, create a GitHub Release and upload the generated `.dmg` and `.exe`.

## macOS Download Warning

GitHub Actions builds are unsigned by default. On macOS, an unsigned downloaded app may show:

```text
"FileWhisper" is damaged and can't be opened.
```

For local testing, remove the quarantine flag after dragging the app to Applications:

```bash
xattr -dr com.apple.quarantine /Applications/FileWhisper.app
```

Then open the app again.

For real public distribution, use an Apple Developer account to code sign and notarize the app. Without notarization, many Mac users will see Gatekeeper warnings.

## LLM Configuration

The app supports these providers:

- Groq
- OpenAI
- Anthropic Claude
- Google Gemini
- Custom OpenAI-compatible APIs

Environment examples:

```bash
LLM_PROVIDER=groq
LLM_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=your_key
```

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-5-mini
OPENAI_API_KEY=your_key
```

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=your_key
```

```bash
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
GEMINI_API_KEY=your_key
```

```bash
LLM_PROVIDER=custom
LLM_MODEL=your_model
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your_key
```

## Project Structure

```text
main.py                    FastAPI app and LLM routing
rag.py                     Ingestion, chunking, embeddings, FAISS search
server_launcher.py         Local backend launcher
static/index.html          Main UI
src-tauri/                 Tauri desktop shell
.github/workflows/         GitHub Actions desktop builds
filewhisper-backend.spec    PyInstaller backend build config
```

## Security Notes

- Do not commit `.env`.
- Do not commit `rag_data/`; it can contain private document text and local file paths.
- A hosted web app cannot browse a user's local folders. Hosted mode should use file uploads instead.
- Do not expose one shared API key publicly without auth, rate limits, and abuse controls.
- Revoke any API key that was ever committed to git history.

## Hosted Web Version

This project is currently optimized for local desktop use.

If you want a hosted web version, replace local folder browsing with file upload:

1. User opens website.
2. User uploads documents.
3. Server indexes uploaded files.
4. User asks questions.

Do not expose `/browse` on a public hosted server.
