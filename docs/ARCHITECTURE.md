# Architecture

## Principles

1. **Frappe-native throughout.** Every entity is a DocType, every query goes
   through the Frappe ORM, every permission check goes through Frappe's role
   and row-level permission system. There is no parallel data layer, no
   separate service to deploy, no external database.
2. **Local by default.** A network guard validates every outbound URL against
   loopback and RFC 1918 ranges before a request is made. Turning that off is
   a deliberate, logged configuration change.
3. **Autonomous operation.** Ingestion, chunking, embedding, indexing, model
   discovery, health monitoring, usage rollups and log retention all run
   unattended on background workers and the scheduler.
4. **Everything is traceable.** Each model call produces an `AI Execution Log`;
   each privileged action produces an `AI Audit Log`; each tool call produces
   an `AI Tool Invocation`; each answer carries the chunks that grounded it.
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
│   ├── ingestion.py         Unified document pipeline
│   ├── agent.py             Agent runtime: prompt → retrieve → tools → answer
│   ├── pipeline.py          Pipeline execution engine
│   ├── automation.py        Event-driven rules
│   ├── monitoring.py        Health checks and model discovery
│   ├── governance.py        Quotas, capabilities, policies
│   ├── logging.py           Execution logs, redaction, audit trail
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
│
├── utils/                   network guard, permissions, jinja, file hooks
├── public/                  Desk assets (SCSS bundles, form scripts)
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
   │                             ├─ provider.chat()        HTTP to runtime
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

---

## Design decisions

### Embeddings in DocTypes, not a vector database

Vectors are stored as base64-encoded float32 in a Long Text field on
`AI Document Chunk`, pre-normalised to unit length at write time. Similarity is
therefore a plain dot product, and ranking is one NumPy matrix multiply when
NumPy is importable, with a pure-Python fallback when it is not.

This costs some throughput at very large scale but buys properties that matter
more for enterprise deployment: nothing extra to install, backup and restore
work through standard `bench` commands, permissions are enforced by the same
engine as everything else, and the app installs on an air-gapped workstation
with no infrastructure beyond Frappe itself.

Vectors are stored normalised so retrieval never pays for a square root.

### Hybrid retrieval with reciprocal rank fusion

Pure vector search is weak on the content enterprises actually store — part
numbers, invoice IDs, proper nouns, acronyms — because those tokens carry
little semantic signal. Pure keyword search misses paraphrase. The platform
runs both and fuses the ranked lists:

```
score(chunk) = Σ 1 / (K + rank_in_list)      K = 60
```

RRF needs no score calibration between the two systems, which is exactly the
problem with naive weighted blending of a cosine similarity against a term
frequency.

### Map-reduce for long documents

Summarisation over content larger than the context window splits into windows,
summarises each, then summarises the summaries. The budget is derived from the
model's declared context window rather than hard-coded, so a 128k model does
the whole document in one call and an 8k model degrades gracefully.

### Tools run as the calling user

A tool never executes with elevated privileges. `frappe.has_permission` and
`doc.check_permission` are called with the session user, so the AI can only
ever see and change what that person could have seen and changed by hand.
Write-capable tools additionally pass through an approval gate that can be
required globally or per tool.

### Redaction happens before storage

`AI Platform Settings` holds a list of regular expressions. Prompts and
responses are redacted before they are written to `AI Execution Log`, so
sensitive values never land in the log table even though the model saw them.
Compiled patterns are cached and invalidated on settings save.

### Automation cannot recurse

The `*` doc_events hook fires on every document event on the site, so the
handler returns immediately for any DocType whose name starts with `AI `, and
during install, migrate and patch. Rules are also blocked at validation time
from targeting the platform's own DocTypes. The rule index itself is cached, so
the common case — no rule for this DocType — is a single cache read.

### Graceful degradation

The platform stays useful when parts are missing. No NumPy: pure-Python vector
maths. No `pypdf`: PDFs report a clear install command instead of crashing the
pipeline. Runtime offline: failover to the next provider by priority, and the
readiness checklist explains exactly what to fix. No `croniter`: scheduled
pipelines are skipped rather than raising. Embedding failures leave chunks
unembedded and a scheduled job backfills them later.

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
