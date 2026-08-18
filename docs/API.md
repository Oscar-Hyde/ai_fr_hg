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
  "message": "AIMSG-000123",
  "prompt_tokens": 842,
  "completion_tokens": 47,
  "total_tokens": 889,
  "duration_ms": 2140
}
```

`timed_out` is `true` when the turn hit its **Max Turn Duration** budget. The
call still returns `200` with a saved `answer` explaining the timeout, rather
than leaving the connection open for the proxy to terminate with a `504`.

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
| `file_record` | string | Exact native `File.name` returned by upload. Strongly recommended and required to disambiguate duplicate URL/content rows. |
| `knowledge_base` | string | Required. |
| `title` | string | Defaults to the filename. |
| `folder` | string | Canonical native File folder. Defaults through the existing folder policy. |
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
| `get_knowledge_overview()` | Counters, recent documents, failed documents. |
| `get_supported_formats()` | Extensions, grouped by reader. |

Classification is constrained to the supplied categories: if the model invents
one, the platform maps it back or returns `null` rather than passing through a
hallucinated label.

---

## AI Document Tree

All tree methods are under `ai_fr_hg.api.document_tree`. This module is a thin
whitelisted facade; authorization, locking, stale-state checks, collisions,
transactions, and audit live in `ai_fr_hg.ai.document_tree`.

Node identifiers are mixed but unambiguous:

- folders use canonical native `File.name` paths such as `Home/Policies`;
- documents use `document::<AI Document.name>`;
- the visible root is `AI Documents` and maps to `Home`;
- pagination nodes are opaque values beginning `__ai_document_page__:` and must
  be returned unchanged as the next `parent`.

### `get_children`

```text
ai_fr_hg.api.document_tree.get_children(
    doctype=None,
    parent=None,
    is_root=False,
    knowledge_base=None,
    search=None,
    limit=100,
)
```

| Parameter | Type | Notes |
| --- | --- | --- |
| `doctype` | string | If supplied, must be `AI Document`. |
| `parent` | string | Omit for root capability discovery; otherwise a node value or opaque page value. |
| `is_root` | bool | Native Tree View compatibility flag; maps the visible root to `Home`. |
| `knowledge_base` | string | Optional server-side document filter. Folders remain navigable. |
| `search` | string | Literal case-insensitive folder/document search. `%`, `_`, and backslash are not wildcards. |
| `limit` | integer | Defaults to 100; clamped to 10–250. |

Root discovery returns one capability-bearing node:

```json
[
  {
    "value": "AI Documents",
    "title": "AI Documents",
    "node_type": "root",
    "folder": "Home",
    "expandable": true,
    "modified": "2026-08-18 12:00:00.000000",
    "can_read": true,
    "can_write": true,
    "can_create_folder": true,
    "can_create_document": true,
    "can_create_child": true,
    "can_delete": false
  }
]
```

A child page contains native folder/document payloads and, when needed, a final
`Load more…` page node:

```json
[
  {
    "value": "Home/Policies",
    "title": "Policies",
    "node_type": "folder",
    "folder": "Home",
    "expandable": true,
    "modified": "2026-08-18 12:01:00.000000",
    "can_read": true,
    "can_write": true,
    "can_copy": true,
    "can_delete": true
  },
  {
    "value": "document::AIDOC-2026-00008",
    "title": "Returns Policy.pdf",
    "node_type": "document",
    "document": "AIDOC-2026-00008",
    "folder": "Home",
    "status": "Indexed",
    "knowledge_base": "General Knowledge",
    "expandable": false,
    "modified": "2026-08-18 12:02:00.000000",
    "can_read": true,
    "can_write": true,
    "can_copy": true,
    "can_delete": false
  },
  {
    "value": "__ai_document_page__:<opaque>",
    "title": "Load more…",
    "node_type": "page",
    "folder": "Home",
    "expandable": true,
    "can_read": true,
    "can_write": false,
    "can_delete": false
  }
]
```

Capability values are display hints, not authorization grants. The server
re-checks every mutation. Global search intersects permission-visible documents
with permission-visible parent folders, uses a parent/search-bound keyset
continuation, scans at most 1,000 candidates per request, and emits no cursor
based only on hidden rows.

### Individual mutations

| Method | Parameters | Result/behavior |
| --- | --- | --- |
| `create_folder` | `folder_name`, `parent`, `expected_parent_modified` | Creates a native File folder and returns its node/path. |
| `rename_node` | `node`, `new_name`, `expected_modified` | Preserves stable document identity; folders update descendant paths. |
| `move_node` | `node`, `target_folder`, `expected_modified` | Moves to a canonical folder or `Home`; may return a queued job for a large folder. |
| `copy_node` | `node`, `target_folder`, `new_name`, `expected_modified` | Creates a new identity; an omitted name gets a deterministic copy suffix. Large folder copies may queue. |
| `delete_node` | `node`, `recursive`, `expected_modified` | Applies native retention/attachment policy; non-empty folders require `recursive=1`. Large recursive deletion may queue. |

`expected_modified` is the timestamp returned in the node payload. Supply it on
interactive mutations. A stale value raises Frappe `TimestampMismatchError`;
the client must refresh rather than retry with guessed state. Queue workers also
check an internal complete-subtree fingerprint, so a late descendant or source
change makes the job stale before mutation.

Completed response example:

```json
{
  "status": "Completed",
  "node": "Home/Archive/Policies",
  "name": "Home/Archive/Policies"
}
```

Queued response example:

```json
{
  "status": "Queued",
  "job_id": "ai-document-tree-move::a1b2c3d4e5",
  "source": "Home/Policies",
  "target_folder": "Home/Archive"
}
```

Queued operations execute on the long worker under the original session user.
Permissions and stale state are checked again. A job ID is an acknowledgement,
not evidence of completion; use the ordinary Frappe job/worker observability
used by the Operations UI.

### Bulk mutations

```text
bulk_move_nodes(nodes, target_folder, enqueue=None)
bulk_delete_nodes(nodes, recursive=False, enqueue=None)
```

- `nodes` may be a JSON array or an encoded JSON array string.
- At most 500 identifiers are accepted.
- Every explicit selection is authorized before duplicate/nested nodes are
  pruned.
- Destination and complete descendant permissions are checked server-side.
- The full operation is atomic; there is no per-item partial-success response.
- `enqueue` may force/disable queuing. When omitted, work over 100 affected
  folder/document/File rows queues automatically.
- Bulk delete of a non-empty folder requires `recursive=1`.

```json
{
  "status": "Completed",
  "moved": [
    {"node": "document::AIDOC-2026-00008", "name": "AIDOC-2026-00008", "folder": "Home/Archive"}
  ],
  "target_folder": "Home/Archive"
}
```

### Identity, authorization, and legacy boundaries

- Moving an AI Document never creates another AI Document identity. A move to
  its current folder returns `unchanged: true` after stale/permission checks and
  performs no physical File or shared-owner mutation.
- Copying never mutates the source and never copies chunks, embeddings, shares,
  unrelated attachments, or prior processing/index state.
- Same-folder name collision is enforced by normalized organization identity;
  equal hashes and URLs may remain separate documents.
- File-backed clients should always propagate exact `file_record` from upload.
  URL-only read/backfill resolution accepts a singleton exact attachment or
  singleton global File row. Multiple candidates fail closed; no API selects
  the oldest match. Ownership-changing move/delete operations are stricter:
  only a stable remaining File link can receive native attachment ownership,
  and a remaining URL-only legacy reference must be backfilled first. Write
  access to a replacement owner is required only when its attachment relation
  is actually changed; an already-attached, unchanged owner is only revalidated
  under lock.
- Folder and AI Document visibility are both required for a document node.
- Source and destination authorization, hidden descendants, direct File
  provenance synchronization, audit writes, and rollback are all enforced by
  the server even if an endpoint is called outside the Tree UI.

For the complete transaction, copy/delete, migration, and validation contract,
see [AI Document Tree](DOCUMENT_TREE.md).

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
    json={
        "file_url": upload["file_url"],
        "file_record": upload["name"],
        "folder": "Home/Policies",
        "knowledge_base": "General Knowledge",
    },
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
