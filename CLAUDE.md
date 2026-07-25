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

- There is **no build step and no test suite**: it's a small FastAPI app served as static HTML + JSON endpoints. Verify changes by running the launcher and exercising the UI / curling endpoints.
- Run the ASGI app directly (no auto-browser, fixed port) with `uvicorn filewhisper.main:app --reload --port 8001`, which is useful for backend iteration. The `server_launcher` adds port-finding, browser-open, PID/port state files, and the single-instance check on top of this.
- The server binds **127.0.0.1 only** by default; `FILEWHISPER_LAN=1` opts into `0.0.0.0` for phone/LAN access. Keep it that way: a LAN bind exposes `/browse` and the user's documents to the whole network.
- Hosted/container mode uses `Procfile` / `Dockerfile` (`uvicorn filewhisper.main:app`). Both set `FILEWHISPER_DISABLE_BROWSE=1`, which 403s `/browse` and `/ingest` (they read the host filesystem). Keep that env var when adding new deployment targets.
- Dev state collides with an installed copy: everything persists under `~/.filewhisper/` (index, `config.json`, pid/port files). When iterating locally on a machine that also runs the installed app, point `RAG_DATA_DIR`, `FILEWHISPER_CONFIG`, and `FILEWHISPER_HOME` at scratch paths so you don't pollute the real index/config. `FILEWHISPER_PORT` pins the launcher's port.
- What the installers (`install.sh` / `install.ps1`) build: a source snapshot in `~/.filewhisper/app` with its own `.venv`, models pre-warmed, plus a Desktop entry point per OS (macOS `.app` bundle, Windows `.lnk` straight to `pythonw.exe` so no console appears, Linux `.desktop`). Re-running them upgrades in place: they first `POST /shutdown` to the running instance, and user data survives because index/config live outside `app/`. Both scripts hunt for Python 3.10-3.13 and install one if missing.

## Architecture (the three modules)

The package is only three files; understanding how they connect is the whole picture.

- **`filewhisper/rag.py`**: all local processing and persistence. Owns module-level global state (`documents`, `doc_sources`, `sources`, `index`) that is loaded from disk on import and re-saved on every ingest. Key flow: `ingest_path` → `extract_text_from_file` → `split_text` (overlapping word windows) → `add_chunks` → fastembed ONNX MiniLM embeddings → FAISS `IndexFlatL2`. `retrieve(query, k)` returns `(chunk_text, source_path)` tuples. Persistence lives in `~/.filewhisper/rag_data/` (override with `RAG_DATA_DIR`): `index.faiss`, `documents.pkl`, `sources.json`. The embedding model and OCR engine are **lazy-loaded singletons** (`_get_model`, `_get_ocr`); first call triggers a download. PDF extraction auto-falls-back to OCR per page when the text layer is empty/gibberish (see `is_gibberish` heuristic). The FAISS index and the parallel `documents`/`doc_sources` lists are **not thread-safe** and FastAPI serves on a thread pool, so every mutation and search goes through the module-level `_lock` (RLock); keep new code that touches them inside it.

- **`filewhisper/main.py`**: FastAPI app, LLM routing, and the answer pipeline. A **LangGraph** `StateGraph` chains `retrieve → answer → followup` (compiled as `graph`, invoked by `POST /ask`). LLM provider abstraction is the bulk of this file: `PROVIDER_DEFAULTS` maps each provider to a `base_url` + `api_key_env` + `api_style` (`openai`/`anthropic`/`gemini`/`huggingface`), and `call_llm` dispatches to the matching `_call_*` function. The default provider is **`free-huggingface`**, which when keyless calls the Pollinations free endpoint (`_call_pollinations`, with POST→GET fallback for Cloudflare blocks) and also falls back to it when a keyed HF request fails. Live config is held in the `llm_config` dict, persisted to `~/.filewhisper/config.json` (override with `FILEWHISPER_CONFIG`, chmod 600 because it holds plaintext keys) via `/llm-config`; `GET /llm-config` returns only `has_api_key`, never the key. `clean_text` post-processes every LLM reply to strip markdown and collapse any lists back into flowing prose; the product intentionally never shows bullet points. Endpoints: `/` (UI), `/health`, `/browse`, `/ingest`, `/sources` (GET + DELETE), `/llm-config` (GET + POST), `/ask`, `/shutdown`.

  `POST /shutdown` is the app's only stop mechanism (the in-app **Quit** button): it deletes the pid/port files and `os._exit(0)`s from a background thread after the response flushes. Both installers also POST it to stop a running instance before upgrading, so keep the route and its path stable.

- **`filewhisper/server_launcher.py`**: desktop launcher concerns only. Finds a free port, opens the browser after a `/health` poll (the health body's `"app": "filewhisper"` field is verified so a stale port file pointing at another app isn't reused), writes PID/port files to `~/.filewhisper/` (override base with `FILEWHISPER_HOME`), and reuses an already-running instance instead of spawning a second server. `_ensure_output_streams` exists because Windows `pythonw.exe` has `stdout`/`stderr` set to `None`, which crashes uvicorn's logger. For the same reason `uvicorn` and the app are imported **inside `main()` after the stream fix**, never at module level, so import failures reach the log file instead of dying silently.

UI is a single static file: `filewhisper/static/index.html` (file browser + chat), served at `/`.

## Conventions & gotchas

- `.env` only seeds the initial LLM config. A `config.json` saved from the UI overrides it at startup (`_load_saved_llm_config` runs at import time and wins for provider/model/base_url). If an env change seems ignored, delete the saved config or point `FILEWHISPER_CONFIG` elsewhere.
- `POST /ask` never returns an HTTP error: every exception is caught and returned as a 200 with `answer: "Error: ..."`. When testing, check the answer text, not the status code.
- **LLM replies must read as plain chatbot prose**, never markdown or lists. This is enforced both in the prompts (`answer_node`) and defensively in `clean_text`. Preserve both layers when touching answer formatting. `clean_text` must never strip non-ASCII characters; answers can be in any language.
- **There is deliberately no CORS middleware.** The UI is same-origin (`window.location.origin`); adding permissive CORS would let any webpage the user visits read `/browse`, query documents, or rewrite `/llm-config` to exfiltrate API keys.
- The supported Python window is **3.10-3.13** (fastembed needs ≥3.10; rapidocr/onnxruntime lack 3.14 support). It's encoded in `pyproject.toml` and both installers' version checks; update all three together when it changes.
- When adding an LLM provider: add an entry to `PROVIDER_DEFAULTS`, add its key env var to the `llm_config["api_keys"]` dict, and (if a new `api_style`) a `_call_*` function wired into `call_llm`.
- Supported file extensions live in **one** place: `SUPPORTED_EXTENSIONS` in `rag.py`, imported by `main.py` for `/browse`. Adding a type also means teaching `extract_text_from_file` to read it and adding an icon to `EXT_ICONS` in the UI.
- Dependencies are duplicated in `requirements.txt` and `pyproject.toml` (`[project] dependencies`); keep them in sync. `pip install -e .` provides the `filewhisper` console command (runs `server_launcher:main`). There is no `setup.py`: `pyproject.toml` is the only packaging metadata, and no `package.json` (nothing in this repo is JS).
- The keyless free path depends on an external service that keeps changing. As of the last check, Pollinations' legacy endpoint answers short prompts but returns **HTTP 402 on anything with real document context**, so `_call_pollinations` fails fast on 402 with a "add an API key" message. If you touch keyless behavior, test with a full-size prompt (a short "say hi" test passes and proves nothing).
- LLM defaults live in **four** places that must agree: `PROVIDER_DEFAULTS` in `main.py`, the JS `PROVIDER_DEFAULTS` in `filewhisper/static/index.html` (which also holds the model dropdown lists), `.env.example`, and the README table. The UI values are what a user actually saves into `config.json`, so a stale `baseUrl` there persists into their config.
- `_call_openai_compatible` branches on the model name: `gpt-5*`/`o*` models reject `max_tokens` and non-default `temperature`, so they get `max_completion_tokens` and no temperature. Adding new reasoning-model families means extending that regex.
- `docs/index.html` is the GitHub Pages marketing site (custom domain via `docs/CNAME`, GoatCounter analytics, Formspree waitlist form). It is one page with two hash-routed views: `#desktop` (the free app) and `#web` (the paid "FileWhisper for Websites" product, which is pre-launch). Waitlist/pricing belong **only** to the web view; the desktop view must never ask visitors to wait for something that already ships. Install one-liners and positioning are embedded here, so sync them with the README. `docs/interest.html` is a redirect stub kept alive for old inbound links.
- Don't commit `.env` or anything under `rag_data/` (contains private document text and local paths).
- Both installers (`install.sh`, `install.ps1`) contain an **opt-out** anonymous install ping gated behind an `ANALYTICS_URL` that is a placeholder (`*example*` ⇒ disabled). It stays disabled until a real webhook URL is set.
