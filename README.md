## AI Fr HG

A feature-rich, local-first enterprise AI platform, built as a native Frappe app.

> **Project status:** technical beta, not production-ready. Phases 0–3 closed
> isolation, API bounds, connection-level provider transport, retrieval
> correctness, and conversation/turn contracts on the pinned Frappe v17 bench.
> Ingestion/translation cancellation, automation state machines, governance
> enforcement, browser E2E, load, upgrade, and restore qualification remain.
> See the [audited project status](docs/PROJECT_STATUS.md), [controlled gap register](docs/GAP_REGISTER.md),
> and [completion roadmap](docs/DEVELOPMENT_PLAN.md) before any deployment.

Models are designed to run on your own hardware. The platform ships with a
strict local-only guard that refuses provider URLs outside private networks
unless an administrator explicitly allows them. Provider connections ignore
environment proxies, pin the dial to the validated address, refuse redirects,
and re-validate the peer socket. Deploy with host firewall and egress controls
as the actual network boundary.

The design goal is supervised automation. Supported document, model-discovery,
pipeline, and event-rule paths can run on Frappe background workers, while
write tools, reviews, approvals, failures, and unsupported inputs remain
explicit human or operator concerns.

---

### What you get

| Capability | Summary |
| --- | --- |
| **Local AI engine** | Ollama first-class, plus configured OpenAI-compatible local runtimes. Model discovery, health records, retry/failover scaffolding, and performance records exist; capability, rate, concurrency, and equivalent-model failover hardening remains. |
| **Document intelligence** | 36 registered extensions through one pipeline: extract → chunk → embed → index. Text-layer PDFs, Office/OpenDocument files, RFC `.eml`, text/code, and images (vision or optional image OCR). Scanned-PDF OCR and Outlook `.msg` are not supported. Extraction returns JSON; it does not create target DocType records. |
| **Translation** | Arabic ⇄ English ⇄ Hebrew text translation. Segmentation preserves extracted-text structure, not the original PDF/Office/image binary. Translation memory requires an authorized knowledge-base scope and includes policy identity. Progress, cancellation, glossary/KB parity, and worker restoration remain Phase 4. |
| **Knowledge & search** | Hybrid retrieval (dense vectors + keyword, fused with RRF) scans every eligible chunk, groups mixed embedding models, and applies per-KB top-k, threshold and weight. A configurable brute-force ceiling flags large corpora as degraded without dropping results. Reranking is not implemented. |
| **Conversational AI** | Multi-session chat with retrieval grounding, inline/footnote citations, tool calling, latest-N history, turn identity, cooperative cancel/reconnect, and conversation rename/pin/archive/export. Browser E2E remains Phase 7. |
| **Automation** | Main-path declarative pipelines and event rules on Frappe workers. Delete snapshots, atomic schedule claims, resumable approvals, and several task/trigger contracts remain open. |
| **Governance** | Quota checks, capability gates, prompt redaction, write-tool approvals, and audit records exist. Distributed concurrency/rate enforcement, quota reservations, and complete trace linkage remain open. |
| **Extensibility** | Three hooks — `ai_providers`, `ai_document_readers`, `ai_tools` — let any app add runtimes, formats and tools without touching this one. |

---

### Requirements

- Frappe Framework `17.0.0-dev` at the revision recorded in
  [`ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md). Upstream has not
  published stable v17 yet; this is a pre-release development target.
- Python `>=3.14,<3.15`, Node 24, and MariaDB 11.8
- A local AI runtime. [Ollama](https://ollama.com) is recommended.

PostgreSQL is not currently supported by this application. Stable Frappe v17
support must be requalified when upstream publishes a stable branch/tag.

---

### Installation

```bash
# 1. Get the app
cd ~/frappe-bench
bench get-app https://github.com/Oscar-Hyde/ai_fr_hg

# 2. Install it onto a site
bench --site your-site.local install-app ai_fr_hg

# 3. Optional: document format support (PDF, Word, Excel, PowerPoint, HTML)
bench pip install --editable "./apps/ai_fr_hg[documents]"

# 4. Make sure background workers and the scheduler are running
bench --site your-site.local enable-scheduler
```

Then set up the runtime. Leave `ollama serve` running and run the pull
commands from another shell:

```bash
ollama serve                        # terminal 1: start the engine

ollama pull llama3.1:8b             # terminal 2: a chat model
ollama pull nomic-embed-text        # an embedding model (required for search)
```

If Ollama was unpacked into the bench instead of installed system-wide, its
binary will not automatically be on `PATH`. Point the shell at the portable
installation first (put these exports in `~/.profile` to persist them):

```bash
export OLLAMA_ROOT="$HOME/frappe-bench/services/ollama"
export PATH="$OLLAMA_ROOT/bin:$PATH"
export OLLAMA_MODELS="$OLLAMA_ROOT/models"
export LD_LIBRARY_PATH="$OLLAMA_ROOT/lib/ollama${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

ollama --version
ollama list
```

Finally, open **`/app/ai-control-center`** and click **Test All Providers**,
then **Discover Models**. The readiness checklist on that page tells you
exactly what is still missing.

Installation seeds a working configuration automatically: the `AI Manager`,
`AI User` and `AI Auditor` roles, a `Local Ollama` provider, a
`General Knowledge` knowledge base, a `General Assistant` agent, six built-in
tools, two extraction schemas and a default resource policy.

#### Recovering an interrupted installation

Frappe can add the app to the site's installed-app list before `after_install`
has finished. If installation was interrupted and a retry says "already
installed", synchronise the schema and rerun the idempotent seed routine:

```bash
bench --site your-site.local migrate
bench --site your-site.local execute ai_fr_hg.install.after_install
bench build --app ai_fr_hg
bench restart
```

---

### Using it

**Chat** — `/app/ai-assistant`
Three-panel technical-beta interface for conversations, messages, and cited
context. Attached files are submitted to Frappe ingestion workers; durable
progress/cancel/reconnect and attachment-identity hardening remain open.

**Search** — `/app/knowledge-explorer`
Hybrid, semantic or keyword search with folder and entity filters, pagination,
and (for managers) retrieval diagnostics. Degraded mode is shown when semantic
embedding fails or the corpus exceeds the published brute-force envelope.

**Operate** — `/app/ai-operations`
Current provider, usage, latency, failure, queue, and approval summaries. SLO
charts, job drill-down, stale reconciliation, and timer cleanup remain open.

**Manage models** — `/app/ai-model-manager`
Install, test, enable and set defaults per provider, with curated suggestions
for a fresh install.

---

### Documentation

| Document | Contents |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module layout, data model, request lifecycle, design boundaries |
| [`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md) | Supported database/runtime and implement-or-remove decisions |
| [`docs/FILE_TO_ANSWER.md`](docs/FILE_TO_ANSWER.md) | Current attach → ingest → index → retrieve → cite main path and limitations |
| [`docs/TRANSLATION.md`](docs/TRANSLATION.md) | Arabic / English / Hebrew translation: pipeline, quality gate, glossaries, memory, review |
| [`docs/LEARNING.md`](docs/LEARNING.md) | The Learning Loop: teach → validate → approve → memory/skill → recall → observe |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Every setting, quotas, redaction, retention, deployment topologies |
| [`docs/EXTENDING.md`](docs/EXTENDING.md) | Writing custom providers, readers, tools and pipeline steps |
| [`docs/API.md`](docs/API.md) | Whitelisted REST endpoints with request and response shapes |
| [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) | Current audited implementation status and known blockers |
| [`docs/GAP_REGISTER.md`](docs/GAP_REGISTER.md) | Controlled owner/phase/status register for all 79 audit findings |
| [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md) | Full frontend/backend completion plan, priorities, phases and acceptance criteria |

---

### Air-gapped deployment

The platform is intended for isolated networks. **Strict Local Only** validates
the hostname once (all resolved addresses must be private unless the host is
allowlisted) and the provider transport ignores environment proxies, dials
only the validated address, refuses redirects, and re-validates the peer
socket before trusting a response. Use host firewall/egress controls as the
actual network boundary. Model files can be transferred through
runtime-supported offline procedures. Current knowledge JSON export/import is
not a complete backup/restore mechanism (OPS-04).

---

### Development

```bash
cd apps/ai_fr_hg
pre-commit install                              # ruff, prettier, eslint
bench --site your-site.local run-tests --app ai_fr_hg
```

The test suite separates fast pure-logic tests (`tests/test_units.py` — no
database, no runtime) from integration tests colocated as
`<module>/doctype/<doctype>/test_<doctype>.py` beside each owning DocType.
Those tests exercise DocTypes and their canonical service paths with the model
runtime stubbed, so CI never needs a GPU or a running Ollama.

---

### License

MIT
