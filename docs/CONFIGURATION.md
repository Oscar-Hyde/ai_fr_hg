# Configuration

All configuration lives in **AI Platform Settings** (`/app/ai-platform-settings`),
a Single DocType organised into seven tabs. This is the only place a human
needs to intervene — everything else is automatic.

---

## General

| Setting | Default | Effect |
| --- | --- | --- |
| Enable AI Platform | on | Master switch. When off, every model call raises immediately. |
| Strict Local Only | **on** | Refuses provider and document URLs outside loopback / RFC 1918. Turn off only if you deliberately want a remote endpoint. |
| Additional Allowed Hosts | empty | Hostnames exempt from the local-only guard, one per line. |
| Default System Prompt | seeded | Used by agents that do not define their own. |
| Request Timeout | 120 s | Per-request HTTP timeout. Raise it for large models on CPU. |
| Max Turn Duration | 90 s | Total budget for one interactive chat turn, across retries, failover and tool calls. Set 0 to disable. |
| Max Retries | 2 | Retry attempts for transient failures before failover. |
| Enable Failover | on | Try the next provider by priority when one is unreachable. |
| Streaming Enabled | on | Allow token streaming where the runtime supports it. |

### On Max Turn Duration

`Request Timeout` bounds a *single* HTTP call. One chat turn can make many:
each tool iteration is another model call, each call may be retried, and each
retry may be tried against every enabled provider. Multiplied out, the worst
case is far longer than any reverse proxy will hold a connection open, and the
user sees a bare `504 Gateway Time-out` — no answer, and no error to explain it.

`Max Turn Duration` is the budget for the whole turn. Every layer beneath it
checks the remaining time before starting more work: socket timeouts are
clamped to what is left, retries and failover stop when they cannot finish, and
tool calling gives way to a final answer as the deadline approaches. If the
budget does run out, the turn still saves a reply explaining what happened.

Keep it comfortably below your proxy's timeout (nginx `proxy_read_timeout`
defaults to 60 s). Set it to `0` for unbounded turns — appropriate only when
nothing with a timeout sits in front of the site. Background jobs, pipelines
and scheduled tasks are never budgeted, since no client is waiting on them.

### On Strict Local Only

The guard resolves the hostname and checks every resulting IP against
loopback, link-local and private ranges. Because it resolves first, a public
hostname pointed at a private IP still passes, and `localhost` aliases behave
correctly. Resolution is cached; the cache clears when settings or a provider
are saved.

This is what makes the platform safe to run in an air-gapped or regulated
environment: it is not a promise in documentation, it is enforced in code on
every request.

---

## Models

| Setting | Purpose |
| --- | --- |
| Default Chat Model | Used when no model is specified. Must be a Chat or Vision model. |
| Default Embedding Model | **Required for semantic search.** Must be an Embedding model. |
| Default Vision Model | Used for image documents when OCR is unavailable. |
| Default Temperature / Top P / Max Tokens | Fallbacks when a model defines none. |

Defaults are type-checked on save: selecting an embedding model as the default
chat model is rejected rather than failing later at request time.

**Changing the embedding model invalidates your index.** Vectors from different
models are not comparable. The platform detects the change, marks affected
knowledge bases `Stale` and prompts you to re-index. Do that before relying on
search again.

---

## Knowledge

| Setting | Default | Notes |
| --- | --- | --- |
| Default Chunk Size | 1200 chars | Larger keeps more context per passage; smaller improves precision. |
| Default Chunk Overlap | 150 chars | Prevents facts being orphaned at a boundary. Must be smaller than chunk size. |
| Default Top K | 6 | Passages retrieved per query. |
| Similarity Threshold | 0.25 | Minimum cosine score. Raise it if answers cite loosely related passages. |
| Enable Hybrid Search | on | Fuse dense and keyword ranking. Recommended. |
| Max Context Characters | 12000 | Ceiling on retrieved context injected into a prompt. |
| Auto Process Documents | on | Ingest and index on upload with no further action. |
| Auto Embed on Ingest | on | Embed immediately rather than waiting for the hourly backfill. |
| Max Document Size (MB) | 50 | Rejected above this, before any parsing work. |
| OCR Enabled | off | Requires `pytesseract` and the Tesseract binary. |
| Processing Queue | long | Which Frappe queue handles ingestion. |

Knowledge bases override chunk size, overlap, top K, threshold and embedding
model individually, so a base of short policy notes and a base of long
contracts can each be tuned appropriately.

### Tuning guidance

- Answers miss information that is definitely in a document → raise Top K, or
  lower the similarity threshold.
- Answers cite irrelevant passages → raise the similarity threshold.
- Facts get split across passages → raise chunk overlap.
- Retrieval is slow on a very large corpus → lower Top K and install NumPy.

---

## AI Document organization

The native AI Document Tree has no separate hierarchy setting: canonical
placement is always `AI Document.folder` → native `File` folder, with `Home` as
the root. This avoids a second folder subsystem and keeps ordinary File forms,
uploads, processing, search, and retention consistent.

Operational constants are currently service defaults rather than mutable site
settings:

| Behavior | Current value | Operational effect |
| --- | --- | --- |
| Lazy page size | 100 | One direct child page; clients may request 10–250. |
| Maximum page size | 250 | Prevents an unbounded Tree View request. |
| Search candidate scan | 1,000 per request | Hidden-parent-safe global search truncates fail-closed at the bound. |
| Background threshold | 100 affected rows | Recursive/bulk work above this defaults to the long queue. |
| Bulk request maximum | 500 selected node IDs | Nested/duplicate selections are pruned only after authorization. |
| Worker timeout | 7,200 seconds | Queued copy/move/delete and bulk jobs use the long queue. |
| Organization name | 140 characters | Normalized parent-scoped display identity. |

### Permissions

- Assign ordinary `AI User`, `AI Manager`, and Frappe File/Knowledge Base
  permissions; there is no tree-specific superuser role.
- A document appears only when the user can read both the AI Document and its
  parent File folder.
- Create requires AI Document/File create permission and destination-folder
  write permission.
- Rename, move, copy, delete, and recursive operations check source,
  destination, physical File, and every affected descendant server-side.
- Folder metadata is copied only through the caller's `AI Folder Settings`
  read permission and after the complete source folder set is readable.
- Queued workers restore the initiating user and re-check authority. Do not run
  organization jobs as Administrator to work around a missing grant.

Client capability flags affect menus only. They are not an authorization cache;
forged RPC calls receive the same server checks.

### Workers and deployment

Keep at least one long-queue worker available when repositories may contain more
than 100 affected rows:

```bash
bench worker --queue long
```

Run schema changes through normal migration and rebuild assets after upgrading:

```bash
bench --site your-site.local migrate
bench build --app ai_fr_hg
bench restart
```

Patch `v0_0_9_ai_document_tree_organization` backfills placement, normalized
location identity, stable File links when unambiguous, and the scoped unique
constraint. After migration, inspect legacy File-backed documents whose
`source_file_record` is still empty. Multiple Files may intentionally share one
URL/hash; attach or select the exact File identity instead of deleting or
merging duplicates.

Before production rollout, run the Bench integration suite and verify assets,
workers, direct File moves/uploads, recursive jobs, MariaDB/PostgreSQL behavior,
and the existing processing/index/retrieval regressions. The implementation
environment's latest pure run passed 23 folder and 28 document-tree tests, but
it did not provide live Frappe/browser/cross-database validation.

See [AI Document Tree](DOCUMENT_TREE.md) for the complete behavior and release
checklist.

---

## Governance

| Setting | Purpose |
| --- | --- |
| Max Requests Per User Per Hour | Global rate limit. 0 disables. |
| Max Tokens Per User Per Day | Global token budget. 0 disables. |
| Require Tool Approval | Hold every write-capable tool call for human approval. |
| Log Prompts / Log Responses | Store prompt and response text on execution logs. |
| Redaction Patterns | Regular expressions, one per line, masked before storage. |

Finer control comes from **AI Resource Policy** records, which apply to a role
or a single user:

- Requests per hour, tokens per day, documents per day, concurrent requests
- Capability flags: tools, document upload, pipeline execution, model management

Resolution order: a policy naming the user beats a policy naming one of their
roles; among equals the lowest `priority` wins; if nothing matches, the global
settings apply. Administrator bypasses all checks.

Installation seeds a `Standard AI User` policy: 200 requests/hour,
500k tokens/day, 100 documents/day, tools and uploads allowed, pipelines and
model management denied.

### Redaction

Patterns are applied to prompts and responses **before** they are written to
`AI Execution Log`. The model still receives the original text; only the stored
record is masked. Seeded defaults cover card numbers and email addresses.

Useful additions:

```
\b\d{3}-\d{2}-\d{4}\b                       US SSN
\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b   IBAN
(?i)\b(api[_-]?key|token|secret)\s*[:=]\s*\S+        Credentials
```

Invalid regular expressions are rejected on save.

---

## Monitoring

| Setting | Default |
| --- | --- |
| Health Check Enabled | on |
| Health Check Interval | 15 min |
| Alert on Provider Offline | on |
| Alert Recipients | empty — one email per line |

A provider is marked Offline after three consecutive failures, which prevents
one slow response from flapping the status. Transitions are written to the
audit log and, when configured, emailed.

---

## Retention

| Log | Default | Notes |
| --- | --- | --- |
| AI Execution Log | 90 days | The highest-volume table. |
| AI Service Health Log | 30 days | One row per provider per check. |
| AI Audit Log | 365 days | Keep long for compliance. |
| AI Search Query | 30 days | Fixed; diagnostic only. |

Cleanup runs weekly. Purge manually from the Operations dashboard when needed.

---

## Backup

Auto Backup exports each enabled knowledge base to a private JSON file daily.
Embeddings are excluded by default, since they can be regenerated and dominate
the file size.

For full disaster recovery use standard Frappe backups — all platform data
lives in ordinary DocTypes and is included automatically:

```bash
bench --site your-site.local backup --with-files
```

Export and import individual knowledge bases from the knowledge base form, or
via `ai_fr_hg.api.admin.export_knowledge_base`.

---

## Deployment topologies

### Single workstation

Ollama and Frappe on the same machine. Point the provider at
`http://localhost:11434`. Expect 5–15 s responses for a 7B model on CPU;
a GPU brings that under 2 s. Use `phi3:mini` on constrained hardware.

### Shared internal server

Ollama on a GPU host, Frappe elsewhere on the LAN. Point the provider at
`http://10.0.0.x:11434` — the local-only guard permits RFC 1918 addresses.
Set `OLLAMA_HOST=0.0.0.0` so the runtime accepts LAN connections, and raise
Max Concurrent Requests on the provider to match the GPU's capacity.

### High availability

Register several providers with ascending `priority`. With failover enabled the
engine tries the next reachable provider automatically, and the health monitor
takes failed endpoints out of rotation.

### Air-gapped

Keep Strict Local Only on. Transfer models offline:

```bash
# On a connected machine
ollama pull llama3.1:8b
ollama save llama3.1:8b > llama31-8b.tar

# On the isolated machine
ollama load < llama31-8b.tar
```

Knowledge bases move the same way, as exported JSON.

---

## Performance

- **Install NumPy.** Ranking becomes one matrix multiply instead of a Python
  loop. This is the single largest retrieval win.
- **Keep `keep_alive` generous** on frequently used models so the runtime does
  not unload weights between requests.
- **Size `num_ctx` honestly.** Oversized context windows waste memory and slow
  every request.
- **Watch the Operations dashboard.** Average latency above ~10 s usually means
  the model is too large for the hardware, or is being reloaded each call.
- **Scale workers** with `bench worker --queue long` for heavy ingestion and
  recursive AI Document tree copy/move/delete jobs.
- **Keep tree operations lazy.** Use `Load more…` and Expand Loaded; do not add
  client code that recursively expands or searches the complete hierarchy.
- **Preserve indexes.** `AI Document.folder`, `organization_name_key`, and
  `source_file_record` are indexed; always migrate before load testing.
