# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Document intelligence operations.

Summarisation, classification, structured extraction and comparison. Long
documents are handled with a map-reduce strategy so content larger than the
model's context window is still processed accurately.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

from ai_fr_hg.ai.chunking import chunk_text, estimate_tokens
from ai_fr_hg.ai.engine import resolve_model, run_chat
from ai_fr_hg.ai.exceptions import HierarchicalReductionError
from ai_fr_hg.ai.validation import ValidationError as _ValidationError, validate_extraction

JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)

TYPE_MAP = {
	"String": "string",
	"Number": "number",
	"Integer": "integer",
	"Boolean": "boolean",
	"Date": "string",
	"Array": "array",
	"Object": "object",
}


def parse_json_response(text: str) -> dict | list | None:
	"""Best-effort extraction of a JSON value from a model response."""
	if not text:
		return None

	candidate = text.strip()
	if match := JSON_BLOCK.search(candidate):
		candidate = match.group(1).strip()

	try:
		return json.loads(candidate)
	except ValueError:
		pass

	# Fall back to the outermost balanced object or array in the text.
	for opener, closer in (("{", "}"), ("[", "]")):
		start = candidate.find(opener)
		end = candidate.rfind(closer)
		if start != -1 and end > start:
			try:
				return json.loads(candidate[start : end + 1])
			except ValueError:
				continue
	return None


def _context_budget(model_doc) -> int:
	"""Characters of input the model can comfortably accept in one call."""
	window = cint(model_doc.num_ctx_override) or cint(model_doc.context_window) or 8192
	# Reserve roughly a third of the window for the prompt and the answer.
	return max(int(window * 0.6) * 4, 2000)


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = (
	"Summarise the following text. Capture the key facts, decisions, figures and "
	"conclusions. Be faithful to the source and do not add information.\n\n"
	"{instructions}\n\nTEXT:\n{text}"
)

REDUCE_PROMPT = (
	"The following are summaries of consecutive sections of one document. "
	"Combine them into a single coherent summary without repetition.\n\n"
	"{instructions}\n\nSECTION SUMMARIES:\n{text}"
)


def summarize(
	text: str,
	model: str | None = None,
	instructions: str = "",
	max_words: int = 0,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> str:
	"""Summarise text, using map-reduce when it exceeds the context window."""
	if not (text or "").strip():
		return ""

	model_doc = resolve_model(model, "Chat")
	budget = _context_budget(model_doc)

	guidance = instructions or ""
	if max_words:
		guidance = f"{guidance}\nKeep the summary under {max_words} words.".strip()

	if len(text) <= budget:
		result = run_chat(
			[{"role": "user", "content": SUMMARY_PROMPT.format(instructions=guidance, text=text)}],
			model=model_doc.name,
			operation="Summarize",
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
		return result.content.strip()

	# Map: summarise each window, then reduce the partial summaries.
	windows = chunk_text(text, chunk_size=budget, chunk_overlap=200)
	partials = []
	for window in windows:
		result = run_chat(
			[
				{
					"role": "user",
					"content": SUMMARY_PROMPT.format(instructions=guidance, text=window.content),
				}
			],
			model=model_doc.name,
			operation="Summarize",
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
		partials.append(result.content.strip())

	# INT-03: hierarchical coverage-preserving reduction — never truncate tail summaries.
	# Provenance: each partial retains its source window index; intermediate reduces preserve ordering.
	def _hierarchical_reduce(summaries: list[str], level: int = 0) -> str:
		if len(summaries) == 1:
			return summaries[0]
		# Pack summaries into budget-respecting batches, then reduce each batch.
		batches: list[list[str]] = []
		cur: list[str] = []
		cur_len = 0
		for idx, s in enumerate(summaries):
			entry = f"[Section {idx+1}]\n{s}"
			if cur and cur_len + len(entry) + 2 > budget:
				batches.append(cur)
				cur = [entry]
				cur_len = len(entry)
			else:
				cur.append(entry)
				cur_len += len(entry) + 2
		if cur:
			batches.append(cur)
		if len(batches) == 1 and len("\n\n".join(batches[0])) <= budget:
			combined = "\n\n".join(batches[0])
			result = run_chat(
				[{"role": "user", "content": REDUCE_PROMPT.format(instructions=guidance, text=combined)}],
				model=model_doc.name,
				operation="Summarize",
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
			return result.content.strip()
			# INT-03: Bounded recursion — never silently discard to fit budget
		if level > 10:
			raise HierarchicalReductionError(
				f"Hierarchical reduction exceeded 10 levels ({len(summaries)} summaries, budget {budget}). "
				f"Input too large for safe reduction — failing explicitly rather than truncating."
			)
		next_level: list[str] = []
		for batch in batches:
			combined = "\n\n".join(batch)
			result = run_chat(
				[{"role": "user", "content": REDUCE_PROMPT.format(instructions=guidance, text=combined)}],
				model=model_doc.name,
				operation="Summarize",
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
			next_level.append(result.content.strip())
		return _hierarchical_reduce(next_level, level+1)

	return _hierarchical_reduce(partials)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = (
	"Classify the text into exactly one of these categories:\n{categories}\n\n"
	"{instructions}\n\n"
	'Respond with JSON only: {{"category": "<category>", "confidence": <0-100>, '
	'"reason": "<one short sentence>"}}\n\nTEXT:\n{text}'
)


def classify(
	text: str,
	categories: list[str],
	model: str | None = None,
	instructions: str = "",
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Assign one of `categories` to the text, with a confidence score."""
	if not categories:
		frappe.throw(_("Classification needs at least one category."))

	model_doc = resolve_model(model, "Chat")
	budget = _context_budget(model_doc)

	prompt = CLASSIFY_PROMPT.format(
		categories="\n".join(f"- {c}" for c in categories),
		instructions=instructions,
		text=text[:budget],
	)

	schema = {
		"type": "object",
		"properties": {
			"category": {"type": "string", "enum": list(categories)},
			"confidence": {"type": "number"},
			"reason": {"type": "string"},
		},
		"required": ["category"],
	}

	result = run_chat(
		[{"role": "user", "content": prompt}],
		model=model_doc.name,
		options={"temperature": 0, "json_mode": True},
		json_schema=schema,
		operation="Classify",
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

	parsed = parse_json_response(result.content) or {}
	category = parsed.get("category")

	# Guard against a model inventing a category outside the allowed set.
	if category not in categories:
		lowered = {c.lower(): c for c in categories}
		category = lowered.get(str(category).lower().strip()) if category else None
		if not category:
			for candidate in categories:
				if candidate.lower() in (result.content or "").lower():
					category = candidate
					break

	return {
		"category": category,
		"confidence": flt(parsed.get("confidence")),
		"reason": parsed.get("reason"),
		"raw": result.content,
	}


# ---------------------------------------------------------------------------
# Structured extraction
# ---------------------------------------------------------------------------


def build_json_schema(schema_doc) -> dict:
	"""Turn an `AI Extraction Schema` into a JSON Schema object."""
	properties: dict = {}
	required: list[str] = []

	for row in schema_doc.get("extraction_fields") or []:
		field: dict = {"type": TYPE_MAP.get(row.field_type, "string")}
		if row.description:
			field["description"] = row.description
		if row.field_type == "Date":
			field["description"] = f"{field.get('description', '')} Format: YYYY-MM-DD.".strip()
		if row.enum_values:
			values = [v.strip() for v in row.enum_values.replace(",", "\n").splitlines() if v.strip()]
			if values:
				field["enum"] = values
		if field["type"] == "array":
			field["items"] = {"type": "string"}
		properties[row.field_name] = field
		if row.required:
			required.append(row.field_name)

	schema = {"type": "object", "properties": properties}
	if required:
		schema["required"] = required
	if schema_doc.strict:
		schema["additionalProperties"] = False
	return schema


EXTRACT_PROMPT = (
	"Extract the requested fields from the text below.\n"
	"Use null for any field that is not present. Do not guess.\n\n"
	"{instructions}\n\nFIELDS:\n{fields}\n\nTEXT:\n{text}"
)


def extract_data(
	text: str,
	schema: str,
	model: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Extract structured data from text using an `AI Extraction Schema`."""
	schema_doc = frappe.get_cached_doc("AI Extraction Schema", schema)
	if not schema_doc.enabled:
		frappe.throw(_("Extraction Schema {0} is disabled.").format(schema))

	model_doc = resolve_model(model or schema_doc.model, "Chat")
	budget = _context_budget(model_doc)
	json_schema = build_json_schema(schema_doc)

	field_lines = []
	for row in schema_doc.get("extraction_fields") or []:
		line = f"- {row.field_name} ({row.field_type})"
		if row.label:
			line += f" - {row.label}"
		if row.description:
			line += f": {row.description}"
		if row.required:
			line += " [required]"
		field_lines.append(line)

	prompt = EXTRACT_PROMPT.format(
		instructions=schema_doc.instructions or "",
		fields="\n".join(field_lines),
		text=text[:budget],
	)

	result = run_chat(
		[{"role": "user", "content": prompt}],
		model=model_doc.name,
		options={"temperature": 0, "json_mode": True},
		json_schema=json_schema,
		operation="Extract",
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

	data = parse_json_response(result.content)
	if data is None:
		raise _ValidationError("Model did not return valid JSON.", errors=[{"field":"","code":"malformed_json","message":"Model output is not valid JSON","severity":"error"}], provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict), "raw_preview": (result.content or "")[:500]})
	if not isinstance(data, dict):
		raise _ValidationError("Model did not return a JSON object.", errors=[{"field":"","code":"type","message":f"Expected object got {type(data).__name__}","severity":"error"}], provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict), "raw_preview": (result.content or "")[:500]})
	# INT-02 canonical validation BEFORE coercion/persistence — single authority
	ok, errors = validate_extraction(data, schema_doc)
	if not ok:
		raise _ValidationError(f"Structured output failed schema validation: {errors[0]['message'] if errors else 'invalid'}", errors=errors, provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict), "payload_bytes": len((result.content or "").encode("utf-8")), "field_count": len(data)})
	coerced = _coerce_types(data, schema_doc)
	# Re-validate after coercion to ensure persisted form is still valid
	ok2, errors2 = validate_extraction(coerced, schema_doc)
	if not ok2:
		raise _ValidationError(f"Coerced output failed validation: {errors2[0]['message']}", errors=errors2, provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict)})
	return coerced


def _coerce_types(data: dict, schema_doc) -> dict:
	"""Coerce extracted values to the declared field types."""
	from frappe.utils import getdate

	coerced: dict = {}
	for row in schema_doc.get("extraction_fields") or []:
		value = data.get(row.field_name)
		if value in (None, "", "null", "N/A"):
			coerced[row.field_name] = None
			continue

		try:
			if row.field_type == "Number":
				coerced[row.field_name] = flt(value)
			elif row.field_type == "Integer":
				coerced[row.field_name] = cint(value)
			elif row.field_type == "Boolean":
				coerced[row.field_name] = (
					value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
				)
			elif row.field_type == "Date":
				coerced[row.field_name] = str(getdate(value))
			elif row.field_type == "Array":
				coerced[row.field_name] = value if isinstance(value, list) else [value]
			else:
				coerced[row.field_name] = value
		except Exception:
			coerced[row.field_name] = value
	return coerced


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

COMPARE_PROMPT = (
	"Compare the two documents below.\n\n{instructions}\n\n"
	"Report:\n"
	"1. Key differences\n"
	"2. Key similarities\n"
	"3. Content present in A but missing from B\n"
	"4. Content present in B but missing from A\n\n"
	"=== DOCUMENT A: {title_a} ===\n{text_a}\n\n"
	"=== DOCUMENT B: {title_b} ===\n{text_b}"
)


def compare_documents(
	document_a: str, document_b: str, model: str | None = None, instructions: str = ""
) -> dict:
	"""Compare two `AI Document` records and describe their differences."""
	doc_a = frappe.get_doc("AI Document", document_a)
	doc_b = frappe.get_doc("AI Document", document_b)
	doc_a.check_permission("read")
	doc_b.check_permission("read")

	model_doc = resolve_model(model, "Chat")
	budget = _context_budget(model_doc) // 2

	prompt = COMPARE_PROMPT.format(
		instructions=instructions,
		title_a=doc_a.title,
		text_a=(doc_a.content or "")[:budget],
		title_b=doc_b.title,
		text_b=(doc_b.content or "")[:budget],
	)

	result = run_chat(
		[{"role": "user", "content": prompt}],
		model=model_doc.name,
		operation="Compare",
		reference_doctype="AI Document",
		reference_name=document_a,
	)

	return {
		"document_a": document_a,
		"document_b": document_b,
		"comparison": result.content,
		"model": model_doc.name,
	}


def render_prompt_template(template: str, context: dict) -> dict:
	"""Render an `AI Prompt Template` against a context dictionary."""
	from frappe.utils.jinja import render_template

	template_doc = frappe.get_cached_doc("AI Prompt Template", template)
	template_doc.check_permission("read")
	if not template_doc.enabled:
		frappe.throw(_("Prompt Template {0} is disabled.").format(template))

	merged = dict(context or {})
	for row in template_doc.get("variables") or []:
		if row.variable not in merged and row.default_value is not None:
			merged[row.variable] = row.default_value
		if row.required and merged.get(row.variable) in (None, ""):
			frappe.throw(_("Prompt variable {0} is required.").format(row.variable))

	return {
		# Prompt templates are manager-authored and rendered by Frappe's sandboxed Jinja environment.
		"system_prompt": render_template(template_doc.system_prompt or "", merged),  # nosemgrep: frappe-ssti
		"user_prompt": render_template(template_doc.user_prompt or "", merged),  # nosemgrep: frappe-ssti
		"model": template_doc.model,
		"output_format": template_doc.output_format,
		"json_schema": json.loads(template_doc.json_schema) if template_doc.json_schema else None,
	}


def run_prompt_template(
	template: str,
	context: dict | None = None,
	model: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Render and execute a prompt template."""
	rendered = render_prompt_template(template, context or {})

	messages = []
	if rendered["system_prompt"]:
		messages.append({"role": "system", "content": rendered["system_prompt"]})
	messages.append({"role": "user", "content": rendered["user_prompt"]})

	options = {"json_mode": True} if rendered["output_format"] == "JSON" else None
	result = run_chat(
		messages,
		model=model or rendered["model"],
		options=options,
		json_schema=rendered["json_schema"],
		operation="Chat",
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

	output = result.content
	if rendered["output_format"] == "JSON":
		output = parse_json_response(result.content)

	return {
		"output": output,
		"raw": result.content,
		"total_tokens": result.total_tokens,
		"duration_ms": result.duration_ms,
	}
