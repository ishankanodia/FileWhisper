from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
import os
import json
import pickle

# PDF
import fitz  # PyMuPDF

# OCR for images
from PIL import Image
import pytesseract

model = SentenceTransformer('all-MiniLM-L6-v2')

# Persistence paths
DATA_DIR = "rag_data"
INDEX_PATH = os.path.join(DATA_DIR, "index.faiss")
DOCS_PATH = os.path.join(DATA_DIR, "documents.pkl")
SOURCES_PATH = os.path.join(DATA_DIR, "sources.json")

os.makedirs(DATA_DIR, exist_ok=True)

# In-memory state
documents = []
doc_sources = []  # parallel list: one source path per chunk
sources = []      # unique list of ingested file paths
index = None


def _save():
    global index, documents, doc_sources, sources
    if index is not None:
        faiss.write_index(index, INDEX_PATH)
    with open(DOCS_PATH, "wb") as f:
        pickle.dump((documents, doc_sources), f)
    with open(SOURCES_PATH, "w") as f:
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
        with open(SOURCES_PATH, "r") as f:
            sources = json.load(f)


# Load on startup
_load()


def split_text(text, chunk_size=150):
    words = text.split()
    return [
        " ".join(words[i:i + chunk_size])
        for i in range(0, len(words), chunk_size)
        if words[i:i + chunk_size]
    ]


def extract_text_from_file(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".txt" or ext == ".md":
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    elif ext == ".pdf":
        text = ""
        try:
            doc = fitz.open(filepath)
            for page in doc:
                text += page.get_text()
        except Exception as e:
            text = f"[Error reading PDF: {e}]"
        return text

    elif ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]:
        try:
            img = Image.open(filepath)
            text = pytesseract.image_to_string(img)
            return text
        except Exception as e:
            return f"[Error reading image: {e}]"

    else:
        return ""


def add_chunks(chunks: list, source_path: str):
    global index, documents, doc_sources, sources

    if not chunks:
        return 0

    embeddings = model.encode(chunks)
    dimension = embeddings.shape[1]

    if index is None:
        index = faiss.IndexFlatL2(dimension)

    index.add(np.array(embeddings))
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

    for filepath in files:
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


def retrieve(query: str, k: int = 5) -> list:
    """Returns list of (chunk_text, source_filepath) tuples."""
    global index, documents, doc_sources

    if index is None or len(documents) == 0:
        return []

    query_embedding = model.encode([query])
    distances, indices = index.search(np.array(query_embedding), k)
    return [
        (documents[i], doc_sources[i])
        for i in indices[0] if i < len(documents)
    ]


def get_indexed_sources() -> list:
    return sources


def clear_index():
    global index, documents, doc_sources, sources
    index = None
    documents = []
    doc_sources = []
    sources = []
    for path in [INDEX_PATH, DOCS_PATH, SOURCES_PATH]:
        if os.path.exists(path):
            os.remove(path)