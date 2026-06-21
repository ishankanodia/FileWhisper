from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .rag import ingest_path, retrieve, get_indexed_sources, clear_index

import json
import re
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypedDict, Optional
from dotenv import load_dotenv
from langgraph.graph import StateGraph

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# =========================
# Models
# =========================
class IngestRequest(BaseModel):
    path: str  # file or folder path


class Query(BaseModel):
    question: str


class LLMConfigRequest(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


# =========================
# Utils
# =========================
PROVIDER_DEFAULTS = {
    "free-huggingface": {
        "model": "mistralai/Mistral-7B-Instruct-v0.3",
        "base_url": "https://router.huggingface.co/hf-inference/models",
        "api_key_env": "HF_API_KEY",
        "api_style": "huggingface",
    },
    "groq": {
        "model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "api_style": "openai",
    },
    "openai": {
        "model": "gpt-5-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "api_style": "openai",
    },
    "anthropic": {
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_style": "anthropic",
    },
    "gemini": {
        "model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GEMINI_API_KEY",
        "api_style": "gemini",
    },
    "custom": {
        "model": "llama-3.1-8b-instant",
        "base_url": "",
        "api_key_env": "LLM_API_KEY",
        "api_style": "openai",
    },
}


llm_config = {
    "provider": os.getenv("LLM_PROVIDER", "free-huggingface").lower(),
    "model": os.getenv("LLM_MODEL", ""),
    "base_url": os.getenv("LLM_BASE_URL", ""),
    "api_keys": {
        "free-huggingface": os.getenv("HF_API_KEY", ""),
        "groq": os.getenv("GROQ_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "gemini": os.getenv("GEMINI_API_KEY", ""),
        "custom": os.getenv("LLM_API_KEY", ""),
    },
}


def _config_path() -> Path:
    configured = os.getenv("FILEWHISPER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".filewhisper" / "config.json"


def _load_saved_llm_config():
    path = _config_path()
    if not path.exists():
        return
    try:
        saved = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return

    provider = _normalize_provider(saved.get("provider", llm_config["provider"]))
    llm_config["provider"] = provider
    llm_config["model"] = saved.get("model") or llm_config["model"]
    llm_config["base_url"] = saved.get("base_url") or llm_config["base_url"]
    for name, key in saved.get("api_keys", {}).items():
        if name in llm_config["api_keys"] and key:
            llm_config["api_keys"][name] = key


def _save_llm_config():
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": llm_config["provider"],
        "model": llm_config["model"],
        "base_url": llm_config["base_url"],
        "api_keys": {name: key for name, key in llm_config["api_keys"].items() if key},
    }
    path.write_text(json.dumps(payload, indent=2))


def _normalize_provider(provider: str) -> str:
    provider = (provider or "free-huggingface").strip().lower()
    return provider if provider in PROVIDER_DEFAULTS else "custom"


_load_saved_llm_config()


def _effective_llm_config() -> dict:
    provider = _normalize_provider(llm_config.get("provider", "free-huggingface"))
    defaults = PROVIDER_DEFAULTS[provider]
    api_key = llm_config["api_keys"].get(provider) or os.getenv(defaults["api_key_env"], "")

    return {
        "provider": provider,
        "model": llm_config.get("model") or defaults["model"],
        "api_key": api_key,
        "base_url": (llm_config.get("base_url") or defaults["base_url"]).rstrip("/"),
        "api_style": defaults["api_style"],
    }


def public_llm_config() -> dict:
    cfg = _effective_llm_config()
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "has_api_key": bool(cfg["api_key"]),
    }


def clean_text(text: str) -> str:
    # Strip Markdown link syntax [text](url) -> text, but keep plain [brackets]
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Remove Markdown bold markers some models emit despite instructions
    text = text.replace('**', '')
    # Normalize "smart"/typographic punctuation to plain ASCII. Done BEFORE the
    # ascii-strip below so apostrophes, quotes and dashes survive and words are
    # never glued together (e.g. "IIM<NBSP>Indore<U+2019>s" -> "IIMIndores").
    _PUNCT = {
        '‘': "'", '’': "'", '‚': "'", '‛': "'",   # single quotes
        '“': '"', '”': '"', '„': '"', '‟': '"',   # double quotes
        '–': '-', '—': '-', '‒': '-', '―': '-',   # dashes
        '…': '...',                                              # ellipsis
        ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ',   # non-breaking/thin spaces
        '​': '', '‌': '', '‍': '', '﻿': '',       # zero-width chars
        '´': "'", '`': "'",                                  # stray accents used as quotes
    }
    for _k, _v in _PUNCT.items():
        text = text.replace(_k, _v)
    text = text.encode("ascii", "ignore").decode()

    # Safety net: if the model ignores the instruction and returns a bullet or
    # numbered list, merge each run of list items back into a flowing sentence
    # so the reply always reads like a chatbot, never as a list.
    lines = text.split('\n')
    out, run = [], []

    def _flush_run():
        if not run:
            return
        frags = []
        for item in run:
            item = item.strip().rstrip(';,')
            if item and item[-1] not in '.!?:':
                item += '.'
            if item:
                frags.append(item)
        if frags:
            out.append(' '.join(frags))
        run.clear()

    for line in lines:
        m = re.match(r'^\s*(?:[-*•]|\d+[.)])\s+(.*)$', line)
        if m:
            run.append(m.group(1))
        else:
            _flush_run()
            out.append(line)
    _flush_run()
    text = '\n'.join(out)

    # Collapse runs of spaces/tabs but keep paragraph line breaks intact
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'[ \t]*\n[ \t]*', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"LLM request failed ({e.code}): {detail[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM request failed: {e.reason}")


def _call_openai_compatible(cfg: dict, prompt: str, max_tokens: int) -> str:
    data = _post_json(
        f"{cfg['base_url']}/chat/completions",
        {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": "Answer ONLY using provided context. Be structured and clear."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.5,
            "max_tokens": max_tokens,
        },
        {"Authorization": f"Bearer {cfg['api_key']}"},
    )
    return data["choices"][0]["message"]["content"].strip()


def _call_anthropic(cfg: dict, prompt: str, max_tokens: int) -> str:
    data = _post_json(
        f"{cfg['base_url']}/messages",
        {
            "model": cfg["model"],
            "system": "Answer ONLY using provided context. Be structured and clear.",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.5,
        },
        {
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
        },
    )
    return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text").strip()


def _call_gemini(cfg: dict, prompt: str, max_tokens: int) -> str:
    model = cfg["model"].removeprefix("models/")
    data = _post_json(
        f"{cfg['base_url']}/models/{model}:generateContent?key={cfg['api_key']}",
        {
            "systemInstruction": {
                "parts": [{"text": "Answer ONLY using provided context. Be structured and clear."}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.5,
                "maxOutputTokens": max_tokens,
            },
        },
        {},
    )
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("LLM request returned no candidates.")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts).strip()


def _call_free_huggingface(cfg: dict, prompt: str, max_tokens: int) -> str:
    api_key = cfg.get("api_key")
    
    if api_key:
        # User has provided an API key, use Hugging Face Serverless Inference
        model = cfg["model"] or "mistralai/Mistral-7B-Instruct-v0.3"
        api_url = f"https://router.huggingface.co/hf-inference/models/{model}"
        
        system_instr = "Answer ONLY using provided context. Be structured and clear."
        full_prompt = f"<s>[INST] {system_instr}\n\n{prompt} [/INST]"
        
        data = {
            "inputs": full_prompt,
            "parameters": {
                "max_new_tokens": max_tokens,
                "temperature": 0.5,
                "return_full_text": False
            }
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        req = urllib.request.Request(
            api_url,
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                if isinstance(res_data, list) and len(res_data) > 0:
                    return res_data[0].get("generated_text", "").strip()
                elif isinstance(res_data, dict):
                    return res_data.get("generated_text", "").strip()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Hugging Face API error ({e.code}): {detail[:300]}")
        except Exception as e:
            raise RuntimeError(f"Hugging Face API error: {e}")
            
    else:
        # Keyless mode: use Pollinations AI as a free fallback.
        # It can be slow on a cold start, so use a generous timeout and retry
        # once on a transient timeout/connection error before giving up.
        api_url = "https://text.pollinations.ai/"
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "model": "openai",
            "jsonMode": False
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        last_err = None
        for _attempt in range(2):
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    return response.read().decode("utf-8").strip()
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise RuntimeError(
                        "Free Assistant rate limit reached. Please try again in a few seconds, "
                        "or add your own API key in LLM Settings for higher limits."
                    )
                detail = e.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"Free Assistant API error ({e.code}): {detail[:300]}")
            except Exception as e:
                last_err = e  # transient (timeout/connection) — retry once

        raise RuntimeError(
            f"Free Assistant API error: {last_err}. Please check your internet connection "
            "or add an API key in LLM Settings."
        )


def call_llm(prompt: str, max_tokens=400):
    cfg = _effective_llm_config()
    
    if cfg["provider"] == "free-huggingface" or cfg["api_style"] == "huggingface":
        return _call_free_huggingface(cfg, prompt, max_tokens)

    if not cfg["api_key"]:
        raise RuntimeError(
            "No LLM API key configured. Set the provider key in .env or deployment secrets."
        )
    if not cfg["base_url"]:
        raise RuntimeError("No LLM base URL configured for the selected provider.")

    if cfg["api_style"] == "anthropic":
        return _call_anthropic(cfg, prompt, max_tokens)
    if cfg["api_style"] == "gemini":
        return _call_gemini(cfg, prompt, max_tokens)
    return _call_openai_compatible(cfg, prompt, max_tokens)


# =========================
# LangGraph
# =========================
class GraphState(TypedDict, total=False):
    question: str
    context: str
    answer: str
    follow_up: str
    sources: list


def retrieve_node(state: GraphState):
    results = retrieve(state["question"])
    if not results:
        return {
            "context": "",
            "answer": "No relevant information found in indexed sources.",
            "follow_up": "",
            "sources": []
        }
    chunks = [r[0] for r in results]
    # unique sources, order preserved
    seen = set()
    src_files = []
    for r in results:
        if r[1] not in seen:
            seen.add(r[1])
            src_files.append(r[1])
    context = "\n\n---\n\n".join(chunks)
    return {"context": context, "sources": src_files}


def answer_node(state: GraphState):
    if not state.get("context"):
        return {}

    prompt = f"""
You are a helpful, friendly assistant answering questions about the user's own documents, using ONLY the context below.

Write your answer as a natural, flowing reply in plain sentences and short paragraphs, exactly like a chatbot talking to a person. Never use bullet points, dashes, numbered lists, or markdown. Even when stating several facts, put them in sentences.

For example, instead of writing:
- Flight: 6E333
- Date: 11 Feb
write it as: "Your flight 6E333 departs on 11 Feb..."

Include the specific details that matter (names, dates, times, places, numbers, references) inside your sentences, and prefer those concrete details over generic boilerplate. If the answer is not in the context, say so plainly instead of guessing.

Context:
{state['context']}

Question:
{state['question']}

Answer:
"""
    answer = call_llm(prompt)
    answer = clean_text(answer)
    return {"answer": answer}


def followup_node(state: GraphState):
    if not state.get("answer"):
        return {}

    prompt = f"""
You are a curious, helpful assistant.

Based on the answer below, generate ONE smart leading question that invites the user to explore further.

Rules:
- Must be specific to actual topics, methods, or concepts in the answer
- Start with "Would you like to know..." or "Want to explore..." or "Curious about..."
- Keep it under 20 words
- Do NOT ask generic questions like "Would you like to know more?"

Answer:
{state['answer']}

Leading question:
"""
    follow = call_llm(prompt, max_tokens=60)
    follow = clean_text(follow)
    if len(follow.split()) < 5:
        follow = "Would you like to explore any of the concepts mentioned above?"
    return {"follow_up": follow}


builder = StateGraph(GraphState)
builder.add_node("retrieve", retrieve_node)
builder.add_node("answer", answer_node)
builder.add_node("followup", followup_node)
builder.set_entry_point("retrieve")
builder.add_edge("retrieve", "answer")
builder.add_edge("answer", "followup")
graph = builder.compile()


# =========================
# API Endpoints
# =========================

@app.get("/")
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/shutdown")
def shutdown():
    """Stop the FileWhisper server. Used by the in-app Quit button so users
    have one way to stop it on every OS (no separate "Stop" desktop app)."""
    base = Path(os.getenv("FILEWHISPER_HOME") or (Path.home() / ".filewhisper"))
    for name in ("filewhisper.pid", "filewhisper.port"):
        try:
            (base / name).unlink()
        except OSError:
            pass

    def _stop():
        time.sleep(0.3)  # let this response flush to the browser first
        os._exit(0)

    threading.Thread(target=_stop, daemon=True).start()
    return {"status": "ok"}


@app.get("/browse")
def browse(path: str = ""):
    """Return contents of a directory for the folder picker UI."""
    if not path:
        path = str(Path.home())
    path = os.path.abspath(path)
    if not os.path.exists(path) or not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Invalid directory")

    SUPPORTED = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

    entries = []
    try:
        for name in sorted(os.listdir(path)):
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                entries.append({"name": name, "path": full, "type": "dir"})
            elif os.path.splitext(name)[1].lower() in SUPPORTED:
                entries.append({"name": name, "path": full, "type": "file",
                                 "ext": os.path.splitext(name)[1].lower()})
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    # breadcrumb parts
    parts = []
    p = path
    while True:
        parent = os.path.dirname(p)
        parts.insert(0, {"name": os.path.basename(p) or p, "path": p})
        if parent == p:
            break
        p = parent

    return {"path": path, "parent": os.path.dirname(path), "entries": entries, "breadcrumb": parts}


@app.post("/ingest")
def ingest(req: IngestRequest):
    path = req.path.strip()
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"Path does not exist: {path}")
    result = ingest_path(path)
    return result


@app.get("/sources")
def sources():
    return {"sources": get_indexed_sources()}


@app.delete("/sources")
def clear():
    clear_index()
    return {"status": "cleared"}


@app.get("/llm-config")
def get_llm_config():
    return public_llm_config()


@app.post("/llm-config")
def set_llm_config(req: LLMConfigRequest):
    provider = _normalize_provider(req.provider)
    if not req.model.strip():
        raise HTTPException(status_code=400, detail="Model is required")
    if provider == "custom" and not (req.base_url or "").strip():
        raise HTTPException(status_code=400, detail="Base URL is required for custom providers")

    llm_config["provider"] = provider
    llm_config["model"] = req.model.strip()
    llm_config["base_url"] = (req.base_url or "").strip()
    if req.api_key is not None and req.api_key.strip():
        llm_config["api_keys"][provider] = req.api_key.strip()
    _save_llm_config()
    return public_llm_config()


@app.post("/ask")
def ask(q: Query):
    try:
        result = graph.invoke({"question": q.question})
        answer = result.get("answer", "")
        follow = result.get("follow_up", "")
        src_files = result.get("sources", [])
        # Return just filenames, not full paths
        filenames = [os.path.basename(s) for s in src_files]
        return {"answer": answer, "follow_up": follow, "sources": filenames}
    except Exception as e:
        return {"answer": f"Error: {str(e)}", "follow_up": "", "sources": []}
