# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FileWhisper is a local-first RAG document assistant. Documents are parsed, OCR'd, embedded, and searched entirely on the user's machine; only the final question + matched snippets are sent to an LLM (which can be a free keyless provider). The whole product ships as a one-line installer that builds an isolated venv and drops a double-click launcher on the Desktop.

## Develop / run

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python 3.10-3.13 (3.14+ unsupported by the AI libs)
pip install -r requirements.txt
cp .env.example .env
python -m filewhisper.server_launcher   # picks a free port 8001-8100, opens browser
```

- There is **no build step and no test suite** — it's a small FastAPI app served as static HTML + JSON endpoints. Verify changes by running the launcher and exercising the UI / curling endpoints.
- Run the ASGI app directly (no auto-browser, fixed port) with `uvicorn filewhisper.main:app --reload --port 8001` — useful for backend iteration. The `server_launcher` adds port-finding, browser-open, PID/port state files, and the single-instance check on top of this.
- The server binds **127.0.0.1 only** by default; `FILEWHISPER_LAN=1` opts into `0.0.0.0` for phone/LAN access. Keep it that way — a LAN bind exposes `/browse` and the user's documents to the whole network.
- Hosted/container mode uses `Procfile` / `Dockerfile` (`uvicorn filewhisper.main:app`). Both set `FILEWHISPER_DISABLE_BROWSE=1`, which 403s `/browse` and `/ingest` (they read the host filesystem). Keep that env var when adding new deployment targets.

## Architecture (the three modules)

The package is only three files; understanding how they connect is the whole picture.

- **`filewhisper/rag.py`** — all local processing and persistence. Owns module-level global state (`documents`, `doc_sources`, `sources`, `index`) that is loaded from disk on import and re-saved on every ingest. Key flow: `ingest_path` → `extract_text_from_file` → `split_text` (overlapping word windows) → `add_chunks` → fastembed ONNX MiniLM embeddings → FAISS `IndexFlatL2`. `retrieve(query, k)` returns `(chunk_text, source_path)` tuples. Persistence lives in `~/.filewhisper/rag_data/` (override with `RAG_DATA_DIR`): `index.faiss`, `documents.pkl`, `sources.json`. The embedding model and OCR engine are **lazy-loaded singletons** (`_get_model`, `_get_ocr`) — first call triggers a download. PDF extraction auto-falls-back to OCR per page when the text layer is empty/gibberish (see `is_gibberish` heuristic).

- **`filewhisper/main.py`** — FastAPI app, LLM routing, and the answer pipeline. A **LangGraph** `StateGraph` chains `retrieve → answer → followup` (compiled as `graph`, invoked by `POST /ask`). LLM provider abstraction is the bulk of this file: `PROVIDER_DEFAULTS` maps each provider to a `base_url` + `api_key_env` + `api_style` (`openai`/`anthropic`/`gemini`/`huggingface`), and `call_llm` dispatches to the matching `_call_*` function. The default provider is **`free-huggingface`**, which when keyless calls the Pollinations free endpoint (`_call_pollinations`, with POST→GET fallback for Cloudflare blocks). Live config is held in the `llm_config` dict, persisted to `~/.filewhisper/config.json` (override with `FILEWHISPER_CONFIG`) via `/llm-config`. `clean_text` post-processes every LLM reply to strip markdown and collapse any lists back into flowing prose — the product intentionally never shows bullet points.

- **`filewhisper/server_launcher.py`** — desktop launcher concerns only. Finds a free port, opens the browser after a `/health` poll (the health body's `"app": "filewhisper"` field is verified so a stale port file pointing at another app isn't reused), writes PID/port files to `~/.filewhisper/` (override base with `FILEWHISPER_HOME`), and reuses an already-running instance instead of spawning a second server. `_ensure_output_streams` exists because Windows `pythonw.exe` has `stdout`/`stderr` set to `None`, which crashes uvicorn's logger — for the same reason `uvicorn` and the app are imported **inside `main()` after the stream fix**, never at module level, so import failures reach the log file instead of dying silently.

UI is a single static file: `filewhisper/static/index.html` (file browser + chat), served at `/`.

## Conventions & gotchas

- **LLM replies must read as plain chatbot prose** — never markdown or lists. This is enforced both in the prompts (`answer_node`) and defensively in `clean_text`. Preserve both layers when touching answer formatting. `clean_text` must never strip non-ASCII characters — answers can be in any language.
- **There is deliberately no CORS middleware.** The UI is same-origin (`window.location.origin`); adding permissive CORS would let any webpage the user visits read `/browse`, query documents, or rewrite `/llm-config` to exfiltrate API keys.
- The supported Python window is **3.10–3.13** (fastembed needs ≥3.10; rapidocr/onnxruntime lack 3.14 support). It's encoded in `pyproject.toml` and both installers' version checks — update all three together when it changes.
- When adding an LLM provider: add an entry to `PROVIDER_DEFAULTS`, add its key env var to the `llm_config["api_keys"]` dict, and (if a new `api_style`) a `_call_*` function wired into `call_llm`.
- Supported file extensions are duplicated as a `SUPPORTED` set in both `rag.py` (`ingest_path`) and `main.py` (`/browse`) — keep them in sync.
- Dependencies are duplicated in `requirements.txt` and `pyproject.toml` (`[project] dependencies`) — keep them in sync. `pip install -e .` also works and provides the `filewhisper` console command (runs `server_launcher:main`).
- `docs/index.html` is the GitHub Pages marketing site and embeds the install one-liners and product messaging — changes to install commands or positioning need syncing there as well as in the README.
- The default LLM model IDs (e.g. `claude-sonnet-4-6`, `gpt-5-mini`, `gemini-2.5-flash`) live in `PROVIDER_DEFAULTS`, `.env.example`, and the README table — update all three together.
- Don't commit `.env` or anything under `rag_data/` (contains private document text and local paths).
- Both installers (`install.sh`, `install.ps1`) contain an **opt-out** anonymous install ping gated behind an `ANALYTICS_URL` that is a placeholder (`*example*` ⇒ disabled). It stays disabled until a real webhook URL is set.
