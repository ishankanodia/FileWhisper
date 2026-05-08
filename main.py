from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rag import ingest_path, retrieve, get_indexed_sources, clear_index

import re
import os
from dotenv import load_dotenv
from groq import Groq
from langgraph.graph import StateGraph

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
client = Groq(api_key=GROQ_API_KEY)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# =========================
# Models
# =========================
class IngestRequest(BaseModel):
    path: str  # file or folder path


class Query(BaseModel):
    question: str


# =========================
# Utils
# =========================
def clean_text(text: str) -> str:
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def call_llm(prompt: str, max_tokens=400):
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": "Answer ONLY using provided context. Be structured and clear."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.5,
        max_tokens=max_tokens
    )
    return response.choices[0].message.content.strip()


# =========================
# LangGraph
# =========================
class GraphState(dict):
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
You are a helpful research assistant.

Answer ONLY using the context below. Be structured and clear.

Format:
- Use bullet points for lists
- Keep it concise but complete
- No markdown bold (**)

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
    return FileResponse("static/index.html")


@app.get("/browse")
def browse(path: str = "/Users"):
    """Return contents of a directory for the folder picker UI."""
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