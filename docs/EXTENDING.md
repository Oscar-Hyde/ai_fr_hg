# Extending the platform

Four hooks let any Frappe app add capability without modifying this one. Each
maps a name to a dotted path, and the platform merges them into its governed
registry at runtime.

```python
# your_app/hooks.py

ai_providers = {"My Runtime": "your_app.ai.providers.MyRuntimeProvider"}
ai_document_readers = {"dwg": "your_app.ai.readers.DWGReader"}
ai_tools = {"lookup_customer": "your_app.ai.tools.lookup_customer"}
ai_pipeline_methods = {"enrich_erp": "your_app.ai.steps.enrich_with_erp_data"}
```

---

## Custom AI providers

Subclass `BaseProvider`. Only `chat`, `embed`, `list_models` and `health_check`
are required; the base class supplies HTTP handling, the local-only guard,
timeouts and error translation.

```python
# your_app/ai/providers.py
import time

from ai_fr_hg.ai.providers.base import (
    BaseProvider,
    ChatMessage,
    CompletionResult,
    HealthStatus,
    ModelInfo,
)


class MyRuntimeProvider(BaseProvider):
    provider_type = "My Runtime"
    supports_streaming = True
    supports_tools = False
    supports_embeddings = True
    supports_model_pull = False

    def chat(self, messages, model, options=None, tools=None, json_schema=None):
        started = time.monotonic()
        response = self.request(
            "POST",
            "/generate",
            {
                "model": model,
                "messages": [m.as_dict() for m in messages],
                **self.build_options(options),
            },
        )
        data = self.parse_json(response)

        return CompletionResult(
            content=data["text"],
            prompt_tokens=data.get("input_tokens", 0),
            completion_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("input_tokens", 0) + data.get("output_tokens", 0),
            duration_ms=int((time.monotonic() - started) * 1000),
            model=model,
            raw=data,
        )

    def embed(self, texts, model, options=None):
        data = self.parse_json(
            self.request("POST", "/embed", {"model": model, "inputs": texts})
        )
        return data["vectors"]

    def list_models(self):
        data = self.parse_json(self.request("GET", "/models"))
        return [
            ModelInfo(name=m["id"], context_window=m.get("ctx", 8192))
            for m in data["models"]
        ]

    def health_check(self):
        started = time.monotonic()
        try:
            models = self.list_models()
            return HealthStatus(
                status="Online",
                latency_ms=int((time.monotonic() - started) * 1000),
                available_models=len(models),
            )
        except Exception as exc:
            return HealthStatus(status="Offline", error=str(exc)[:500])
```

Register it, then create an `AI Provider` record with **Provider Type** set to
`My Runtime`.

For a one-off adapter without a hook, set Provider Type to `Custom` and put the
dotted path in the **Adapter Path** field.

### The contract

| Member | Purpose |
| --- | --- |
| `provider_type` | Must match the value used in the hook and the DocType field. |
| `supports_streaming` / `supports_tools` / `supports_embeddings` / `supports_model_pull` | Advertised capabilities; the platform will not call what you do not support. |
| `chat(...) -> CompletionResult` | Required. Tool calls go in `CompletionResult.tool_calls` as `{"id", "name", "arguments": dict}`. |
| `embed(texts, model, options) -> list[list[float]]` | Required for search. |
| `list_models() -> list[ModelInfo]` | Powers automatic discovery. |
| `health_check() -> HealthStatus` | Powers monitoring and failover. |
| `self.request(method, path, payload)` | Handles URLs, headers, timeouts, TLS and the local-only guard. |
| `self.build_options(overrides)` | Merges model parameters with per-request overrides. |

---

## Custom document readers

Subclass `BaseReader` and declare the extensions it handles.

```python
# your_app/ai/readers.py
from ai_fr_hg.ai.readers.base import BaseReader, ReadResult


class DWGReader(BaseReader):
    label = "AutoCAD Drawing"
    extensions = ("dwg", "dxf")

    def read(self, content: bytes, filename: str) -> ReadResult:
        # `require` raises MissingDependency with an install command,
        # which the UI surfaces to the user verbatim.
        ezdxf = self.require("ezdxf", "pip install ezdxf")

        import io

        doc = ezdxf.read(io.BytesIO(content))
        lines = [
            entity.dxf.text
            for entity in doc.modelspace()
            if entity.dxftype() in ("TEXT", "MTEXT")
        ]

        return ReadResult(
            text="\n".join(lines),
            page_count=1,
            metadata={"format": "dwg", "layers": len(doc.layers)},
        )
```

Once registered, the format works everywhere: upload, ingestion, pipelines,
the supported-formats dialog and automation rules. Nothing else changes.

`ReadResult` carries `text`, `metadata`, `page_count`, `pages` and `warnings`.
Use `warnings` for partial extraction — a scanned page with no text layer, a
password-protected section — rather than raising, so the rest of the document
still indexes.

For paginated formats, emit `[Page N]` markers in the text; the chunker reads
them and records page numbers on each chunk, which then appear in citations.

---

## Custom tools

A tool handler is an ordinary function. Keyword arguments correspond to the
parameters declared on the `AI Tool` record, and the return value must be
JSON-serialisable.

```python
# your_app/ai/tools.py
import frappe


def lookup_customer(name: str, include_orders: bool = False) -> dict:
    """Find a customer and optionally their recent orders."""
    # Always check permissions as the calling user. Never elevate.
    frappe.has_permission("Customer", "read", throw=True)

    customer = frappe.db.get_value(
        "Customer",
        {"customer_name": ["like", f"%{name}%"]},
        ["name", "customer_name", "customer_group", "territory"],
        as_dict=True,
    )
    if not customer:
        return {"found": False, "message": f"No customer matching '{name}'."}

    result = {"found": True, **customer}

    if include_orders:
        result["recent_orders"] = frappe.get_list(
            "Sales Order",
            filters={"customer": customer.name},
            fields=["name", "transaction_date", "grand_total", "status"],
            order_by="transaction_date desc",
            limit_page_length=5,
        )

    return result
```

Then create an `AI Tool` record:

- **Tool Name**: `lookup_customer` (lowercase snake_case — runtimes require it)
- **Tool Type**: `Builtin`
- **Handler**: `lookup_customer`
- **Description**: written for the model, not for a human. State plainly when
  the tool should be used; this text is the model's only guide.
- **Parameters**: `name` (String, required), `include_orders` (Boolean)
- **Read Only**: tick it if the tool never writes, to skip the approval gate.

Finally add the tool to an agent's Tools table.

### Writing descriptions the model can act on

The description is the entire specification the model sees. Compare:

> Gets customer data.

against:

> Look up a customer by name or partial name. Returns their customer group and
> territory. Set include_orders to true when the user asks about recent
> purchases, orders or spending.

The second reliably triggers at the right moment; the first does not.

### Tool types

| Type | Behaviour |
| --- | --- |
| `Builtin` | Calls a registered handler function. |
| `Server Method` | Calls a whitelisted dotted path. Non-whitelisted methods are refused. |
| `DocType Query` | Generic read against a DocType, filtered to valid fields, capped at 100 rows. |
| `DocType Action` | Create or update a record. Always subject to the approval gate. |
| `Report` | Runs a query report and returns columns and rows. |
| `Pipeline` | Runs an AI Pipeline synchronously and returns its output. |

---

## Custom pipeline steps

For logic that does not fit the built-in step types, use a `Custom Method` step.

```python
# your_app/ai/steps.py

def enrich_with_erp_data(context, step, config):
    """Pipeline step: attach ERP context to the run.

    context - the shared run dictionary; earlier steps wrote their output here
    step    - the AI Pipeline Step row, for model, input_field, output_field
    config  - the step's parsed JSON configuration
    """
    import frappe

    document = context.get("document")
    supplier = (context.get("extracted") or {}).get("supplier_name")
    if not supplier:
        return {"matched": False}

    match = frappe.db.get_value(
        "Supplier", {"supplier_name": ["like", f"%{supplier}%"]}, "name"
    )
    return {"matched": bool(match), "supplier": match, "document": document}
```

Register the dotted path in the extension app's `ai_pipeline_methods` hook,
then set the step's **Method** to
`your_app.ai.steps.enrich_with_erp_data`. Unregistered methods are rejected at
both configuration and execution time. The callable runs as the user who
started the Pipeline and must use permission-aware Frappe APIs; it must never
elevate privileges.

The return value is written into the run context under the step's **Output
Field**, where later steps read it via their **Input Field**.

---

## Extending AI Document organization

The AI Document Tree is intentionally not an open client-side persistence hook.
Its supported boundary is:

```text
frappe.treeview_settings["AI Document"]
  -> ai_fr_hg.api.document_tree
    -> ai_fr_hg.ai.document_tree
      -> native File / AI Document / existing services
```

Read the complete contract in [AI Document Tree](DOCUMENT_TREE.md) before adding
an action.

### Rules for a new tree operation

1. Put only prompts, routing, labels, and refresh behavior in
   `public/js/ai_document_tree.js`. Never authorize or compute canonical paths
   in JavaScript.
2. Add a narrow whitelisted facade function in `api/document_tree.py`. Validate
   JSON shape and request bounds there, then delegate immediately.
3. Implement behavior in `ai/document_tree.py` or an existing authoritative
   service. Do not import the API package from the service layer.
4. Resolve mixed IDs with `split_node_value`; do not infer identity from labels.
5. Use native `File` folders and `AI Document.folder`. Do not add a second tree,
   custom folder table, client cache of relationships, or manual `lft`/`rgt`
   writes.
6. Treat `AI Document.name` and `source_file_record` as stable identities. URLs,
   names, and hashes may be shared and are not record identifiers.
7. Check every source, destination, physical File, and affected descendant with
   Frappe permissions. Internal recursive discovery must not silently omit
   hidden rows; denial must fail without naming them.
8. Use the tree savepoint helper, deterministic parent → File → AI Document lock
   order, post-lock re-discovery, `expected_modified`/fingerprint checks, and
   fail-closed audit writes. Do not commit in a request-path service.
9. Keep reads lazy and bounded. Add opaque continuation rather than loading a
   complete folder or recursively expanding the browser tree.
10. Preserve existing readers, processing runs, chunks, embeddings, retrieval,
    attachment retention, Knowledge Base statistics, and lifecycle hooks.

### Creating a document in a folder

Use the canonical ingestion/API path and propagate exact upload identity:

```python
from ai_fr_hg.ai.ingestion import ingest_file

name = ingest_file(
    file_url=uploaded.file_url,
    file_record=uploaded.name,
    folder="Home/Engineering",
    knowledge_base="Engineering Knowledge",
    title=uploaded.file_name,
    enqueue_job=True,
)
```

Do not insert a URL-only AI Document when the exact `File.name` is available.
If more than one File row uses a legacy URL, runtime resolution deliberately
fails until the caller supplies the stable record.

For text documents, ordinary `ingest_text`/`add_text` behavior remains
canonical. Placement is organization metadata; it must not create an alternate
chunk/index path.

### Calling organization behavior from Python

Server code may call the service layer directly while running under an
intentional Frappe user/transaction:

```python
from ai_fr_hg.ai.document_tree import copy_document, move_document

moved = move_document(
    "AIDOC-2026-00008",
    "Home/Archive",
    expected_modified="2026-08-18 12:02:00.000000",
)
copy = copy_document("AIDOC-2026-00008", "Home/Working Copies")
```

Do not call private worker functions directly. Do not set process-local copy or
ingestion suppression contexts outside the canonical service; those contexts
protect lifecycle assembly and are not extension authorization mechanisms.

### Required tests

A new organization action needs coverage for:

- root and nested lifecycle behavior;
- case/Unicode name collision and deterministic copy suffixes;
- source/destination/descendant denial without disclosure;
- rollback of data and audit on a mid-operation failure;
- stale timestamps, late descendants, and concurrent source changes;
- direct File hooks and stable/ambiguous source identity;
- large lazy pages or background threshold behavior;
- chunks, processing/index state, retrieval, and native attachment retention;
- UI capability/action visibility plus forged API authorization.

Run focused pure tests and the live Bench suite on the supported MariaDB and
PostgreSQL configurations. The repository's current implementation has pure
coverage, but browser, asset-build, live Frappe, load/concurrency, and full
processing/retrieval regressions remain deployment release checks.

---

## Adding AI to your own forms

`frappe.ai` is available on every Desk page.

```javascript
frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        // Adds a ready-made "Ask AI" button.
        frappe.ai.add_form_button(frm);

        // Or drive it yourself.
        frm.add_custom_button(__("Check Against Policy"), async () => {
            await frappe.ai.ask(
                `Does this order comply with our discount policy?\n\n` +
                `Order ${frm.doc.name}, total ${frm.doc.grand_total}, ` +
                `discount ${frm.doc.discount_amount}`,
                { knowledge_bases: ["Company Policies"] }
            );
        });
    },
});
```

From Python, call the service layer directly:

```python
from ai_fr_hg.ai.agent import run_agent_turn
from ai_fr_hg.ai.intelligence import classify, extract_data, summarize
from ai_fr_hg.ai.knowledge import retrieve

answer = run_agent_turn("What is our refund window?", save_messages=False)
summary = summarize(long_text, max_words=200)
category = classify(text, categories=["Invoice", "Contract", "Report"])
fields = extract_data(text, schema="Invoice Data")
passages = retrieve("refund policy", top_k=5)
```

From Jinja, in a print format or notification:

```jinja
{{ get_ai_summary("Sales Order", doc.name) }}

{% for hit in ai_search("refund policy", limit=3) %}
  <p>{{ hit.content }}</p>
{% endfor %}
```

---

## Automating without writing code

Most automation needs no Python at all. Create an **AI Automation Rule**:

- **Document Type**: `Purchase Invoice`
- **Event**: `after_insert`
- **Condition**: `doc.grand_total > 10000` (optional, safely evaluated)
- **Action**: `Extract Data` with an extraction schema
- **Target Field**: where the result is written back

The rule runs on a background worker, records its own success and failure
counts, and can be tested against an existing document from the form before
you enable it.
