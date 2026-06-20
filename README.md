# FileWhisper

Desktop-ready local RAG for asking questions over files on your computer.

The app indexes local documents, retrieves relevant chunks with FAISS, and answers questions using a configurable LLM provider.

## Features

- 100% local: your files never leave your computer.
- Works with no API key out of the box (a free built-in assistant).
- Browse and index local folders/files, then ask questions over them.
- Supports `.txt`, `.md`, `.pdf`, and common image formats.
- Extracts PDF text with PyMuPDF, with automatic OCR fallback for scanned pages.
- Reads images and scanned PDFs with a lightweight ONNX OCR engine (no PyTorch).
- Local FAISS vector search with ONNX MiniLM embeddings (fast, ~400 MB install, no PyTorch).
- Optional providers: Groq, OpenAI, Claude, Gemini, or any custom OpenAI-compatible API.

## Install (macOS)

For non-technical users — no Git, Node, or Rust required. Open the **Terminal** app and paste this single line:

```bash
curl -fsSL https://raw.githubusercontent.com/ishankanodia/server_rag/main/install.sh | bash
```

This downloads FileWhisper, builds a small isolated environment (~400 MB, no PyTorch), pre-loads the local AI models, and drops a **FileWhisper** icon on your Desktop. After that, just **double-click FileWhisper** — it opens in your web browser. You never need Terminal again.

Everything runs locally. The built-in free assistant means you don't even need an API key; paste one in **LLM Settings** only if you prefer a specific provider.

> The Desktop launcher is generated on your own machine, so macOS does not flag it as an "unidentified developer" — it just opens.

## Install (Windows 10/11)

Open **PowerShell** and paste this one line:

```powershell
irm https://raw.githubusercontent.com/ishankanodia/server_rag/main/install.ps1 | iex
```

It installs Python if needed (via winget), sets up the same small isolated environment, and puts **FileWhisper** and **Stop FileWhisper** shortcuts (with the logo) on your Desktop. Double-click **FileWhisper** to start — it opens in your browser with **no console window** — and **Stop FileWhisper** to stop it.

### For Developers

Developers can clone the repo and run the app locally from Terminal.

```bash
git clone https://github.com/ishankanodia/server_rag.git
cd server_rag
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m filewhisper.server_launcher
```

Open:

```text
http://localhost:8001
```

Developers can either edit `.env` or paste an API key in `LLM Settings`.

OCR for images and scanned PDFs is built in (ONNX, no system Tesseract required).

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
install.sh                      One-line macOS installer (recommended)
filewhisper/main.py             FastAPI app and LLM routing
filewhisper/rag.py              Ingestion, chunking, ONNX embeddings, FAISS search
filewhisper/server_launcher.py  Local backend launcher (opens the browser)
filewhisper/static/index.html   Main UI
Dockerfile                      Optional container image for self-hosting
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
