# API reference

All endpoints are Frappe whitelisted methods, callable over REST or from
client-side JavaScript. They enforce the session user's permissions and the
governance policies in force.

**REST**

```
POST /api/method/<dotted.path>
Content-Type: application/json
Authorization: token <api_key>:<api_secret>
```

**JavaScript**

```javascript
const response = await frappe.xcall("<dotted.path>", { ...params });
```

---

## Chat

### `ai_fr_hg.api.chat.send_message`

Send a message and get a grounded reply. Creates the conversation if none is
supplied.

| Parameter | Type | Notes |
| --- | --- | --- |
| `message` | string | Required. |
| `conversation` | string | Omit to start a new one. |
| `agent` | string | Defaults to the configured default agent. |
| `knowledge_bases` | list | Overrides the agent's knowledge bases. |
| `model` | string | Overrides the agent's model. |
| `documents` | list | Just-uploaded `AI Document` names to ground this turn. |
| `stream` | bool | When true and **Enable Streaming** is on, tokens are pushed on the native `ai_fr_hg:chat_token` realtime event. The HTTP response still returns the finished answer. Off, or a non-streaming provider, uses the same blocking path. |
| `turn_id` | string | Client-generated id so the Desk can match realtime fragments to this send. Generated server-side when omitted. |

```json
{
  "answer": "The refund window is 30 days from purchase [1].",
  "conversation": "AICONV-2026-00042",
  "agent": "General Assistant",
  "model": "llama3.1:8b (Local Ollama)",
  "citations": [
    {
      "chunk": "a1b2c3",
      "document": "AIDOC-2026-00007",
      "document_title": "Returns Policy",
      "knowledge_base": "General Knowledge",
      "content": "Customers may return items within thirty days...",
      "score": 0.8734,
      "semantic_score": 0.8912,
      "keyword_score": 0.7421,
      "heading": "Refunds",
      "page_number": 3
    }
  ],
  "tool_invocations": [],
  "timed_out": false,
  "streamed": true,
  "turn_id": "a1b2c3d4e5f6",
  "message": "AIMSG-000123",
  "prompt_tokens": 842,
  "completion_tokens": 47,
  "total_tokens": 889,
  "duration_ms": 2140
}
```

`timed_out` is `true` when a positive **Max Turn Duration** budget ran out.
The default is `0` (unlimited), so local models are not cut off. When a budget
is configured the call still returns `200` with a saved `answer` explaining
the timeout, rather than leaving the connection open for the proxy to
terminate with a `504`.

### Other chat endpoints

| Method | Purpose |
| --- | --- |
| `start_conversation(agent, title, knowledge_bases)` | Create an empty conversation. |
| `get_conversation(conversation)` | Full history with parsed citations. |
| `list_conversations(limit, include_archived)` | The user's conversations, pinned first. |
| `rename_conversation(conversation, title)` | Rename. |
| `archive_conversation(conversation)` | Archive without deleting. |
| `delete_conversation(conversation)` | Delete the conversation and its messages. |
| `submit_feedback(message, feedback, correction, reason)` | Rate an answer; optional correction/reason enters the governed learning loop. |
| `summarize_conversation(conversation)` | Generate and store a summary. |
| `get_chat_context()` | Bootstrap payload: agents, models, knowledge bases, settings. |

---

## Knowledge

### `ai_fr_hg.api.knowledge.upload_document`

Ingest a previously uploaded file. Processing is queued by default.

| Parameter | Type | Notes |
| --- | --- | --- |
| `file_url` | string | Required. From Frappe's file upload. |
| `knowledge_base` | string | Required. |
| `title` | string | Defaults to the filename. |
| `extraction_schema` | string | Run structured extraction after indexing. |
| `process_now` | bool | Process synchronously instead of queuing. |

```json
{ "document": "AIDOC-2026-00008", "status": "Queued" }
```

### `ai_fr_hg.api.knowledge.search`

Ranked passages, no model generation.

| Parameter | Type | Notes |
| --- | --- | --- |
| `query` | string | Required. |
| `knowledge_bases` | list | Defaults to everything the user can read. |
| `top_k` | int | Default 10. |
| `search_type` | string | `Hybrid`, `Semantic` or `Keyword`. |

```json
{
  "query": "refund policy",
  "count": 3,
  "results": [ { "document_title": "Returns Policy", "score": 0.8734, "...": "..." } ]
}
```

### `ai_fr_hg.api.knowledge.ask`

One-shot grounded question answering. Same response shape as `send_message`,
but nothing is persisted.

| Parameter | Type |
| --- | --- |
| `question` | string, required |
| `knowledge_bases` | list |
| `agent` | string |
| `model` | string |

### Document intelligence

| Method | Returns |
| --- | --- |
| `summarize_document(document, max_words, save)` | `{document, summary}` |
| `classify_document(document, categories, save)` | `{document, category, confidence, reason}` |
| `extract_document_data(document, schema, save)` | `{document, schema, data}` |
| `compare(document_a, document_b, instructions)` | `{document_a, document_b, comparison}` |
| `reprocess_document(document, force)` | `{document, status}` |
| `reindex_knowledge_base(knowledge_base)` | `{knowledge_base, queued}` |
| `add_text(text, knowledge_base, title)` | `{document, status}` |
| `get_document_chunks(document, limit)` | List of chunks with embedding status. |
| `scan_pattern_entities(document)` | `{document, total, created, updated, removed, by_type}` — high-precision regex scan of the document's stored content into `AI Pattern Entity` rows. Requires write access, like the other intelligence actions. |
| `get_pattern_entities(document, entity_type, limit)` | `{document, entities, entity_counts}` — occurrences-ordered rows with provenance quotes, grouped counts per type. Read access. |
| `get_knowledge_overview()` | Counters, recent documents, failed documents. |
| `get_supported_formats()` | Extensions, grouped by reader. |

Classification is constrained to the supplied categories: if the model invents
one, the platform maps it back or returns `null` rather than passing through a
hallucinated label.

---

## Translation

Arabic, English and Hebrew, translated on local models. Full guide:
[`docs/TRANSLATION.md`](TRANSLATION.md).

### `ai_fr_hg.api.translation.translate_document`

Translate an extracted document into a stored, reviewable `AI Translation`.

| Parameter | Type | Notes |
| --- | --- | --- |
| `document` | string | Required. Must already have extracted text. |
| `target_language` | string | Required: `ar`, `en` or `he`. |
| `source_language` | string | Detected from the text when omitted. |
| `model` | string | Overrides the default translation model. |
| `glossary` | string | An `AI Translation Glossary` to enforce. |
| `tone` | string | `Neutral`, `Formal`, `Informal`, `Technical` or `Legal`. |
| `domain` | string | Subject domain hint, e.g. `construction contracts`. |
| `preserve_formatting` | bool | Preserve extracted-text blocks/separators; does not reconstruct the source file. |
| `index_output` | bool | Also store the result as a searchable document. |
| `background` | bool | Default true; false translates in the request. |

```json
{
  "translation": "AITRN-2026-00001",
  "status": "Queued",
  "job_id": "ai-translation::AITRN-2026-00001"
}
```

### `ai_fr_hg.api.translation.translate`

Translate a passage inline, up to 20 000 characters. Same optional parameters,
with `text` instead of `document`.

```json
{
  "text": "…",
  "source_language": "en",
  "target_language": "ar",
  "direction": "rtl",
  "quality_score": 96.4,
  "issues": {},
  "memory_hits": 2,
  "flagged": 0,
  "segment_count": 14,
  "model": "qwen2.5:7b",
  "duration_ms": 18422,
  "total_tokens": 5310
}
```

### Other translation endpoints

| Method | Returns |
| --- | --- |
| `get_languages()` | `{enabled, languages, pairs}` |
| `get_translation(translation, include_segments)` | Record plus per-segment source, translation, status, score and issues. |
| `list_translations(document, knowledge_base, target_language, limit)` | Translations the user may read. |
| `retranslate(translation, segment_index, instructions)` | Re-runs one segment and rescores it. |
| `index_output(translation)` | `{translation, document}` |
| `get_glossaries(knowledge_base)` | Enabled glossaries. |

Every segment is scored locally before it is stored: a translation that loses a
figure, answers instead of translating, or comes back in the wrong script is
flagged as **Needs Review** rather than returned as a clean result.

---

## Administration

All of these require `AI Manager` or `System Manager`.

| Method | Purpose |
| --- | --- |
| `test_provider(provider)` | Probe one provider, persist the result. |
| `test_all_providers()` | Probe every enabled provider. |
| `discover_models(provider, create_missing)` | Register models the runtime reports. |
| `pull_model(provider, model_name)` | Download a model (Ollama). Runs in the background. |
| `test_model(model, prompt)` | Send a probe prompt; returns latency and tokens/sec. |
| `get_dashboard()` | Full operations payload. |
| `get_system_status()` | Readiness checklist. |
| `get_usage_report(days, user)` | Usage by day, model and user. |
| `export_knowledge_base(knowledge_base, include_embeddings)` | Export to a private JSON file. |
| `import_knowledge_base(file_url, knowledge_base)` | Import, skipping duplicates by checksum. |
| `purge_logs(doctype, days)` | Delete old log records. |

### `get_system_status`

```json
{
  "ready": false,
  "offline_mode": true,
  "checks": [
    { "label": "Platform enabled", "status": true, "hint": "..." },
    {
      "label": "An embedding model is registered",
      "status": false,
      "hint": "Install an embedding model, e.g. `ollama pull nomic-embed-text`."
    }
  ]
}
```

### `get_dashboard`

Returns `providers`, `models`, `knowledge`, `activity_24h`,
`providers_detail`, `models_detail`, `recent_errors`, `pending_approvals`,
`active_jobs` and `top_users`.

---

## Learning

### `ai_fr_hg.api.learning.teach`

Create and test a governed knowledge candidate. The method never writes
straight into active memory. With the default policy it returns a `Validated`
or `Conflict` candidate for review; when **Require Approval for Learned
Knowledge** is disabled, a conflict-free candidate is promoted automatically.

| Parameter | Type | Notes |
| --- | --- | --- |
| `content` | string | Required teaching, correction, fact, preference, or procedure. |
| `title` | string | Optional review title. |
| `candidate_type` | string | `Fact`, `Preference`, `Instruction`, `Feedback`, or `Document`; inferred when omitted. |
| `source_type` | string | Defaults to `Explicit Teaching`. Document/tool/automation sources require a source record. |
| `source_reference_doctype` | string | Must be paired with `source_reference_name`; the caller must be able to read it. |
| `source_reference_name` | string | Originating record. |
| `provenance` | string | Optional detail; a basic user/source statement is generated when omitted. |
| `confidence` | number | Candidate confidence percentage. |
| `target_scope` | string | `Global`, `User`, `Role`, or `Agent`. Preferences and feedback default to the teaching user. |
| `target_scope_value` | string | Required for non-global scopes. |

```json
{
  "candidate": "AI-CAND-2026-00012",
  "status": "Validated",
  "valid": true,
  "conflicts": { "duplicates": [], "overlaps": [] },
  "validation": { "checks": [] }
}
```

### Other learning endpoints

| Method | Purpose |
| --- | --- |
| `approve_candidate(candidate, notes)` | AI Manager/System Manager promotion to memory or skill. Conflict overrides require notes. |
| `reject_candidate(candidate, notes)` | Reject without learning. |
| `list_candidates(status)` | Permission-filtered review queue. |
| `list_memories(status, limit)` | Active or archived memories visible in the caller's scope. |
| `list_skills(enabled, limit)` | Skills visible in the caller's scope. |
| `overview()` | Permission-filtered learning counters. |

Every assistant response records the identifiers of memories and skills that
shaped it. `submit_feedback` updates the corresponding helpful/not-helpful
counters exactly once. Negative feedback without a supplied correction creates
a clearly labelled failure example, never an authoritative copy of the wrong
answer.

---

## Tools

| Method | Purpose |
| --- | --- |
| `ai_fr_hg.ai.tools.approve_invocation(invocation)` | Approve and run a held tool call. |
| `ai_fr_hg.ai.tools.reject_invocation(invocation)` | Reject it. |

---

## Errors

Failures return standard Frappe error responses. The exception hierarchy is:

| Exception | Meaning |
| --- | --- |
| `AIError` | Base class. |
| `ProviderError` | Provider misconfigured or returned an error. |
| `ProviderOfflineError` | Runtime unreachable. |
| `ProviderTimeoutError` | Request exceeded the timeout. |
| `ModelNotFoundError` | Model not registered or not present on the runtime. |
| `QuotaExceededError` | Governance limit reached. |
| `DocumentProcessingError` | Extraction failed. |
| `ToolExecutionError` | Tool failed or is not permitted. |
| `PipelineError` | Pipeline step failed. |

Tool execution never raises into the model loop — `execute_tool` returns
`{"status": "Failed", "error": "..."}` so the model can recover or explain
itself rather than the whole turn collapsing.

---

## Example: end-to-end ingestion and query

```python
import requests

BASE = "http://localhost:8000/api/method"
AUTH = {"Authorization": "token <key>:<secret>"}

# 1. Upload a file through Frappe's standard endpoint
with open("policy.pdf", "rb") as handle:
    upload = requests.post(
        f"{BASE.replace('/api/method', '')}/api/method/upload_file",
        headers=AUTH,
        files={"file": handle},
        data={"is_private": 1},
    ).json()["message"]

# 2. Ingest it
document = requests.post(
    f"{BASE}/ai_fr_hg.api.knowledge.upload_document",
    headers=AUTH,
    json={"file_url": upload["file_url"], "knowledge_base": "General Knowledge"},
).json()["message"]

# 3. Ask about it once indexing finishes
answer = requests.post(
    f"{BASE}/ai_fr_hg.api.knowledge.ask",
    headers=AUTH,
    json={"question": "What is the refund window?"},
).json()["message"]

print(answer["answer"])
for index, citation in enumerate(answer["citations"], start=1):
    print(f"[{index}] {citation['document_title']} p{citation['page_number']}")
```
