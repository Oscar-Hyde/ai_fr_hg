# Configuration

Application configuration lives in **AI Platform Settings**
(`/app/ai-platform-settings`), a Single DocType organised into seven tabs.
Supported background paths automate routine work, but approvals, reviews,
failures, and the limitations below still require users or operators.

---

## General

| Setting | Default | Effect |
| --- | --- | --- |
| Enable AI Platform | on | Master switch. When off, every model call raises immediately. |
| Strict Local Only | **on** | Refuses provider and document URLs outside loopback / RFC 1918. Turn off only if you deliberately want a remote endpoint. |
| Additional Allowed Hosts | empty | Hostnames exempt from the local-only guard, one per line. |
| Default System Prompt | seeded | Used by agents that do not define their own. |
| Request Timeout | 120 s | Per-request HTTP timeout. Raise it for large models on CPU. |
| Max Turn Duration | **0** (unlimited) | Optional budget for one interactive chat turn, across retries, failover and tool calls. Leave at 0 on a local bench so a slow first token is not cut off. Set a positive value only when a reverse proxy would otherwise return 504. |
| Max Retries | 2 | Retry attempts for transient failures before failover. |
| Enable Failover | on | Try the next provider by priority when one is unreachable. |
| Streaming Enabled | on | Desk chat streams tokens over Frappe realtime when the provider supports it. Off, or a non-streaming runtime, uses the same blocking `send_message` path as before. |

### On Max Turn Duration

`Request Timeout` bounds a *single* HTTP call. One chat turn can make many:
each tool iteration is another model call, each call may be retried, and each
retry may be tried against every enabled provider. Multiplied out, the worst
case is far longer than any reverse proxy will hold a connection open, and the
user sees a bare `504 Gateway Time-out` — no answer, and no error to explain it.

`Max Turn Duration` is an *optional* budget for the whole turn. The default is
`0`: the platform waits for the local model and does not invent a second
deadline on top of **Request Timeout**. Every layer beneath a positive budget
checks the remaining time before starting more work: socket timeouts are
clamped to what is left, retries and failover stop when they cannot finish, and
tool calling gives way to a final answer as the deadline approaches. If the
budget does run out, the turn still saves a reply explaining what happened.

On a local bench (`site1.local`, no nginx in front) leave this at `0`. Only set
a positive value when something with a timeout sits in front of the site, and
keep it comfortably below that proxy timeout (nginx `proxy_read_timeout`
defaults to 60 s). Background jobs, pipelines and scheduled tasks are never
budgeted, since no client is waiting on them.

### On Strict Local Only

The guard resolves the hostname and checks every resulting IP against
loopback, link-local and private ranges. Because it resolves first, a public
hostname pointed at a private IP still passes, and `localhost` aliases behave
correctly. Resolution is cached; the cache clears when settings or a provider
are saved.

This is an application-layer guard, not the network security boundary. SEC-04
tracks proxy bypass, DNS rebinding, redirects, IPv4/IPv6, and connection-level
validation. Use host firewall and egress policy for an air-gapped or regulated
deployment until that finding closes.

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
| Similarity Threshold | 0.25 | Minimum cosine score from 0 to 1. Values such as `25` are stored as `0.25`. Raise it if answers cite loosely related passages. |
| Enable Hybrid Search | on | Fuse dense and keyword ranking. Recommended. |
| Max Context Characters | 12000 | Ceiling on retrieved context injected into a prompt. |
| Auto Process Documents | on | Ingest and index on upload with no further action. |
| Auto Embed on Ingest | on | Embed immediately rather than waiting for the hourly backfill. |
| Max Document Size (MB) | 50 | Rejected above this, before any parsing work. |
| OCR Enabled | **off** | Image-file OCR fallback only; requires `pytesseract` and Tesseract. It does not OCR scanned PDFs. |
| Processing Queue | long | Which Frappe queue handles ingestion. |
| Auto Pattern Scan | **off** | Hourly background extraction of high-precision pattern entities (emails, URLs, phones, IPs, hashes, dates, identifiers, money) from indexed documents into `AI Pattern Entity` rows. Reads only already-extracted content; manual **Extract Patterns** on the AI Document form works regardless. |
| Max Pattern Entities | 500 | Upper bound of distinct entities stored per document. The scan samples at most 1 MB of extracted text (head and tail) with linear-time guards. |

Knowledge-base chunk size, overlap, and embedding model affect ingestion.
Although top K and threshold fields also exist, their complete per-KB retrieval
precedence is not yet enforced (RET-04); treat platform/search-request values as
the effective retrieval controls until Phase 2 closes that finding.

### Default agent retrieval

The seeded **General Assistant** has **Use Knowledge** off. Small talk therefore
does not pay an embedding round-trip on a site that may have no documents yet.
Grounded answers still happen when:

- the user selects one or more knowledge chips in the Assistant,
- a file is attached (the next send is scoped to that upload, including
  already-indexed files),
- the agent calls the `search_knowledge_base` tool,
- or an administrator turns **Use Knowledge** on for that agent.

This is intentional, not a missing retrieval path.

Extracted text is labelled with its written language (`AI Document.language`,
ISO 639-1). English, Arabic and Hebrew are first-class, including mixed files
(`en,ar,he`). The label is written on ingest, refreshed by patch `v0_0_12`,
and injected into the prompt as `language=English + Arabic` so a small local
model such as `qwen2.5:0.5b` can name every language in the file.

### Tuning guidance

- Answers miss information that is definitely in a document → raise Top K, or
  lower the similarity threshold.
- Answers cite irrelevant passages → raise the similarity threshold.
- Facts get split across passages → raise chunk overlap.
- Retrieval is slow on a very large corpus → lower Top K and install NumPy.

---

## Translation

Arabic / English / Hebrew translation. Full guide:
[`TRANSLATION.md`](TRANSLATION.md).

| Setting | Default | Notes |
| --- | --- | --- |
| Enable Translation | on | Master switch for translation, its tool and its pipeline step. |
| Default Target Language | `en` | Used when a caller does not name one. |
| Default Translation Model | empty | Falls back to the default chat model. Must be a Chat or Vision model. |
| Default Glossary | empty | Terminology applied when a translation names none. |
| Segment Size (characters) | 1800 | How much source text is translated as one unit. Minimum 200. Lower it for small context windows. |
| Segments per Model Call | 6 | Batch size. Larger batches mean fewer round trips but a longer prompt. |
| Run Quality Checks | on | Score every segment locally: placeholders, script, length, glossary, refusals, repetition. |
| Repair Flagged Segments | on | One stricter retry per flagged segment, kept only when it scores better. |
| Use Translation Memory | on | **Do not enable in production yet.** SEC-01/TRN-01 track mandatory KB scope and policy-safe identity on every caller. |
| Back-Translation Samples | 0 | Verify this many segments by translating them back and comparing embeddings. Costs extra model calls. |
| Index Translations as Documents | off | Default for new translations: store the result as its own searchable document. |

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

- Requests per hour, tokens per day, and documents per day
- Capability flags: tools, document upload, pipeline execution, model management
- A declared concurrent-request value that is **not yet enforced** (GOV-01)

Resolution order: a policy naming the user beats a policy naming one of their
roles; among equals the lowest `priority` wins; if nothing matches, the global
settings apply. Administrator bypasses current checks. Quota reservation and
distributed concurrency/rate enforcement remain Phase 6 work.

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
That JSON is a content export, not a complete restore artifact: embeddings,
metadata, policies, and several linked components are incomplete (OPS-04).

For site-level disaster recovery use standard Frappe database and file backups:

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
Set `OLLAMA_HOST=0.0.0.0` so the runtime accepts LAN connections. The provider
Max Concurrent Requests field is not enforced until GOV-01 closes; enforce a
capacity limit at the runtime/proxy meanwhile.

### High availability

Registering providers with priorities enables retry/failover scaffolding, but
PROV-01 remains open: the engine does not yet guarantee an equivalent model on
the target provider. Do not rely on this as high availability until equivalent
model selection and real failover tests pass.

### Air-gapped

Keep Strict Local Only on. Transfer models offline:

```bash
# On a connected machine
ollama pull llama3.1:8b
ollama save llama3.1:8b > llama31-8b.tar

# On the isolated machine
ollama load < llama31-8b.tar
```

Current knowledge JSON can move extracted text for limited interchange, but it
is not a complete backup/restore path. Use verified Frappe backups until OPS-04
closes.

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
- **Scale workers** with `bench worker --queue long` for heavy ingestion.
