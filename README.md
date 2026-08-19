## AI Fr HG

A complete, fully local enterprise AI platform, built as a native Frappe app.

Every model runs on your own hardware. No prompt, document or embedding ever
leaves your network — the platform ships with a strict local-only guard that
refuses to talk to non-private addresses unless you explicitly disable it.

The design goal is autonomy: **everything is driven by AI with no human
intervention except configuration.** Documents ingest, chunk, embed and index
themselves; models are discovered and classified automatically; pipelines and
event rules run unattended on background workers.

---

### What you get

| Capability | Summary |
| --- | --- |
| **Local AI engine** | Ollama first-class, plus llama.cpp, vLLM, LM Studio, Text Generation WebUI and any OpenAI-compatible runtime. Automatic model discovery, health monitoring, failover and performance tracking. |
| **Document intelligence** | 37 registered file extensions through one pipeline: extract → chunk → embed → index. Text, office (including OpenDocument), and images. Summarisation, classification, structured extraction and document comparison. |
| **Knowledge & search** | Hybrid retrieval (dense vectors + keyword, fused with RRF) computed entirely in Python. Embeddings live in DocTypes — no external vector database. |
| **Conversational AI** | Multi-session chat with retrieval grounding, inline citations, tool calling and a full audit trail of every invocation. |
| **Automation** | Declarative pipelines and event-driven rules that bind any Frappe document event to an AI action. |
| **Governance** | Per-role and per-user quotas, capability gates, prompt redaction, approval gates for write actions, and a complete audit log. |
| **Extensibility** | Three hooks — `ai_providers`, `ai_document_readers`, `ai_tools` — let any app add runtimes, formats and tools without touching this one. |

---

### Requirements

- Frappe Framework v17
- Python 3.14+ (matching Frappe v17 and `pyproject.toml`)
- A local AI runtime. [Ollama](https://ollama.com) is recommended.

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
Three-panel interface: conversations, messages, and a context inspector showing
the exact passages behind each answer. Attach a document mid-conversation and
it is indexed and searchable within seconds.

**Search** — `/app/knowledge-explorer`
Hybrid, semantic or keyword search across your documents, with an
"Answer with AI" toggle for grounded question answering.

**Operate** — `/app/ai-operations`
Live provider health, token usage, latency, failed executions, queue depth and
pending tool approvals.

**Manage models** — `/app/ai-model-manager`
Install, test, enable and set defaults per provider, with curated suggestions
for a fresh install.

---

### Documentation

| Document | Contents |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module layout, data model, request lifecycle, design decisions |
| [`docs/FILE_TO_ANSWER.md`](docs/FILE_TO_ANSWER.md) | The complete attach → ingest → index → retrieve → cite lifecycle, dev & production process |
| [`docs/LEARNING.md`](docs/LEARNING.md) | The Learning Loop: teach → validate → approve → memory/skill → recall → observe |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Every setting, quotas, redaction, retention, deployment topologies |
| [`docs/EXTENDING.md`](docs/EXTENDING.md) | Writing custom providers, readers, tools and pipeline steps |
| [`docs/API.md`](docs/API.md) | Whitelisted REST endpoints with request and response shapes |

---

### Air-gapped deployment

The platform is built for isolated networks. Set **Strict Local Only** in
AI Platform Settings (on by default) and every outbound URL is validated
against loopback and RFC 1918 ranges before a request is made. Model files can
be transferred offline with `ollama save` / `ollama load`, and knowledge bases
export and import as self-contained JSON.

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
