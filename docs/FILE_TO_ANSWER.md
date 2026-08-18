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
   │  file_url + exact file_record + canonical folder
   │  check_capability("document_upload")        governance
   │  check_document_quota()                     governance
   │  ingest_file()
   │     ├─ resolve exact File by stable file_record (URL-only legacy fails on ambiguity)
   │     ├─ authorize File + parent + Knowledge Base
   │     ├─ create AI Document (source_file URL + source_file_record Link)
   │     ├─ set folder/source_folder + normalized organization identity
   │     └─ enqueue_processing()  → background worker (queue="long")
   ▼
Background worker: process_document()
   ├─ 1. read      extract_source_text()  → reader dispatcher
   │                 (PDF/DOCX/XLSX/PPTX/TXT/MD/CSV/JSON/HTML/…)  → bytes → text
   ├─ 2. extract   store content, counts, checksum, metadata on AI Document
   ├─ 3. chunk     chunk_text()            structure-aware, heading-aware windows
   ├─ 4. embed     index_document()        embed_chunks() in batches of 16
   ├─ 5. index     write AI Document Chunk rows (base64 float32 vectors)
   └─ publish realtime event  ai_document_processed   (status = Indexed)
   │
   ▼
User asks about the file       (chat composer → send_message)
   │  documents=[<uploaded doc>]          ← the new wiring
   ├─ wait_for_indexed(documents)          ← bounded by turn budget; no more race
   ├─ run_agent_turn(..., documents=…)
   │     ├─ retrieve(..., documents=…)     ← retrieval scoped to the upload
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

Now `send_message` accepts the just-uploaded `documents`, waits for them to
reach `Indexed` (bounded by the turn's time budget), and scopes retrieval to
those records, so the answer is grounded in the new upload even on the very
first question.

---

## 2. Data model involved

| DocType | Role |
| --- | --- |
| `AI Knowledge Base` | Owns chunking + embedding policy (chunk size/overlap, top_k, similarity threshold, embedding model). |
| `File` | Native physical attachment identity and canonical folder hierarchy. `File.name` is stable even when another File shares the same URL/bytes. |
| `AI Folder Settings` | Optional metadata for a canonical File folder; never a second hierarchy. |
| `AI Document` | One stable ingested-source identity: `source_file_record` identifies the exact File; `folder`/`source_folder` place it; `organization_name_key` enforces parent-scoped display identity; extracted content/status/counts remain ordinary processing state. |
| `AI Document Chunk` | One retrievable slice with its embedding (base64 float32), heading, page, checksum, source document. |
| `AI Conversation` | Chat thread bound to an agent; conversation-level knowledge bases. |
| `AI Message` | A turn's user/assistant/tool messages, with citations and token accounting. |
| `AI Execution Log` | Every model call (redacted). |
| `AI Audit Log` | Privileged actions (e.g. conversation creation). |
| `AI Search Query` | Retrieval telemetry (queued, not inline). |

### Stable File identity and placement

A File URL or checksum is content/location identity, not record identity. Frappe
may retain separate File rows for copies while deduplicating physical bytes.
Every uploader in this app therefore propagates the exact upload response
`name` as `file_record` together with `file_url`.

For pre-migration AI Documents that have only a URL, resolution is deliberately
bounded and fail-closed:

1. one exact File attachment to that AI Document is accepted;
2. otherwise one globally unique File row for the URL is accepted;
3. multiple exact or global candidates raise a fetch error and remain
   unresolved until an operator supplies the exact File.

No pipeline chooses the oldest duplicate. Patch
`v0_0_9_ai_document_tree_organization` applies the same singleton rule during
backfill and caps ambiguity tracking at two candidates.

Placement is orthogonal to processing. `AI Document.folder` links to canonical
native File folder `Home/...`; `source_folder` is synchronized provenance. The
native AI Document Tree uses those same records for lazy mixed navigation.
Moving a document retains its AI Document/chunks/index identity. Copying creates
an independent AI Document and File identity, resets derived processing/index
state, and does not copy chunks, embeddings, shares, or unrelated attachments.
See [AI Document Tree](DOCUMENT_TREE.md).

---

## 3. Chunking policy and its validation

`AI Knowledge Base.validate_chunking()` enforces:

- `chunk_size >= 100`
- `0 <= chunk_overlap < chunk_size`
- `0 <= similarity_threshold <= 1`

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

An interactive turn runs under a shared **deadline** (`ai_fr_hg.ai.deadline`).
Every provider HTTP call is clamped to the remaining budget, so a slow local
model can never hold the connection past what the reverse proxy allows. Two
failure shapes are handled:

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
- **Organization** follows UI/native Tree View → `api/document_tree.py` facade →
  `ai/document_tree.py` service → native File/AI Document and existing
  processing services. Tree JavaScript contains no business authorization.

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
guard and tool wire formats without touching a database. Focused folder/tree
pure suites additionally cover stable File ambiguity, escaped search/prefixes,
permission filtering, continuation, collisions, and migration selection.
Integration tests stub the chat and embedding engines so CI needs no GPU or
Ollama, and the AI Document integration suite covers organization lifecycle and
processing relationships.

The latest environment available for this branch passed 23 folder and 28 tree
pure tests. A live Bench/Frappe v17 site was unavailable there, so browser,
asset, MariaDB/PostgreSQL, concurrency/load, and complete reader/index/retrieval
regressions are still production release checks.

---

## 6. Production / operations process

1. **Runtime** — keep `ollama serve` running; pull a chat model and an
   embedding model (`nomic-embed-text` is required for semantic search).
2. **Background workers + scheduler** — ingestion, indexing, health checks,
   usage rollups, and AI Document recursive/bulk organization run on workers and
   the scheduler: `bench --site your-site.local enable-scheduler`; keep a
   `bench worker --queue long` process available.
3. **Tuning** — if chats time out, raise **Request Timeout** / **Max Turn
   Seconds** in AI Platform Settings or switch to a smaller model. If retrieval
   misses relevant passages, lower the similarity threshold / raise top_k; if
   it returns noise, raise the threshold.
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
| `ai/ingestion.py` | Added `wait_for_indexed()` so interactive chat can await a background index within the turn budget. |
| `api/chat.py` | `send_message` accepts `documents`, checks read permission, waits for indexing, and passes them to the agent. |
| `ai/agent.py` | `run_agent_turn` accepts `documents` and scopes retrieval; catches `ProviderTimeoutError`/`ProviderOfflineError` gracefully with friendly saved answers. |
| `ai/knowledge.py` | `retrieve`, `semantic_search`, `keyword_search` accept a `documents` filter and scope targets to the uploaded files' knowledge bases. |
| `api/knowledge.py` | One-shot `ask` accepts `documents`, waits for indexing, and grounds the answer on the chosen records. |
| `public/js/ai_helpers.js` | `frappe.ai.ask` forwards `documents` so the AI Document "Ask About This" button answers from that record. |
| `ai_knowledge/doctype/ai_document/ai_document.js` | "Ask About This" passes the current document to scope retrieval. |
| `ai_knowledge_base.json` | Field constraints (`min_value`/`max_value`) and descriptions for chunking settings. |
| `ai_knowledge_base.py` | Clearer validation messages that name the offending value. |
| `ai_knowledge/doctype/ai_knowledge_base/ai_knowledge_base.js` | Client-side validation + auto-correction of chunk overlap. |
| `ai_assistant.js` | Tracks just-uploaded documents and passes them on the next send. |
| `ai/document_tree.py` + `api/document_tree.py` | Native mixed-tree lazy reads and transactional create/rename/move/copy/delete/bulk services with permissions, locks, stale-state fingerprints, audit, and workers. |
| `public/js/ai_document_tree.js` | Frappe-native AI Document Tree View, capability-aware actions, breadcrumbs, filters/search, loaded expansion, refresh, and bulk selection. |
| `ai/folders.py` + `utils/file_hooks.py` | Canonical File-folder lifecycle, direct-move locking, stable-link-first provenance synchronization, and fail-closed legacy ambiguity. |
| `api/knowledge.py` + upload clients | Propagate exact `file_record` and canonical folder into ingestion. |
| `AI Document` schema/controller | Indexed folder, normalized scoped organization identity, stable File Link, copy provenance, revision/stale validation, and uniqueness enforcement. |
| `v0_0_9_ai_document_tree_organization` | Paged retry-safe backfill and scoped uniqueness migration; singleton-only File identity resolution. |
| `tests/test_folder_units.py` + `tests/test_document_tree_units.py` | Focused no-Bench permission, ambiguity, literal-LIKE, continuation, portable-locking, no-op move, and migration regressions (latest: 23 + 28 passed). |
