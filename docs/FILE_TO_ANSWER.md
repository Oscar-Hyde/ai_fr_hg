# File → AI → Attachment: the end-to-end document lifecycle

This document walks the **complete** path a document takes through the
platform — from the moment a user attaches a file in the AI Assistant, through
automatic ingestion, indexing, retrieval and grounded answering, to citing and
linking the source and its chunks. It also records the development and
production processes for this flow and the specific changes made on this
branch.

The whole loop is local-first: no prompt, document or embedding ever leaves the
network, and every step is recorded in the platform's audit, execution and
search logs.

---

## 1. The lifecycle, end to end

```
Attach a file in chat            (ai_assistant.js  →  FileUploader)
   │
   ▼
POST /api/method/ai_fr_hg.api.knowledge.upload_document
   │  check_capability("document_upload")        governance
   │  check_document_quota()                     governance
   │  ingest_file()
   │     ├─ resolve the Frappe File by file_url
   │     ├─ create AI Document (source_type=File, source_file=file_url)
   │     └─ enqueue_processing()  → background worker (queue="long")
   ▼
Background worker: process_document()
   ├─ 1. read      extract_source_text()  → reader dispatcher
   │                 (PDF/DOCX/XLSX/PPTX/TXT/MD/CSV/JSON/HTML/…)  → bytes → text
   ├─ 2. extract   store content, language, counts, checksum, metadata on AI Document
   ├─ 3. chunk     chunk_text()            structure-aware, heading-aware windows
   ├─ 4. embed     index_document()        embed_chunks() in batches of 16
   ├─ 5. index     write AI Document Chunk rows (base64 float32 vectors)
   └─ publish realtime event  ai_document_processed   (status = Indexed)
   │
   ▼
User asks about the file       (chat composer → send_message)
   │  documents=[<uploaded doc>]          ← the new wiring
   ├─ prepare_documents_for_turn()         ← extract inline if needed; no 45s poll
   ├─ run_agent_turn(..., documents=…, extra_context=…)
   │     ├─ retrieve(..., documents=…)     ← retrieval scoped to indexed uploads
   │     ├─ extra_context                  ← extracted text when not yet embedded
   │     ├─ build_system_prompt(context)   ← numbered, cited context block
   │     └─ run_chat()  → tool loop → final answer
   ▼
Persist + attach
   ├─ AI Message rows (user + assistant, citations, token accounting)
   ├─ citations → [1], [2]… clickable → /app/ai-document/<doc>
   └─ the uploaded file is the AI Document's Source File (attached)
```

### Why this matters

Before this change the loop had a **race**: `upload_document` enqueued indexing
in the background and returned immediately, but the chat composer pre-filled
"Summarise the document I just uploaded" and sent it straight away. If the
worker hadn't finished embedding, retrieval found no chunks and the model
answered *"I don't have that information"* — the exact opposite of the feature.

Now `send_message` accepts the just-uploaded `documents`, prepares them for
the turn (inline extraction if the worker has not finished, a short wait only
when nothing readable exists), and scopes retrieval to those records. Extracted
but not-yet-embedded text is injected as extra context, so the answer is
grounded in the new upload even on the very first question.

---

## 2. Data model involved

| DocType | Role |
| --- | --- |
| `AI Knowledge Base` | Owns chunking + embedding policy (chunk size/overlap, top_k, similarity threshold, embedding model). |
| `AI Document` | One ingested source: extracted text, metadata, source file attachment, status, chunk/embed counts. |
| `AI Document Chunk` | One retrievable slice with its embedding (base64 float32), heading, page, checksum, source document. |
| `AI Conversation` | Chat thread bound to an agent; conversation-level knowledge bases. |
| `AI Message` | A turn's user/assistant/tool messages, with citations and token accounting. |
| `AI Execution Log` | Every model call (redacted). |
| `AI Audit Log` | Privileged actions (e.g. conversation creation). |
| `AI Search Query` | Retrieval telemetry (queued, not inline). |

---

## 3. Chunking policy and its validation

`AI Knowledge Base.validate_chunking()` enforces:

- `chunk_size >= 100`
- `0 <= chunk_overlap < chunk_size`
- `0 <= similarity_threshold <= 1` (values such as `25` are stored as `0.25`)

On this branch these are now **enforced before the server is reached**:

1. **DocType field constraints** (`ai_knowledge_base.json`): `min_value` /
   `max_value` on `chunk_size`, `chunk_overlap`, `top_k` and
   `similarity_threshold`, plus human descriptions so the form explains each
   knob.
2. **Client-side validation**
   (`ai_knowledge/doctype/ai_knowledge_base/ai_knowledge_base.js`): a
   `validate` handler throws the same errors inline, and the `chunk_size` /
   `chunk_overlap` handlers auto-correct an overlap that crosses chunk size the
   moment it happens.

This kills the repeated **HTTP 417** saves seen in the console: an invalid
value is now either prevented or corrected in the form, and if it ever still
reaches the server the Python message names the offending value.

### Choosing chunk parameters

- **Smaller chunks** → finer retrieval precision, more context overhead.
- **Larger chunks** → more local context per passage, less precision.
- **Overlap** should be ~10–20% of chunk size so a fact spanning a boundary is
  still fully retrievable from either side.
- **Similarity threshold** of `0` returns everything (noise); `1` only exact
  matches. `0.25` is a reasonable default for local embedding models; lower it
  if relevant results are being filtered out.

---

## 4. Error handling: timeouts and failures

### Chat timeouts

An interactive turn *may* run under a shared **deadline** (`ai_fr_hg.ai.deadline`).
The default **Max Turn Duration** is `0` (unlimited) so a local model is not
cut off on its first, slowest run. When a positive budget is configured, every
provider HTTP call is clamped to the remaining time so a reverse proxy cannot
return a bare 504. Two failure shapes are handled:

| Situation | Before | Now |
| --- | --- | --- |
| Overall turn budget exhausted | `DeadlineExceededError` → saved "ran out of time" answer | unchanged (budget) |
| Provider read timeout (`ProviderTimeoutError`) | **HTTP 417** surfaced to the browser | saved, friendly `PROVIDER_TIMEOUT_ANSWER` |
| Provider unreachable (`ProviderOfflineError`) | **HTTP 417** surfaced to the browser | saved, friendly `PROVIDER_OFFLINE_ANSWER` |

In `run_agent_turn`, the model loop now catches `DeadlineExceededError`,
`ProviderTimeoutError` and `ProviderOfflineError` (in that order — the first is
a subclass of the second) and persists an explanatory answer marked
`status="Failed"`, so the thread stays coherent and the user is told what to
change instead of seeing a bare error.

### Ingestion failures

`process_document` writes `status = Failed` with a readable `error_message`
(MissingDependency, unsupported format, empty extraction, indexing failure),
publishes a realtime event, and logs a traceback. `Failed` documents surface on
the knowledge dashboard and the AI Document form.

---

## 5. Development process

### Layout

- **Service layer** in `ai_fr_hg/ai/` — no DocType controllers, no web
  request. Testable without a browser.
- **API** in `ai_fr_hg/api/` — thin whitelisted wrappers.
- **DocType controllers** — validation and thin actions that delegate into
  `ai/`.
- **Desk assets** use Frappe's native layout: standard DocType scripts are
  colocated under `<module>/doctype/<doctype>/`, shared bundles remain in
  `public/js/`, and pages live under their owning module's `page/` directory.

### Extending

- New file formats → register an `ai_document_readers` entry in `hooks.py`
  (see `docs/EXTENDING.md`).
- New model runtimes → `ai_providers`.
- New agent capabilities → `ai_tools`.

### Testing

```bash
# fast, no DB / runtime
bench --site your-site.local run-tests --app ai_fr_hg --module test_units

# integration (colocated DocType tests + service layer, model runtime stubbed)
bench --site your-site.local run-tests --app ai_fr_hg
```

Unit tests cover chunking, vector math, deadlines, JSON parsing, the network
guard and tool wire formats without touching a database. Integration tests
stub the chat and embedding engines so CI needs no GPU or Ollama.

---

## 6. Production / operations process

1. **Runtime** — keep `ollama serve` running; pull a chat model and an
   embedding model (`nomic-embed-text` is required for semantic search).
2. **Background workers + scheduler** — ingestion, indexing, health checks and
   usage rollups run on workers and the scheduler:
   `bench --site your-site.local enable-scheduler` and run `bench worker`.
3. **Tuning** — if chats time out, raise **Request Timeout** on the provider,
   leave **Max Turn Duration** at `0` on a local bench, or switch to a smaller
   model. If retrieval misses relevant passages, lower the similarity threshold
   / raise top_k; if it returns noise, raise the threshold.
4. **Monitoring** — `/app/ai-operations` shows provider health, latency, token
   usage and failed executions. `AI Execution Log`, `AI Search Query` and
   `AI Audit Log` are retained per `hooks.py` and pruned by the weekly cleanup.
5. **Air-gapped** — Strict Local Only is on by default; transfer model files
   offline with `ollama save`/`load`; export/import knowledge bases as JSON.
6. **Backups** — because embeddings live in DocTypes, standard `bench backup`
   covers the whole index.

---

## 7. What changed on this branch

| Area | Change |
| --- | --- |
| `ai/ingestion.py` | `prepare_documents_for_turn()` extracts unread uploads inline and only polls briefly when nothing readable exists. |
| `api/chat.py` | `send_message` accepts `documents`, checks read permission, prepares them, and passes indexed names plus extracted text to the agent. |
| `ai/agent.py` | `run_agent_turn` accepts `documents` and scopes retrieval even when `use_knowledge` is off; language instructions when context is labelled; catches provider OOM/timeout/offline as saved answers. |
| `ai/language.py` | Detects written language (BG first-class) and labels excerpts / retrieved chunks. |
| `ai/knowledge.py` | `retrieve`, `semantic_search`, `keyword_search` accept a `documents` filter and scope targets to the uploaded files' knowledge bases. |
| `api/knowledge.py` | One-shot `ask` accepts `documents`, waits for indexing, and grounds the answer on the chosen records. |
| `public/js/ai_helpers.js` | `frappe.ai.ask` forwards `documents` so the AI Document "Ask About This" button answers from that record. |
| `ai_knowledge/doctype/ai_document/ai_document.js` | "Ask About This" passes the current document to scope retrieval. |
| `ai_knowledge_base.json` | Field constraints (`min_value`/`max_value`) and descriptions for chunking settings. |
| `ai_knowledge_base.py` | Clearer validation messages that name the offending value. |
| `ai_knowledge/doctype/ai_knowledge_base/ai_knowledge_base.js` | Client-side validation + auto-correction of chunk overlap. |
| `ai_assistant.js` | Tracks just-uploaded documents and passes them on the next send. |
