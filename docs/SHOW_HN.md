# Show HN draft: FileWhisper

Everything below is ready to copy-paste. Pick the title, set the URL, paste the
first comment right after posting.

---

## Title (pick one, keep "Show HN:" prefix)

**Recommended:**
> Show HN: FileWhisper: Chat with your files, 100% local, one-line install

Alternates:
> Show HN: Local RAG over your files: no cloud, no API key, one-line install
> Show HN: Ask questions about your local files, fully offline

Keep it under ~80 chars. No emoji, no hype words ("revolutionary", "AI-powered"),
no trailing period; HN readers downvote marketing tone.

## URL to submit

Submit the **website** (better first impression than a repo for non-HN-natives):
> https://ishankanodia.github.io/FileWhisper/

(If you'd rather send people straight to code: https://github.com/ishankanodia/FileWhisper)

## First comment (post immediately after submitting)

HN convention: the author drops a context comment right away. Be a builder, not a
marketer. Say what it is, why you built it, what's technically interesting, and
what's rough. Invite criticism.

```
Author here. I kept wanting to ask questions about my own documents (leases,
manuals, scanned PDFs) without uploading them to someone else's server, so I
built FileWhisper: a local RAG app where the files never leave your machine.

Parsing, OCR, embeddings (ONNX MiniLM), and vector search (FAISS) all run
locally; only your question plus the matched snippets go to an LLM, and even
that can be a free keyless model, so you can run it with zero API keys and zero
accounts. Install is one line (curl|bash or irm|iex); it builds an isolated env
and drops a double-click app on your Desktop. No PyTorch: the whole thing incl.
OCR is ~435 MB.

Stack: Python/FastAPI backend, single-file HTML UI in the browser, LangGraph for
the retrieve→answer→followup pipeline. No Electron/Tauri; I dropped the native
shell because notarization/signing wasn't worth it for a local tool.

Known rough edges: Windows/Linux installers are newer than the macOS one, and
retrieval quality on very large/vague queries still needs tuning. Would love
feedback on the install flow and on how it answers over your own files.
```

## Before you post: checklist

- [ ] README has the screenshot/GIF (done) and the install command works when copy-pasted
- [ ] The one-line installers actually work on a clean machine for each OS you claim
      (esp. Windows: smoke-test before posting, or say "macOS/Linux tested, Windows beta")
- [ ] Website link returns 200 and renders
- [ ] You have ~3-4 free hours right after posting to answer every comment fast

## Timing

- Best: **Tuesday-Thursday, 9:00-11:00am US Eastern** (peak HN traffic, fresh "new" queue).
- Avoid Fri-Sun and late evenings.
- Post once. If it doesn't catch, you may repost a *significantly* improved version
  weeks later; don't spam.

## Engagement tips

- Reply to every comment in the first few hours; HN rewards author responsiveness.
- Answer skeptics with specifics, not defensiveness ("fair, here's how X works…").
- Common questions to pre-think: How is this different from NotebookLM/ChatGPT?
  (answer: local, no upload). What model by default? (free keyless). What data
  leaves my machine? (only the final LLM call, which you control).
- Don't ask for upvotes anywhere; it's against HN rules and gets posts flagged.
