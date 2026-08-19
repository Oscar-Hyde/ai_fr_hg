# AI Fr HG — complete project status

Snapshot of **every** module, service, RPC, DocType, page, job and known
gap as of **2026-08-19**. This is an inventory of implementation status, not
a how-to. For how things work see the other docs listed at the end.

| Field | Value |
| --- | --- |
| App version | `0.0.1` (`ai_fr_hg/__init__.py`) |
| Target | Frappe v17, Python 3.14+ |
| Branch | `arena/01a01846-ai-fr-hg` (remediation pass after `7316b4c`) |
| Base | `main` @ `e960ed2` (PR #22 merged) |
| Pull request | [#23](https://github.com/Oscar-Hyde/ai_fr_hg/pull/23) — **open, mergeable** |
| Site (local) | `site1.local` (Sofia) |
| Default runtime | Ollama at `http://127.0.0.1:11434` / `http://localhost:11434` |
| Working tree | language detection + attach→ask retrieve |

**PR #23 commits (this session's work):**

1. `fe773c4` — unbounded local chat, faster attach→ask, % similarity, File preview guard
2. `1d4ad5f` — local `relative_time` so Knowledge Explorer does not crash on a stale `frappe.ai`
3. `885e58e` — AI Document Tree View JS owned by the DocType (`doctype_tree_js`)

**CI on #23 (diagnosed from check annotations):** Server failed on Frappe v17 `get_list` COUNT syntax, folder-unit mocks against a real `frappe`, and a nested-pipeline assertion string. Those are fixed in this pass. Confirm green checks after push.

---

## Status legend

| Tag | Meaning |
| --- | --- |
| **READY** | Implemented, wired, and covered by tests or recent Desk fixes |
| **IMPLEMENTED** | Complete in code; not re-verified on `site1.local` this session |
| **PARTIAL** | Works for the main path; a documented gap remains |
| **SEEDED** | Created at install; needs a running runtime / models to be useful |
| **STALE-SITE** | Fixed in git; the live bench may still be on an older commit or old assets |
| **UPSTREAM** | Frappe core behaviour, not this app |

---

## 1. Product capabilities

| Capability | Status | Notes |
| --- | --- | --- |
| Local chat (AI Assistant) | **READY / STALE-SITE** | `send_message` → `run_agent_turn`. Default **Max Turn Duration = 0**. Patch `v0_0_10` only resets a leftover **90**. |
| Attach file then ask | **READY / STALE-SITE** | `prepare_documents_for_turn` extracts inline; wait default **8s** (was 45s). Indexed attachments are retrieved even when `use_knowledge` is off. |
| Document language | **READY** | `AI Document.language` is written on extract, backfilled by `v0_0_11`, and labelled in chat context. |
| Hybrid retrieval + citations | **IMPLEMENTED** | Dense + keyword, RRF fusion, numbered citations on the answer. |
| One-shot ask / search | **IMPLEMENTED** | Knowledge Explorer + `api.knowledge.ask` / `search`. |
| Document intelligence | **IMPLEMENTED** | Summarise, classify, extract, compare. Map-reduce for long text. |
| Model discovery & health | **IMPLEMENTED** | Operations + Model Manager pages. Scheduler probes every 5 min. |
| Tool calling | **IMPLEMENTED** | Approval gate for writes. Tools run as the session user. |
| Learning Loop | **IMPLEMENTED** | Teach → validate → approve → memory/skill → recall. Best-effort; cannot break a chat turn. |
| Pipelines | **IMPLEMENTED** | Ordered steps, nested runs with cycle guards, cancel/retry. |
| Event automation | **IMPLEMENTED** | `*` doc_events hook; skips `AI *` DocTypes; cached rule index. |
| Folder / File organization | **IMPLEMENTED** | Native Frappe `File` tree. No parallel FS. |
| AI Document tree | **READY / STALE-SITE** | Tree JS on the DocType. Mutations in `ai.document_tree`. **Not** NestedSet (`is_tree` is off). |
| Governance / quotas | **IMPLEMENTED** | Per-user / per-role `AI Resource Policy`. |
| Strict local-only network | **IMPLEMENTED** | Default **on**. Resolves DNS then checks loopback / RFC 1918. |
| Knowledge export / import | **IMPLEMENTED** | Manager-only JSON, embeddings optional. |
| Token streaming in Desk | **READY / STALE-SITE** | `send_message(stream=1)` streams the final, tool-free completion over `frappe.publish_realtime` (`ai_fr_hg:chat_token`). Falls back to blocking `provider.chat` when streaming is off, unsupported, or fails before the first token. |
| OpenDocument (.odt/.ods) | **IMPLEMENTED** | `OdtReader` / `OdsReader` in the existing registry; optional `odfpy`. |
| Moment.js RFC2822 warning | **UPSTREAM** | Frappe `refresh_when` with `_i: undefined`. Our `comment_when` calls are guarded. |

If Desk still shows `frappe.ai.relative_time is not a function`, the old tree path, a 90s cutoff, or a 45s attach wait, the site is **not** on `885e58e` with a **full** `bench build`. `bench build --app ai_fr_hg` alone can finish in ~100 ms and skip `desk.bundle`.

---

## 2. Service layer (`ai_fr_hg/ai/`)

Business logic lives here. Nothing in `ai/` imports `api/`. DocType controllers and RPCs only delegate.

### 2.1 Engine — `ai/engine.py` — **IMPLEMENTED**

| Function | Role |
| --- | --- |
| `get_settings()` | Cached AI Platform Settings single |
| `resolve_model(model, model_type)` | Named model, configured default, or best candidate |
| `build_options(model_doc, overrides)` | Platform → model → per-call merge |
| `normalise_messages(messages)` | dict / `ChatMessage` → `ChatMessage` |
| `run_chat(...)` | Quota, log, provider call, retry, failover, metrics |
| `run_embedding(texts, model, ...)` | Batch embed + contract validation |
| `update_model_metrics(model, result)` | Rolling latency / token stats |

### 2.2 Agent runtime — `ai/agent.py` — **READY**

| Function | Role |
| --- | --- |
| `get_agent(agent)` | Named or default agent |
| `check_agent_access(agent_doc)` | Role gate |
| `build_system_prompt(...)` | Persona + user-language + grounding + document-language + memory/skills + numbered context |
| `get_agent_knowledge_bases(...)` | Conversation overrides agent |
| `get_conversation_history(...)` | Recent turns as chat messages |
| `run_agent_turn(...)` | Full turn: retrieve → prompt → tools → persist. Accepts `documents` + `extra_context`. Retrieves attached files even when `use_knowledge` is off. Catches deadline / provider timeout / offline / OOM and **saves a friendly answer** instead of HTTP 417. |
| `save_message` / `update_conversation_stats` / `update_agent_stats` | Persistence |
| `create_conversation(...)` | New thread |

### 2.3 Deadline — `ai/deadline.py` — **READY**

| Function / type | Role |
| --- | --- |
| `Deadline` | Monotonic budget |
| `turn_budget(seconds)` | Context manager. **`0` / `None` = unlimited** |
| `get_deadline` / `remaining_seconds` / `expired` / `allows` / `clamp_timeout` | Read by engine, tools, ingestion wait |

Interactive default is unlimited. Background jobs never install a budget.

### 2.4 Settings helpers — `ai/settings.py` — **READY**

| Function | Role |
| --- | --- |
| `normalize_similarity_threshold(value)` | `0–1` stays; `1 < n ≤ 100` → `n/100`; `1` stays `1`. Used by settings + knowledge base (client and server). |

### 2.5 Knowledge / retrieval — `ai/knowledge.py` — **IMPLEMENTED**

| Function | Role |
| --- | --- |
| `index_document(document, force, embed)` | Chunk + optional embed |
| `embed_chunks(chunk_names, model)` | Batch 16; typed errors |
| `update_knowledge_base_stats(...)` | Denormalised counters |
| `get_accessible_knowledge_bases(user)` | Role-filtered |
| `keyword_search` / `semantic_search` | Ranked lists; optional `documents` filter |
| `retrieve(...)` | Hybrid / semantic / keyword + RRF (`K=60`) + optional folder scope |
| `build_context(results, max_characters)` | Numbered citation block, with `[language=…]` when known |

Vectors live on `AI Document Chunk` as base64 float32, pre-normalised. NumPy if present, pure Python otherwise.

### 2.6 Ingestion — `ai/ingestion.py` — **READY**

| Function | Role |
| --- | --- |
| `validate_source_access` / `get_source_content` / `get_file_content` / `get_doctype_content` | Permission-first reads |
| `fetch_url_content(url, user)` | Manual redirect validation, size caps, local-only |
| `ingest_file` / `ingest_text` | Create `AI Document` + enqueue |
| `enqueue_processing` / `process_document` / `process_document_now` | Canonical extract → index |
| **`prepare_documents_for_turn(names)`** | Inline extract; inject unread text labelled with language; short wait only if nothing readable |
| `wait_for_indexed(names, timeout)` | `DEFAULT_WAIT_SECONDS = 8.0` |
| `process_pending_documents()` | Hourly backfill / retry |

Caps: 50 MB default, archive member limits, zip-bomb checks, File identity disambiguation for duplicate URLs.

### 2.7 Intelligence — `ai/intelligence.py` — **IMPLEMENTED**

| Function | Role |
| --- | --- |
| `parse_json_response(text)` | Fenced / prose-wrapped JSON |
| `summarize(...)` | Map-reduce when over context |
| `classify(text, categories, ...)` | Constrained labels; invented labels mapped or `null` |
| `build_json_schema` / `extract_data` | `AI Extraction Schema` |
| `compare_documents(a, b, ...)` | Diff two `AI Document`s |
| `render_prompt_template` / `run_prompt_template` | Jinja templates |

### 2.8 Chunking — `ai/chunking.py` — **READY** (unit-tested)

`chunk_text`, `estimate_tokens`, `split_sentences`, `Chunk` dataclass. Heading-aware, overlapping windows.

### 2.9 Vectors — `ai/vector.py` — **READY** (unit-tested)

`encode_vector` / `decode_vector` / `norm` / `normalize` / `dot` / `cosine_similarity` / `rank`.

### 2.10 Providers — `ai/providers/` — **IMPLEMENTED**

| Piece | Status |
| --- | --- |
| `BaseProvider` + `ChatMessage`, `CompletionResult`, `ModelInfo`, `HealthStatus` | Contract |
| `OllamaProvider` | Native `/api/chat`, `/api/embed`, pull/delete/show |
| `OpenAICompatibleProvider` | vLLM, LM Studio, Text Generation WebUI |
| `LlamaCppProvider` | OpenAI at server root |
| `get_provider` / `get_provider_classes` / `get_failover_providers` | Registry + `ai_providers` hook |
| `stream_chat` on adapters | Used by `run_chat(..., on_token=)` for the final tool-free completion |

### 2.11 Readers — `ai/readers/` — **IMPLEMENTED** (optional deps degrade)

| Extension | Reader | Extra package |
| --- | --- | --- |
| txt, log, rst, py, js, ts, sql, sh, yaml, yml, ini, cfg, toml | `TextReader` | — |
| md, markdown | `MarkdownReader` | — |
| json | `JSONReader` | — |
| xml | `XMLReader` | — |
| html, htm | `HTMLReader` | beautifulsoup4 / lxml |
| eml, msg | `EmailReader` | — |
| pdf | `PDFReader` | pypdf |
| docx | `DocxReader` | python-docx |
| xlsx, xlsm | `XlsxReader` | openpyxl |
| pptx | `PptxReader` | python-pptx |
| odt | `OdtReader` | odfpy |
| ods | `OdsReader` | odfpy |
| csv, tsv | `CSVReader` | — |
| png, jpg, jpeg, webp, gif, bmp, tiff | `ImageReader` | vision model, OCR fallback (Pillow + pytesseract) |

Hook: `ai_document_readers`. Missing library → `MissingDependency` with install command, not a crash.

### 2.12 Tools — `ai/tools/` — **IMPLEMENTED**

| Function | Role |
| --- | --- |
| `build_tool_schema` / `get_agent_tool_schemas` | JSON Schema for the model |
| `execute_tool(...)` | Validate, permission, approval, dispatch, persist `AI Tool Invocation` |
| `approve_invocation` / `reject_invocation` | Whitelisted manager actions |
| Builtin handlers | `search_knowledge_base`, `get_document_text`, `get_document`, `list_documents`, `count_documents`, `run_report`, `current_datetime` |

Install seeds six tools. Default agent **General Assistant** is bound to only three: `search_knowledge_base`, `get_document_text`, `current_datetime`. `use_knowledge` on that agent is **off** so empty-site small talk does not pay an embedding round-trip.

### 2.13 Pipeline — `ai/pipeline.py` — **IMPLEMENTED**

`validate_pipeline_dependencies`, `pipeline_step_method`, `resolve_pipeline_step_method`, `run_pipeline`, `execute_run`, `execute_step`. Nested depth / cycle / cancellation / checkpointing. Custom methods must be marked trusted (`ai_pipeline_methods` hook).

### 2.14 Automation — `ai/automation.py` — **IMPLEMENTED**

`get_rule_index` (cached), `handle_document_event` (`*` hook), `trigger_rule`, `evaluate_condition`, `execute_rule`. Ignores `AI *` DocTypes and install/migrate/patch.

### 2.15 Monitoring — `ai/monitoring.py` — **IMPLEMENTED**

`check_provider_health`, `check_all_providers`, `sync_provider_models`, `sync_all_models`, `get_platform_metrics`. Name-based model-type guess on discover.

### 2.16 Governance — `ai/governance.py` — **IMPLEMENTED**

`get_effective_policy`, `check_quota`, `check_capability`, `check_document_quota`, `record_usage`. User policy > role policy > global. System Manager bypasses.

### 2.17 Logging — `ai/logging.py` — **IMPLEMENTED**

`redact`, `start_execution_log`, `finish_execution_log`, `write_audit_log`. Patterns compiled and cached; cleared on settings save.

### 2.18 Learning — `ai/learning.py` + `ai/learning_utils.py` — **IMPLEMENTED**

| Function | Role |
| --- | --- |
| `create_candidate` / `validate_candidate` / `check_conflicts` | Stages 1–4 |
| `approve_candidate` / `reject_candidate` / `process_candidate` / `teach` | Gate + promotion |
| `recall` / `prepare_memory_context` / `build_memory_context` | Additive prompt injection |
| `observe_feedback` / `record_feedback` | Helpful / not-helpful; negative without correction is a failure example, never truth |
| Utils | tokenize, score, rank, dedupe, classify, prompt blocks (no Frappe) |

### 2.19 Folders — `ai/folders.py` — **IMPLEMENTED**

Native Frappe `File` mutations: create/rename/move/copy/delete, bulk move, list/tree/search, favorites, recents, tabs, default folder, ingest-with-folder, document-scoped folders. Permission-aware counts. Circular-move and uniqueness checks.

### 2.20 Document tree — `ai/document_tree.py` — **READY**

Mixed **folder + AI Document** tree for Desk Tree View. Not NestedSet.

Public: `get_children`, `create_folder`, `rename_document` / `rename_folder` / `rename_node`, `move_*`, `copy_*`, `delete_*`, `bulk_move_nodes`, `bulk_delete_nodes`, `resolve_document_name`, `split_node_value`. Large folder jobs enqueue with subtree fingerprints and optimistic concurrency (`expected_modified`).

### 2.21 Language — `ai/language.py` — **READY**

`detect_language`, `language_name`, `resolve_document_language`. Script counts plus function-word hints. No extra packages. Bulgarian is first-class; also EN/DE/FR/ES/IT/RU/UK and script gates for EL/AR/HE/ZH/JA/KO.

### 2.22 Organization — `ai/organization.py` — **IMPLEMENTED**

`organization_name_key(value)` — collation-independent, case-insensitive location key for AI Document names.

### 2.23 Exceptions — `ai/exceptions.py` — **IMPLEMENTED**

`AIError` hierarchy: provider (offline / timeout / deadline), model, local-only, quota, tool, document (source permission / unsupported / corrupt / resource / fetch), pipeline (recorded / approval), folder (not found / exists / circular / permission / not empty / file / invalid name).

---

## 3. Whitelisted API (`ai_fr_hg/api/`)

Thin wrappers. `use_json_request_body = True`. Bulk tree still accepts JSON-stringified `nodes`.

### 3.1 Chat — `api/chat.py` — **READY**

| Method | Purpose |
| --- | --- |
| `send_message(message, conversation, agent, knowledge_bases, model, documents)` | Grounded reply. Prepares uploads. `timed_out` flag. |
| `start_conversation` / `get_conversation` / `list_conversations` | Sessions |
| `delete_conversation` / `archive_conversation` / `rename_conversation` | Lifecycle |
| `submit_feedback` | Learning Loop observation |
| `summarize_conversation` | Stored summary |
| `get_chat_context` | Bootstrap: agents, models, KBs, settings |

`_get_turn_budget()`: `None` or `0` → unlimited.

### 3.2 Knowledge — `api/knowledge.py` — **IMPLEMENTED**

`upload_document`, `add_text`, `reprocess_document`, `reindex_knowledge_base`, `search`, `ask`, `summarize_document`, `classify_document`, `extract_document_data`, `compare`, `get_document_chunks`, `get_knowledge_overview`, `get_supported_formats`.

### 3.3 Admin — `api/admin.py` — **IMPLEMENTED** (AI Manager / System Manager)

`test_provider`, `test_all_providers`, `discover_models`, `pull_model`, `test_model`, `get_dashboard`, `get_usage_report`, `export_knowledge_base`, `import_knowledge_base`, `purge_logs`, `get_system_status`.

### 3.4 Document tree — `api/document_tree.py` — **READY**

`get_children`, `create_folder`, `rename_node`, `move_node`, `copy_node`, `delete_node`, `bulk_move_nodes`, `bulk_delete_nodes`. Optimistic concurrency via `expected_modified`.

### 3.5 Folders — `api/folders.py` — **IMPLEMENTED**

Create/rename/move/delete/copy file and folder, `set_file_folder`, `bulk_move`, list/tree/breadcrumbs/info/search, favorites, recents, tabs, `get_default_folder`, `upload_file_with_folder`.

### 3.6 Learning — `api/learning.py` — **IMPLEMENTED**

`teach`, `approve_candidate`, `reject_candidate`, `list_candidates`, `list_memories`, `list_skills`, `overview`.

### 3.7 Extra whitelist on DocType controllers

Form buttons call these; they delegate into `ai/`:

- Document: `process`, `reprocess`, `generate_summary`, `run_extraction`
- Knowledge Base: `reindex`, `refresh_stats`
- Provider: `test_connection`, `discover_models`
- Model: `test_model`, `refresh_metadata`
- Agent: `start_conversation`, `test_agent`
- Conversation: `send`, `generate_summary`
- Pipeline / Run / Task / Rule: `run_now`, `retry`, `cancel_run`, `test_rule`
- Candidate / Memory / Skill: `validate_and_test`, `approve`, `reject`, `archive`, `enable` / `disable`
- Prompt template: `preview`
- Extraction schema: `test_extraction`
- Tools: `approve_invocation`, `reject_invocation`
- Learning: `record_feedback`

~102 `@frappe.whitelist` methods in the app.

---

## 4. Data model (42 DocTypes)

### AI Core (9)

| DocType | Kind | Status |
| --- | --- | --- |
| AI Platform Settings | Single, 66 fields, 7 tabs | **READY** — `max_turn_seconds` default 0; threshold accepts % |
| AI Provider | 27 fields | **IMPLEMENTED** |
| AI Model | 50 fields + Parameter / Version children | **IMPLEMENTED** |
| AI Prompt Template + Variable | — | **IMPLEMENTED** |
| AI Execution Log | 31 fields | **IMPLEMENTED** |
| AI Folder Settings | 14 fields | **IMPLEMENTED** |
| AI Folder Favorite | 2 fields | **IMPLEMENTED** |

### AI Knowledge (8)

| DocType | Kind | Status |
| --- | --- | --- |
| AI Knowledge Base + Role | 20 fields | **IMPLEMENTED** — client + server threshold / chunk validation |
| AI Document | 55 fields, **not** NestedSet | **READY** — tree JS on DocType |
| AI Document Chunk | 19 fields (embedding Long Text) | **IMPLEMENTED** |
| AI Document Tag | child | **IMPLEMENTED** |
| AI Extraction Schema + Field | — | **SEEDED** Invoice Data, Contract Summary |
| AI Search Query | telemetry | **IMPLEMENTED** |

### AI Conversation (10)

AI Agent (+ Knowledge Base / Role / Tool children), AI Conversation, AI Message, AI Tool (+ Parameter / Role), AI Tool Invocation. **IMPLEMENTED**. Seeded agent: **General Assistant**.

### AI Automation (6)

AI Pipeline + Step, AI Pipeline Run + Run Step, AI Automation Rule, AI Task. **IMPLEMENTED**.

### AI Operations (5)

AI Service Health Log, AI Audit Log, AI Resource Policy + Policy Model, AI Usage Snapshot. **IMPLEMENTED**. Seeded policy: **Standard AI User**.

### AI Learning (3)

AI Knowledge Candidate, AI Memory, AI Skill. **IMPLEMENTED**.

---

## 5. Desk UI

### Pages

| Route | File | Lines | Status |
| --- | --- | --- | --- |
| `/app/ai-assistant` | `ai_core/page/ai_assistant/ai_assistant.js` | ~850 | **READY / STALE-SITE** — local `relative_time`; tracks uploads; streams tokens on `ai_fr_hg:chat_token`. |
| `/app/knowledge-explorer` | `ai_knowledge/page/knowledge_explorer/knowledge_explorer.js` | 432 | **READY / STALE-SITE** — local `relative_time` (was crashing) |
| `/app/ai-operations` | `ai_operations/page/ai_operations/ai_operations.js` | 491 | **IMPLEMENTED** — local `relative_time` |
| `/app/ai-model-manager` | `ai_operations/page/ai_model_manager/ai_model_manager.js` | 416 | **IMPLEMENTED** |

Page JS compiles into **desk.bundle**, independently of `ai_fr_hg.bundle.js`. That is why helpers were duplicated locally.

### Workspaces

AI Control Center (apps-screen home), AI Workspace, AI Knowledge, AI Automation, AI Learning.

### Reports (AI Learning)

Learning Activity, Memory Usage, Skill Summary.

### Client helpers

| Asset | Status |
| --- | --- |
| `public/js/ai_helpers.js` → `frappe.ai` | status_color, normalize_similarity_threshold, relative_time, compact, ask, add_form_button |
| `public/js/file.js` | File form guard — no `file_type.toLowerCase` crash on folders |
| `public/js/file_list.js` / `file_folder.js` | Native File list / folder picker |
| `ai_document_tree.js` on the DocType | Tree View only; mutations stay in Python |
| `public/js/ai_document_tree.js` | **deleted** in `885e58e` |
| SCSS | `ai_assistant`, `ai_dashboard`, `ai_document_tree` via `ai_fr_hg.bundle.scss` |

### Hooks that matter

- `doctype_js = {File: public/js/file.js}`
- `doctype_list_js = {File: public/js/file_list.js}`
- `doctype_tree_js = {AI Document: ai_knowledge/doctype/ai_document/ai_document_tree.js}`
- Row-level `permission_query_conditions` + `has_permission` for 16 DocTypes
- `doc_events` on `*` (automation) and `File` (ingest + folder lock)
- Roles fixture: AI Manager, AI User, AI Auditor
- Extension hooks (empty by default): `ai_providers`, `ai_document_readers`, `ai_tools`, `ai_pipeline_methods`

---

## 6. Background jobs (`tasks.py`)

| Job | Schedule | Status |
| --- | --- | --- |
| `health_check` | cron `*/5` (throttled to settings interval, default 15 min) | **IMPLEMENTED** |
| `run_scheduled_pipelines` | cron `*/10` | **IMPLEMENTED** (needs optional `croniter`) |
| `process_pending_documents` | hourly_long | **IMPLEMENTED** |
| `sync_models` | daily_long | **IMPLEMENTED** |
| `rollup_usage` | daily_long | **IMPLEMENTED** |
| `backup_knowledge` | daily_long | **IMPLEMENTED** (off until enabled) |
| `cleanup_logs` | weekly_long | **IMPLEMENTED** |

Default log retention (also in `hooks.default_log_clearing_doctypes`): Execution 90d, Health 30d, Audit 365d, Search Query 30d.

---

## 7. Install, patches, seed

`after_install` is idempotent: roles, settings, **Local Ollama** (`http://localhost:11434`), six tools, two extraction schemas, two prompt templates, **General Knowledge**, **General Assistant**, **Standard AI User** policy, default folders under `Home/AI Platform`.

| Patch | Purpose |
| --- | --- |
| v0_0_1 | Fast defaults + code fields |
| v0_0_2 | Introduced turn time budget (historical 90s) |
| v0_0_3 | Learning DocType modules |
| v0_0_4 | Folder organization |
| v0_0_5 | Legacy Long Int (pre_model_sync) |
| v0_0_6 | Folder audit category |
| v0_0_7 | Site file directories |
| v0_0_8 | Learning audit category |
| v0_0_9 | AI Document tree organization |
| **v0_0_10** | **90 → 0** for `max_turn_seconds` only if still the shipped 90 |
| **v0_0_11** | Detect `AI Document.language` for already extracted documents |

---

## 8. Tests

19 test modules, **56 classes, 287 methods**.

| Suite | Methods | Needs Frappe DB? |
| --- | --- | --- |
| `tests/test_units.py` | 89 | No — chunking, vectors, JSON, network, readers, tools, threshold, deadline, wait default, language detection, streaming decision |
| `tests/test_learning_utils.py` | 19 | No |
| `tests/test_folder_units.py` | 16 | No |
| `tests/test_document_tree_units.py` | 20 | No |
| Colocated DocType integration | 139 | Yes — runtime stubbed, no GPU |

This sandbox has **no Frappe**, so `python -m unittest` fails with `ModuleNotFoundError: frappe`. `compileall` succeeds. Full suite: `bench --site site1.local run-tests --app ai_fr_hg`.

---

## 9. Documentation

| File | Role |
| --- | --- |
| `README.md` | Install, Ollama, first-run |
| `docs/ARCHITECTURE.md` | Layout, data model, lifecycle, design |
| `docs/FILE_TO_ANSWER.md` | Attach → index → cite |
| `docs/LEARNING.md` | Learning Loop |
| `docs/CONFIGURATION.md` | Every setting + topologies |
| `docs/EXTENDING.md` | Providers / readers / tools / pipeline methods |
| `docs/API.md` | REST shapes |
| `docs/PROJECT_STATUS.md` | This inventory |

Small doc drift: CONFIGURATION says OCR default **off**; the DocType default is **on** (`ocr_enabled = 1`). README says 30+ formats; the registry is the table in §2.11.

---

## 10. What is not done / watch-outs

1. **Live site may lag git.** Pull `885e58e`, then **full** `bench build` (not `--app` only), `clear-cache`, supervisor restart, hard refresh.
2. **Chat cutoff after migrate.** Confirm `AI Platform Settings.max_turn_seconds` is `0`. The patch does not overwrite a custom non-90 value.
3. **Desk chat is not streamed.** First token on a cold 7B/8B can look “stuck” even with an unlimited budget.
4. **CI on PR #23 is red** (linter + server). Not reproduced here.
5. **Default agent** does not auto-retrieve the whole KB (`use_knowledge = 0`); attached files, knowledge chips, or the `search_knowledge_base` tool still ground the turn.
6. **Streaming, OpenDocument, richer default-agent tools** (`list_documents`, `get_document`, `run_report`) are code-complete as building blocks but not first-class in the default UX.
7. **Upstream moment warning** remains in Frappe core.
8. Optional extras: `bench pip install --editable "./apps/ai_fr_hg[documents]"` (and `[ocr]`, `[performance]`, `[scheduling]`).

---

## 11. Apply this branch on `site1.local`

```bash
cd ~/frappe-bench/apps/ai_fr_hg
git fetch origin
git checkout arena/01a01846-ai-fr-hg
git pull

cd ~/frappe-bench
bench --site site1.local migrate
bench --site site1.local clear-cache
bench build                    # full build — required for desk.bundle
sudo supervisorctl restart all
```

Then hard-refresh Desk (Ctrl+Shift+R). Confirm **Max Turn Duration** is 0 and Ollama is up (`ollama list` should show at least a chat model and `nomic-embed-text`).
