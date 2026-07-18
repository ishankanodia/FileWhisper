import faiss
import numpy as np
import os
import json
import pickle
import threading

# PDF text extraction (lightweight, no PyTorch)
import fitz  # PyMuPDF

# ONNX-based embeddings: same MiniLM model as before, but no PyTorch
from fastembed import TextEmbedding

import logging

logger = logging.getLogger(__name__)

_model = None
_ocr = None


def _get_model():
    global _model
    if _model is None:
        try:
            _model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
        except Exception as e:
            logger.error("Failed to load embedding model: %s", e)
            raise RuntimeError(
                "Could not load the embedding model. Check your internet connection for the first-time download."
            ) from e
    return _model


def _embed(texts) -> np.ndarray:
    """Embed a list of texts into a float32 matrix (FAISS requires float32)."""
    vectors = list(_get_model().embed(list(texts)))
    return np.array(vectors, dtype="float32")


def _get_ocr():
    """Lazy-load the ONNX OCR engine (torch-free). Optional dependency."""
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def _ocr_image(src) -> str:
    """OCR an image given a file path, PNG bytes, or ndarray. Returns extracted text.

    Gracefully returns "" if the optional OCR engine is not installed so that
    text/markdown/digital-PDF indexing still works on a minimal install.
    """
    try:
        ocr = _get_ocr()
    except ImportError:
        logger.warning(
            "OCR engine (rapidocr-onnxruntime) not installed; skipping image text extraction."
        )
        return ""
    result, _ = ocr(src)
    if not result:
        return ""
    return " ".join(line[1] for line in result)

# Persistence paths
DATA_DIR = os.getenv("RAG_DATA_DIR", os.path.expanduser("~/.filewhisper/rag_data"))
INDEX_PATH = os.path.join(DATA_DIR, "index.faiss")
DOCS_PATH = os.path.join(DATA_DIR, "documents.pkl")
SOURCES_PATH = os.path.join(DATA_DIR, "sources.json")

os.makedirs(DATA_DIR, exist_ok=True)

# In-memory state
documents = []
doc_sources = []  # parallel list: one source path per chunk
sources = []      # unique list of ingested file paths
index = None

# FAISS and the parallel lists are not thread-safe; FastAPI serves requests on a
# thread pool, so concurrent /ingest and /ask must not mutate them unguarded.
_lock = threading.RLock()


def _save():
    global index, documents, doc_sources, sources
    if index is not None:
        faiss.write_index(index, INDEX_PATH)
    with open(DOCS_PATH, "wb") as f:
        pickle.dump((documents, doc_sources), f)
    with open(SOURCES_PATH, "w", encoding="utf-8") as f:
        json.dump(sources, f)


def _load():
    global index, documents, doc_sources, sources
    if os.path.exists(INDEX_PATH) and os.path.exists(DOCS_PATH):
        index = faiss.read_index(INDEX_PATH)
        with open(DOCS_PATH, "rb") as f:
            loaded = pickle.load(f)
            if isinstance(loaded, tuple):
                documents, doc_sources = loaded
            else:
                # backward compat: old format was just a list
                documents = loaded
                doc_sources = ["unknown"] * len(documents)
    if os.path.exists(SOURCES_PATH):
        with open(SOURCES_PATH, "r", encoding="utf-8") as f:
            sources = json.load(f)


# Load on startup
_load()


def split_text(text, chunk_size=140, overlap=40):
    """Split text into overlapping word windows.

    Overlap keeps short structured data (flight rows, names, PNRs) from being
    cut across a chunk boundary, which improves retrieval of specifics.
    """
    words = text.split()
    if not words:
        return []
    step = max(1, chunk_size - overlap)
    chunks = []
    for i in range(0, len(words), step):
        piece = words[i:i + chunk_size]
        if piece:
            chunks.append(" ".join(piece))
        if i + chunk_size >= len(words):
            break
    return chunks


def extract_text_from_file(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".txt" or ext == ".md":
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    elif ext == ".pdf":
        text = ""
        try:
            doc = fitz.open(filepath)

            def is_gibberish(txt: str) -> bool:
                cleaned_txt = txt.strip()
                if not cleaned_txt:
                    return True
                words = cleaned_txt.split()
                if not words:
                    return True
                gibberish_words = 0
                for w in words:
                    if w.isdigit():
                        continue
                    clean = w.strip(".,;:?!'\"()[]{}|•")
                    if not clean:
                        continue
                    if not clean.isalnum():
                        gibberish_words += 1
                        continue
                    # Check if it contains a weird mixed alphanumeric structure
                    has_letters = any(c.isalpha() for c in clean)
                    has_digits = any(c.isdigit() for c in clean)
                    if has_letters and has_digits:
                        if len(clean) != 10:  # Allow 10-char PAN numbers
                            gibberish_words += 1
                
                ratio_gibberish = gibberish_words / len(words)
                return ratio_gibberish > 0.15

            for page_num, page in enumerate(doc):
                page_text = page.get_text()
                
                # Check if page text is empty or has a very low ratio of normal alphanumeric characters
                cleaned = page_text.strip()
                if len(cleaned) > 0:
                    alnum_count = sum(1 for c in cleaned if c.isalnum() or c.isspace())
                    ratio = alnum_count / len(cleaned)
                else:
                    ratio = 0
                
                # Fallback to OCR if the text layer is too short, mostly garbage, or detected as gibberish
                if len(cleaned) < 60 or ratio < 0.65 or is_gibberish(page_text):
                    try:
                        pix = page.get_pixmap(dpi=150)
                        ocr_page_text = _ocr_image(pix.tobytes("png"))
                        if ocr_page_text.strip():
                            page_text = ocr_page_text
                    except Exception as ocr_err:
                        logger.warning(f"OCR fallback failed on page {page_num} of {filepath}: {ocr_err}")
                
                text += page_text + "\n"
        except Exception as e:
            text = f"[Error reading PDF: {e}]"
        return text

    elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]:
        try:
            return _ocr_image(filepath)
        except Exception as e:
            return f"[Error reading image: {e}]"

    else:
        return ""


def add_chunks(chunks: list, source_path: str):
    global index, documents, doc_sources, sources

    if not chunks:
        return 0

    embeddings = _embed(chunks)
    dimension = embeddings.shape[1]

    with _lock:
        if index is None:
            index = faiss.IndexFlatL2(dimension)

        index.add(embeddings)
        documents.extend(chunks)
        doc_sources.extend([source_path] * len(chunks))  # track source per chunk

        if source_path not in sources:
            sources.append(source_path)

        _save()
    return len(chunks)


def ingest_path(path: str) -> dict:
    """Ingest a file or entire folder recursively."""
    results = []

    SUPPORTED = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

    if os.path.isfile(path):
        files = [path]
    elif os.path.isdir(path):
        files = []
        for root, _, fnames in os.walk(path):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() in SUPPORTED:
                    files.append(os.path.join(root, fname))
    else:
        return {"error": "Invalid path"}

    # Compare normalized paths so the same file reached with different casing or
    # slashes (common on Windows) isn't indexed twice.
    def _norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    indexed = {_norm(s) for s in sources}

    for filepath in files:
        if _norm(filepath) in indexed:
            results.append({"file": filepath, "status": "skipped", "chunks": 0, "reason": "already indexed"})
            continue
        text = extract_text_from_file(filepath)
        if not text.strip():
            results.append({"file": filepath, "status": "skipped", "chunks": 0})
            continue
        chunks = split_text(text)
        n = add_chunks(chunks, filepath)
        results.append({"file": filepath, "status": "indexed", "chunks": n})

    return {
        "status": "done",
        "files_processed": len(files),
        "details": results
    }


def retrieve(query: str, k: int = 8) -> list:
    """Returns list of (chunk_text, source_filepath) tuples."""
    global index, documents, doc_sources

    if index is None or len(documents) == 0:
        return []

    query_embedding = _embed([query])
    with _lock:
        distances, indices = index.search(query_embedding, k)
        # FAISS pads with -1 when the index holds fewer than k vectors; a plain
        # `i < len(documents)` lets -1 through and wraps to the last chunk.
        return [
            (documents[i], doc_sources[i])
            for i in indices[0] if 0 <= i < len(documents)
        ]


def get_indexed_sources() -> list:
    return sources


def clear_index():
    global index, documents, doc_sources, sources
    with _lock:
        index = None
        documents = []
        doc_sources = []
        sources = []
        for path in [INDEX_PATH, DOCS_PATH, SOURCES_PATH]:
            if os.path.exists(path):
                os.remove(path)
