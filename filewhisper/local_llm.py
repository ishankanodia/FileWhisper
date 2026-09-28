"""Fully local answer generation.

Three tiers, tried in this order by ``main.call_llm``:

1. **Built-in ONNX engine** (this module's ``generate``): onnxruntime-genai runs a
   small int4 instruct model on the CPU, from ``~/.filewhisper/models``. Nothing
   leaves the machine. This is what the default ``local`` provider uses.
2. **An external local server** (``detect_external_runtime``): Ollama, LM Studio,
   llama.cpp or Jan already running on a loopback port. Also fully local, and the
   way to use a bigger model than we would download ourselves.
3. **The offline reader** (``extractive_answer``): no model at all, just sentence
   ranking over the retrieved chunks. Pure Python, always available, so a question
   about private documents can always be answered without a network call.

The onnxruntime-genai wheel does not exist for every platform (no macOS x86_64, no
Python 3.10), so the import is lazy and every entry point degrades instead of
raising at import time.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# =========================
# Model catalog
# =========================
# Every entry must be an onnxruntime-genai build (a directory holding
# genai_config.json). `subdir` is the path inside the HF repo to fetch; "" means
# the repo root. `size_mb` is the on-disk size of that subdir and is what the UI
# shows before download, so keep it roughly honest.
#
# `revision` pins an exact commit. Two of these are community repos rather than
# first-party ones, and this code downloads and then executes what they serve, so
# a moving `main` would let a repo change under every future user. Bump a pin
# deliberately, after checking what changed.
MODEL_CATALOG = {
    "qwen3-1.7b": {
        "label": "Qwen3 1.7B (balanced)",
        "repo": "hb-dev/Qwen3-1.7B-ONNX-GenAI",
        "revision": "f9a79b179d34383b3e6afc95ae4882767067ba31",
        "subdir": "",
        "size_mb": 1360,
        "license": "Apache-2.0",
        "description": "Good accuracy on documents, runs on any modern laptop. Recommended.",
    },
    "qwen3-0.6b": {
        "label": "Qwen3 0.6B (smallest)",
        "repo": "xiaoyao9184/Qwen3-0.6B-onnx-genai",
        "revision": "dfaa6540d16eb3e12eeac660ac80cf69cd5e282d",
        "subdir": "cpu_and_mobile/cpu-int4-rtn-block-32-acc-level-4",
        "size_mb": 390,
        "license": "Apache-2.0",
        "description": "Fastest and smallest download. Weaker on long numeric details.",
    },
    "phi-3.5-mini": {
        "label": "Phi-3.5 Mini (most accurate)",
        "repo": "microsoft/Phi-3.5-mini-instruct-onnx",
        "revision": "7230dcd6c1dd28aab70f263ecc8734ec9d9bcb70",
        "subdir": "cpu_and_mobile/cpu-int4-awq-block-128-acc-level-4",
        "size_mb": 2781,
        "license": "MIT",
        "description": "Best answers, biggest download, slower on older CPUs.",
    },
    "llama-3.2-1b": {
        "label": "Llama 3.2 1B",
        "repo": "onnx-community/Llama-3.2-1B-Instruct-GENAI-ONNX",
        "revision": "e983c740a38fcfa57fb4d124b18b644974c3d966",
        "subdir": "cpu_and_mobile/cpu-int4-rtn-block-32-acc-level-4",
        "size_mb": 1866,
        "license": "Llama 3.2 Community License",
        "description": "Alternative 1B model if Qwen output reads oddly for your files.",
    },
}

DEFAULT_MODEL = "qwen3-1.7b"

SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions about the user's own documents "
    "using ONLY the context you are given. Reply in natural flowing sentences, like a "
    "chatbot talking to a person. Never use bullet points, dashes, numbered lists, "
    "headings or markdown. Include the concrete details that matter (names, dates, "
    "times, amounts, reference numbers) inside your sentences. If the answer is not in "
    "the context, say so plainly instead of guessing."
)


def models_dir() -> Path:
    base = os.getenv("FILEWHISPER_MODELS_DIR")
    if base:
        return Path(base).expanduser()
    return Path(os.getenv("FILEWHISPER_HOME") or (Path.home() / ".filewhisper")) / "models"


def model_path(model_id: str) -> Path:
    """Directory that will hold genai_config.json for this model."""
    entry = MODEL_CATALOG[model_id]
    root = models_dir() / model_id
    return root / entry["subdir"] if entry["subdir"] else root


def is_downloaded(model_id: str) -> bool:
    """True only when the model can actually be loaded.

    Checking for genai_config.json alone is not enough: huggingface_hub writes
    that few-KB file within seconds while the multi-GB weights are still
    `.incomplete`, so a naive check reports "ready" almost immediately and the
    first load then fails. An interrupted download leaves the same state behind
    permanently, so this has to look at the weights, not at download progress.
    """
    if model_id not in MODEL_CATALOG:
        return False
    path = model_path(model_id)
    config = path / "genai_config.json"
    if not config.exists():
        return False

    # Any leftover partial file means the snapshot never finished.
    cache = models_dir() / model_id / ".cache" / "huggingface" / "download"
    if cache.exists():
        for _root, _dirs, files in os.walk(cache):
            if any(f.endswith(".incomplete") for f in files):
                return False

    try:
        decoder = json.loads(config.read_text())["model"]["decoder"]
        weights = path / decoder["filename"]
    except (OSError, ValueError, KeyError, TypeError):
        return True  # unusual layout: let the load attempt report the real error
    if not weights.exists():
        return False
    # Big models keep their tensors in a sidecar next to the graph.
    sidecar = weights.with_name(weights.name + ".data")
    if sidecar.exists() and sidecar.stat().st_size == 0:
        return False
    return weights.stat().st_size > 0


def engine_available() -> bool:
    """True when the onnxruntime-genai wheel exists for this interpreter."""
    try:
        import onnxruntime_genai  # noqa: F401
        return True
    except Exception:
        return False


def local_llm_disabled() -> bool:
    """Hosted deployments set this: never download GBs or burn server CPU there."""
    return os.getenv("FILEWHISPER_DISABLE_LOCAL_LLM", "").strip().lower() in ("1", "true", "yes")


def engine_unavailable_reason() -> str:
    import sys
    if local_llm_disabled():
        return "The built-in local model is disabled on this deployment."
    if sys.version_info < (3, 11):
        return (
            f"The built-in local model needs Python 3.11 or newer (this is "
            f"{sys.version_info.major}.{sys.version_info.minor}). Reinstall FileWhisper with a "
            "newer Python, or point it at Ollama / LM Studio instead."
        )
    if sys.platform == "darwin" and os.uname().machine == "x86_64":
        return (
            "The built-in local model has no build for Intel Macs. Install Ollama "
            "(ollama.com) and FileWhisper will detect it automatically, or add an API key."
        )
    return (
        "The built-in local model engine (onnxruntime-genai) is not installed. Run "
        "`pip install onnxruntime-genai` in FileWhisper's environment, or install Ollama."
    )


# =========================
# Download management
# =========================
_dl_lock = threading.Lock()
_dl_state = {
    "model_id": None,
    "status": "idle",   # idle | downloading | done | error
    "downloaded_mb": 0,
    "total_mb": 0,
    "error": "",
    "started_at": 0.0,
}


def download_state() -> dict:
    with _dl_lock:
        state = dict(_dl_state)
    # huggingface_hub flushes in large chunks, so the byte count can sit still
    # for minutes on a big file. Elapsed time gives the UI something honest to
    # show so a running download doesn't look wedged.
    state["elapsed_s"] = int(time.time() - state["started_at"]) if state["started_at"] else 0
    return state


def _dir_size_mb(path: Path) -> float:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1e6


def start_download(model_id: str) -> dict:
    """Kick off a background download. Returns the new state immediately.

    Progress is measured by walking the destination directory rather than by
    hooking huggingface_hub's tqdm, because partial files (.incomplete) already
    live there and the byte count stays correct across resumes.
    """
    if model_id not in MODEL_CATALOG:
        raise ValueError(f"Unknown model: {model_id}")
    if local_llm_disabled():
        raise RuntimeError("The built-in local model is disabled on this deployment.")
    if not engine_available():
        raise RuntimeError(engine_unavailable_reason())

    with _dl_lock:
        if _dl_state["status"] == "downloading":
            return dict(_dl_state)
        _dl_state.update(
            model_id=model_id, status="downloading", downloaded_mb=0,
            total_mb=MODEL_CATALOG[model_id]["size_mb"], error="",
            started_at=time.time(),
        )

    entry = MODEL_CATALOG[model_id]
    dest = models_dir() / model_id
    dest.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def _poll():
        while not stop.wait(1.0):
            with _dl_lock:
                if _dl_state["model_id"] != model_id:
                    return
                _dl_state["downloaded_mb"] = round(_dir_size_mb(dest), 1)

    def _run():
        try:
            from huggingface_hub import snapshot_download
            patterns = [f"{entry['subdir']}/*"] if entry["subdir"] else None
            snapshot_download(
                entry["repo"],
                revision=entry["revision"],
                local_dir=str(dest),
                allow_patterns=patterns,
                ignore_patterns=["*.md", ".gitattributes", "*.annotated.yaml"],
                max_workers=4,
            )
            if not (dest / entry["subdir"] / "genai_config.json").exists():
                raise RuntimeError("Download finished but genai_config.json is missing.")
            with _dl_lock:
                _dl_state.update(status="done", downloaded_mb=round(_dir_size_mb(dest), 1))
        except Exception as e:
            logger.exception("Local model download failed")
            with _dl_lock:
                _dl_state.update(status="error", error=str(e)[:300])
        finally:
            stop.set()

    threading.Thread(target=_poll, daemon=True).start()
    threading.Thread(target=_run, daemon=True).start()
    return download_state()


# =========================
# Built-in ONNX engine
# =========================
# onnxruntime-genai holds native state; one generation at a time per process.
_gen_lock = threading.RLock()
_loaded = {"model_id": None, "model": None, "tokenizer": None, "context_length": 4096}
_last_used = 0.0
_reaper_started = False


def _idle_seconds() -> int:
    """How long a loaded model may sit unused. 0 disables unloading."""
    try:
        return int(os.getenv("FILEWHISPER_MODEL_IDLE_SECONDS", "600"))
    except ValueError:
        return 600


def _start_reaper():
    """Drop the model from memory once it has been idle for a while.

    A loaded 1.7B model holds well over a gigabyte, which is a lot for an app
    that mostly sits in a browser tab doing nothing. Reloading costs ~1s.
    """
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def _loop():
        while True:
            time.sleep(60)
            idle = _idle_seconds()
            if idle <= 0:
                continue
            with _gen_lock:
                if _loaded["model"] is not None and time.time() - _last_used > idle:
                    logger.info("Unloading local model after %ds idle", idle)
                    _loaded.update(model_id=None, model=None, tokenizer=None)

    threading.Thread(target=_loop, daemon=True, name="filewhisper-model-reaper").start()


def _load(model_id: str):
    """Load (and cache) the model. Caller must hold _gen_lock."""
    if _loaded["model_id"] == model_id and _loaded["model"] is not None:
        return
    import onnxruntime_genai as og

    path = model_path(model_id)
    if not (path / "genai_config.json").exists():
        raise RuntimeError(f"Model '{model_id}' is not downloaded yet.")

    # Free the previous model before allocating the next one.
    _loaded.update(model_id=None, model=None, tokenizer=None)

    t0 = time.time()
    model = og.Model(og.Config(str(path)))
    tokenizer = og.Tokenizer(model)
    ctx = 4096
    try:
        cfg = json.loads((path / "genai_config.json").read_text())
        ctx = int(cfg.get("model", {}).get("context_length") or ctx)
    except (OSError, ValueError, TypeError):
        pass
    _loaded.update(model_id=model_id, model=model, tokenizer=tokenizer, context_length=ctx)
    logger.info("Loaded local model %s in %.1fs (context %d)", model_id, time.time() - t0, ctx)


def unload():
    with _gen_lock:
        _loaded.update(model_id=None, model=None, tokenizer=None)


def loaded_model_id():
    return _loaded["model_id"]


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _build_prompt(tokenizer, question_block: str) -> str:
    messages = json.dumps([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question_block},
    ])
    try:
        prompt = tokenizer.apply_chat_template(messages=messages, add_generation_prompt=True)
    except Exception:
        # Very old builds without template support: ChatML covers Qwen/Phi well enough.
        prompt = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{question_block}<|im_end|>\n<|im_start|>assistant\n"
        )
    # Qwen3 emits a reasoning block unless one is pre-closed. Pre-closing it keeps
    # answers fast and stops <think> text reaching the user; other models simply
    # never see these tokens because we only add them for a ChatML-style prompt.
    if prompt.rstrip().endswith("<|im_start|>assistant") or prompt.endswith("<|im_start|>assistant\n"):
        prompt = prompt.rstrip("\n") + "\n<think>\n\n</think>\n\n"
    return prompt


def generate(question_block: str, max_tokens: int = 400, model_id: str = None) -> str:
    """Run the built-in local model. `question_block` is the full user turn."""
    global _last_used
    import onnxruntime_genai as og

    model_id = model_id or DEFAULT_MODEL
    if model_id not in MODEL_CATALOG:
        model_id = DEFAULT_MODEL
    _start_reaper()

    with _gen_lock:
        _last_used = time.time()
        _load(model_id)
        model, tokenizer = _loaded["model"], _loaded["tokenizer"]
        ctx = _loaded["context_length"]

        prompt = _build_prompt(tokenizer, question_block)
        tokens = tokenizer.encode(prompt)

        # Leave room for the reply. When the retrieved context overflows the
        # model's window, drop from the MIDDLE: the instructions are at the front
        # and the actual question plus the generation prompt are at the end, so
        # slicing the token list would throw away the question itself.
        budget = max(512, ctx - max_tokens - 64)
        if len(tokens) > budget:
            keep_tail = min(3000, len(question_block) // 3)
            chars_per_token = max(1.0, len(question_block) / max(1, len(tokens)))
            head_chars = max(500, int((budget * chars_per_token) - keep_tail) - 200)
            for _ in range(8):
                trimmed = question_block[:head_chars] + "\n...\n" + question_block[-keep_tail:]
                prompt = _build_prompt(tokenizer, trimmed)
                tokens = tokenizer.encode(prompt)
                if len(tokens) <= budget:
                    break
                head_chars = int(head_chars * 0.7)
                if head_chars < 400:
                    # Last resort: keep only the tail, which holds the question.
                    prompt = _build_prompt(tokenizer, question_block[-keep_tail:])
                    tokens = tokenizer.encode(prompt)
                    break
            logger.info("Trimmed an oversized prompt to %d tokens (window %d)", len(tokens), ctx)

        params = og.GeneratorParams(model)
        # Greedy. Sampling and repetition_penalty both measurably increased
        # invented numbers and dates in testing, which is the worst failure mode
        # for document questions.
        params.set_search_options(max_length=len(tokens) + max_tokens, do_sample=False)
        generator = og.Generator(model, params)
        generator.append_tokens(tokens)

        stream = tokenizer.create_stream()
        out, produced = [], 0
        while not generator.is_done() and produced < max_tokens:
            generator.generate_next_token()
            out.append(stream.decode(generator.get_next_tokens()[0]))
            produced += 1
        _last_used = time.time()

    text = _THINK_RE.sub("", "".join(out))
    return text.replace("<think>", "").replace("</think>", "").strip()


# =========================
# External local servers
# =========================
# All of these speak the OpenAI chat-completions shape on loopback.
EXTERNAL_RUNTIMES = [
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("llama.cpp", "http://127.0.0.1:8080/v1"),
    ("Jan", "http://127.0.0.1:1337/v1"),
]

_detect_cache = {"at": 0.0, "value": None}
_DETECT_TTL = 10.0


def detect_external_runtime(force: bool = False) -> dict:
    """Return {name, base_url, models} for the first local server that answers."""
    now = time.time()
    if not force and _detect_cache["value"] is not None and now - _detect_cache["at"] < _DETECT_TTL:
        return _detect_cache["value"]

    found = {}
    for name, base_url in EXTERNAL_RUNTIMES:
        try:
            req = urllib.request.Request(f"{base_url}/models", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status != 200:
                    continue
                data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            found = {"name": name, "base_url": base_url, "models": models}
            break
        except Exception:
            continue

    _detect_cache.update(at=now, value=found)
    return found


# =========================
# Offline reader (no model at all)
# =========================
_STOPWORDS = {
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by", "can",
    "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "in", "is", "it",
    "me", "my", "of", "on", "or", "our", "should", "so", "some", "that", "the", "their",
    "them", "there", "these", "they", "this", "to", "was", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your",
}

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9][a-z0-9'./-]*")


def _terms(text: str) -> list:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1]


def extractive_answer(question: str, chunks: list) -> str:
    """Answer from the retrieved text alone, with no LLM anywhere.

    This is the guaranteed-offline path: it never calls out, never loads a model,
    and always produces something grounded in the user's own documents. The reply
    is honest about being quoted rather than written.
    """
    if not chunks:
        return "I could not find anything about that in your indexed documents."

    q_terms = set(_terms(question))
    if not q_terms:
        q_terms = set()

    # Rarer words across the retrieved set are the discriminating ones.
    freq = {}
    sentences = []
    for chunk in chunks:
        for raw in _SENT_SPLIT.split(chunk):
            s = raw.strip()
            if len(s) < 25 or len(s) > 600:
                continue
            terms = _terms(s)
            if not terms:
                continue
            sentences.append((s, set(terms)))
            for t in set(terms):
                freq[t] = freq.get(t, 0) + 1

    if not sentences:
        joined = " ".join(chunks)[:700].strip()
        return (
            "I could not run a language model, so here is the closest text from your "
            f"documents: {joined}"
        )

    total = len(sentences)
    scored = []
    for idx, (s, terms) in enumerate(sentences):
        overlap = q_terms & terms
        if not overlap:
            continue
        score = sum(1.0 / (1 + freq.get(t, 1) / total) for t in overlap)
        score /= 1 + len(terms) / 120.0  # mild preference for tight sentences
        scored.append((score, idx, s))

    if not scored:
        return (
            "I could not find a sentence in your documents that matches that question. "
            "Try rephrasing it with words that appear in the document."
        )

    scored.sort(key=lambda x: -x[0])
    picked = sorted(scored[:4], key=lambda x: x[1])

    seen, parts = set(), []
    for _, _, s in picked:
        key = s.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        parts.append(s if s[-1] in ".!?" else s + ".")

    return (
        "No language model is available right now, so this is quoted straight from your "
        "documents rather than written as an answer. " + " ".join(parts)
    )


# =========================
# CLI: used by the installers to pre-warm a model
# =========================
def _cli():
    """`python -m filewhisper.local_llm [model_id]` downloads a model with progress.

    The installers call this so a fresh install can answer its first question
    without waiting. Exits non-zero on failure so an installer can warn and
    carry on rather than abort the whole install.
    """
    import sys

    model_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    if model_id not in MODEL_CATALOG:
        print(f"Unknown model '{model_id}'. Options: {', '.join(MODEL_CATALOG)}")
        return 2
    if not engine_available():
        print(engine_unavailable_reason())
        return 1
    if is_downloaded(model_id):
        print(f"{MODEL_CATALOG[model_id]['label']} is already installed.")
        return 0

    entry = MODEL_CATALOG[model_id]
    print(f"Downloading {entry['label']} ({entry['size_mb']} MB, one time)...")
    start_download(model_id)
    last = -1
    while True:
        time.sleep(2)
        st = download_state()
        if st["status"] == "downloading":
            pct = int(min(99, st["downloaded_mb"] / max(1, st["total_mb"]) * 100))
            if pct != last:
                print(f"  {pct}%  ({st['downloaded_mb']:.0f} / {st['total_mb']} MB)", flush=True)
                last = pct
            continue
        if st["status"] == "done":
            print("Local model ready. FileWhisper can answer without any network.")
            return 0
        print(f"Download failed: {st['error']}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
