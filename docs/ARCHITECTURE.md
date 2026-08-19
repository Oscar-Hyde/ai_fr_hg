# Architecture

## Principles

1. **Frappe-native ownership.** Entities are DocTypes; controllers, ORM/query
   builder, permission hooks, File, background jobs, scheduler, realtime and
   Desk are the default authorities. Legacy raw SQL remains and MariaDB is the
   only qualified database; portability work must move toward Frappe APIs.
2. **Local intent, layered enforcement.** A URL/address guard restricts configured
   endpoints, but SEC-04 connection hardening remains open. Host firewall and
   egress controls are required as the actual network boundary.
3. **Supervised background operation.** Supported ingestion, indexing, model
   discovery, health, usage and retention paths use Frappe workers/scheduler.
   Durable cancellation/recovery and several state machines remain open.
4. **Traceability is the target.** Model/tool/audit records exist, but message/task
   links, queue timing, stale-run reconciliation and telemetry redaction remain
   tracked gaps; current records are not described as a complete trace.
5. **Extension over modification.** Providers, readers and tools are registries
   merged from hooks, so other apps extend the platform without forking it.

---

## Module layout

```
ai_fr_hg/
├── ai/                      Service layer (no DocType controllers here)
│   ├── providers/           Runtime adapters
│   │   ├── base.py          BaseProvider contract + shared dataclasses
│   │   ├── ollama.py        Native Ollama API
│   │   ├── openai_compatible.py   vLLM, LM Studio, llama.cpp, …
│   │   └── __init__.py      Registry, merges the `ai_providers` hook
│   ├── readers/             Document text extraction
│   │   ├── base.py          BaseReader contract, ReadResult, MissingDependency
│   │   ├── plain.py         Text, Markdown, CSV, JSON, XML, HTML, code, images
│   │   ├── office.py        PDF, Word, Excel, PowerPoint, OpenDocument
│   │   └── __init__.py      Registry, merges the `ai_document_readers` hook
│   ├── tools/               Tool calling
│   │   ├── builtin.py       Built-in handlers
│   │   └── __init__.py      Schema building, dispatch, approval gate
│   ├── engine.py            Model resolution, retries, failover, metrics
│   ├── chunking.py          Structure-aware chunker
│   ├── vector.py            Base64 float32 vectors, cosine similarity, ranking
│   ├── knowledge.py         Indexing and hybrid retrieval
│   ├── intelligence.py      Summarise, classify, extract, compare
│   ├── patterns.py          High-precision pattern entities (pure layer)
│   ├── ingestion.py         Unified document pipeline
│   ├── settings.py          Shared settings helpers (threshold normalisation)
│   ├── agent.py             Agent runtime: prompt → retrieve → tools → answer
│   ├── pipeline.py          Pipeline execution engine
│   ├── automation.py        Event-driven rules
│   ├── monitoring.py        Health checks and model discovery
│   ├── governance.py        Quotas, capabilities, policies
│   ├── logging.py           Execution logs, redaction, audit trail
│   ├── learning_utils.py    Pure scoring / dedup / classification (no Frappe)
│   ├── learning.py          Learning Loop orchestration
│   └── exceptions.py        AIError hierarchy
│
├── api/                     Whitelisted endpoints (thin; logic lives in ai/)
│   ├── chat.py              Conversations and messaging
│   ├── knowledge.py         Upload, search, ask, summarise, extract
│   └── admin.py             Providers, models, dashboards, export/import
│
├── ai_core/                 Module: settings, providers, models, prompts, logs
├── ai_knowledge/            Module: knowledge bases, documents, chunks, schemas
├── ai_conversation/         Module: agents, tools, conversations, messages
├── ai_automation/           Module: pipelines, rules, tasks
├── ai_operations/           Module: health, audit, policies, usage
├── ai_learning/             Module: knowledge candidates, memories, skills
│
├── utils/                   network guard, permissions, jinja, file hooks
├── public/                  Desk SCSS bundles and File DocType extensions
├── tests/                   Unit and integration suites
├── install.py               Roles, defaults, seed records
├── tasks.py                 Scheduled jobs
└── hooks.py                 Framework wiring + extension points
```

The `ai/` package holds all business logic and never imports from `api/`.
DocType controllers hold validation and thin action methods that delegate into
`ai/`. This keeps the service layer testable without a web request and makes
every operation reachable from the Desk, the REST API, a background job or
another app's Python code.

---

## Data model

**AI Core** — the engine.
`AI Platform Settings` (Single, 7 tabs) is the one place a human configures
anything. `AI Provider` is a runtime endpoint; `AI Model` is a model on that
runtime, carrying its own generation parameters and rolling performance
metrics. `AI Prompt Template` holds reusable Jinja prompts.
`AI Execution Log` records every call.

**AI Knowledge** — the corpus.
`AI Knowledge Base` groups documents and owns the chunking and embedding
policy. `AI Document` is one source item in any format. `AI Document Chunk` is
a retrievable slice with its embedding stored as base64 float32.
`AI Extraction Schema` defines structured extraction targets.
`AI Pattern Entity` holds high-precision pattern matches (email, url,
phone, ip, hash, date, identifier, money, custom) extracted from a
document's already-stored content — an enhancement layer that never
touches the ingestion pipeline, keyed by
`(document, entity_type, normalized_value)` with denormalized
`knowledge_base` so it rides the same row-level permissions as chunks.
`AI Translation` stores one Arabic / English / Hebrew translation of a document
with its per-segment review state, and `AI Translation Glossary` holds the
trilingual terminology enforced while producing it.

**AI Conversation** — the interaction.
`AI Agent` binds a model, a system prompt, knowledge bases and tools into a
persona. `AI Conversation` and `AI Message` hold history, citations and token
accounting. `AI Tool` declares a callable capability; `AI Tool Invocation`
records every execution with its arguments, result and approval state.

**AI Automation** — the autonomy.
`AI Pipeline` is an ordered list of steps; `AI Pipeline Run` records each
execution step by step. `AI Automation Rule` binds a Frappe document event to
an AI action. `AI Task` is a unit of unattended work.

**AI Operations** — the oversight.
`AI Service Health Log`, `AI Audit Log`, `AI Resource Policy` and
`AI Usage Snapshot`.

---

## Request lifecycle: a grounded answer

```
User message
   │
   ├─ 1. get_agent()            resolve agent, check role access
   ├─ 2. retrieve()             embed query → cosine rank chunks
   │                            + keyword scan → fuse with RRF
   ├─ 3. build_system_prompt()  persona + grounding rules + numbered context
   ├─ 4. get_conversation_history()
   │
   ├─ 5. run_chat() ──────────► engine
   │                             ├─ resolve_model()
   │                             ├─ check_quota()          governance
   │                             ├─ start_execution_log()  redacted
   │                             ├─ provider.stream_chat() when Desk asked and
   │                             │   this is the final, tool-free completion
   │                             │   (tokens via frappe.publish_realtime)
   │                             ├─ else provider.chat()   HTTP to runtime
   │                             ├─ retry / failover on transient failure
   │                             ├─ finish_execution_log()
   │                             └─ update_model_metrics()
   │
   ├─ 6. tool loop              while the model returns tool calls:
   │                             ├─ check_capability("tools")
   │                             ├─ permission check as the *calling user*
   │                             ├─ approval gate for write tools
   │                             ├─ execute + record AI Tool Invocation
   │                             └─ feed the result back, re-run
   │
   └─ 7. persist                AI Message rows with citations and tokens
```

### Attaching a file and asking about it

A file attached in chat is ingested on a background worker, so a question
asked in the same breath would race the index. `send_message` accepts the
just-uploaded `documents` and `prepare_documents_for_turn` makes them usable
immediately: already-indexed records stay in the retrieval scope, extracted
text is injected as extra context, and a short wait is used only when nothing
readable exists. Interactive turns default to an unbounded budget so a local
model is not cut off; a positive **Max Turn Duration** remains available
behind a reverse proxy. See [`docs/FILE_TO_ANSWER.md`](FILE_TO_ANSWER.md) for
the full lifecycle.

---

## Design decisions

The controlled support-boundary decisions (database, encryption, Folder source,
reranker, model versions, translation output, and the v17 revision pin) live in
[`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md).

### Embeddings in DocTypes, not a vector database

Vectors are stored as base64-encoded float32 in a Long Text field on
`AI Document Chunk`, pre-normalised to unit length at write time. Similarity is
therefore a plain dot product, and ranking is one NumPy matrix multiply when
NumPy is importable, with a pure-Python fallback when it is not.

This costs throughput at larger scale but avoids a second vector service and
keeps rows under Frappe permissions. Standard Frappe site backups include the
DocType data; the application-level knowledge export is not yet a complete
selective restore path (OPS-04). Retrieval completeness and scale remain Phase
2 qualification work.

Vectors are stored normalised so retrieval never pays for a square root.

### Hybrid retrieval with reciprocal rank fusion

Pure vector search can be weak on part numbers, IDs, proper nouns and acronyms;
pure keyword search misses paraphrase. The current small-corpus path runs both
and fuses ranked lists, but RET-01 through RET-04 remain correctness blockers
for candidate completeness, mixed models, and per-KB policy:

```
score(chunk) = Σ 1 / (K + rank_in_list)      K = 60
```

RRF needs no score calibration between the two systems, which is exactly the
problem with naive weighted blending of a cosine similarity against a term
frequency.

### Map-reduce for long documents

Summarisation over content larger than the context window splits into windows,
summarises each, then reduces summaries. The budget uses the declared context
window. INT-03 remains open because final reduction can lose facts; whole-
document coverage must be measured before this is production-qualified.

### Tools run as the calling user

Tools are intended to execute as the calling user and write tools have an
approval gate. SEC-02 and SEC-03 remain open because generic count and field
selection do not yet have complete row/field parity. Until those close, this is
an architectural invariant under remediation rather than a proven guarantee.

### Redaction happens before storage

`AI Platform Settings` holds regular expressions applied before prompts and
responses are written to `AI Execution Log`. This reduces exposure but is not a
universal storage guarantee: SEC-07 tracks search telemetry that does not yet
use the same path, and the model/provider still receives original request text.

### Automation cannot recurse

The `*` doc_events hook fires on every document event on the site, so the
handler returns immediately for any DocType whose name starts with `AI `, and
during install, migrate and patch. Rules are also blocked at validation time
from targeting the platform's own DocTypes. The rule index itself is cached, so
the common case — no rule for this DocType — is a single cache read.

### The Learning Loop: knowledge is approved, then additive

Learned knowledge flows through candidates and approval rather than mutating a
model silently. Approved candidates can be promoted to `AI Memory` or
`AI Skill` and injected additively. LEARN-03 through LEARN-05 remain open:
semantic memory recall, skill relevance ranking, and lifecycle maintenance are
not yet complete. Recall/feedback failures are best-effort and should not break
an otherwise healthy chat turn. See
[`docs/LEARNING.md`](LEARNING.md).

### Mixed AI Document tree is not NestedSet

Frappe NestedSet / `is_tree` assumes one homogeneous DocType. The AI Document
tree mixes native `File` folders with `AI Document` nodes, so NestedSet cannot
own that projection. File stays the NestedSet authority for physical folders;
`ai/document_tree.py` is the mixed-type facade used by Desk Tree View.

### Graceful degradation

Several paths degrade explicitly when dependencies are absent: no NumPy uses
pure-Python vector math; no `pypdf` reports the parser dependency; no `croniter`
skips scheduled pipelines; embedding failures can leave chunks for backfill.
Equivalent-model provider failover is not complete (PROV-01), so runtime outage
must not be represented as automatic high availability.

---

## Background processing

| Job | Schedule | Purpose |
| --- | --- | --- |
| `health_check` | every 5 min (throttled to the configured interval) | Provider reachability |
| `run_scheduled_pipelines` | every 10 min | Start due cron pipelines |
| `process_pending_documents` | hourly (long) | Retry stuck documents, backfill embeddings |
| `sync_models` | daily (long) | Reconcile registered models with each runtime |
| `rollup_usage` | daily (long) | Build `AI Usage Snapshot` from execution logs |
| `backup_knowledge` | daily (long) | Export knowledge bases to private files |
| `cleanup_logs` | weekly (long) | Enforce retention windows |

Document processing and pipeline runs are enqueued with `deduplicate=True` and
a deterministic `job_id`, so double-clicking a button cannot start two runs of
the same work.
