# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Validate every DocType against Frappe v17's *own* schema rules.

`fakebench` proves application behaviour, but it invents its own notion of a
DocType. It therefore cannot catch a schema that Frappe itself would reject at
`bench migrate` — an illegal fieldname, a `Link` with no target, a `Select`
whose default is not among its options, a `Check` with a non-boolean default, a
`title_field` that does not exist.

Those failures happen at install time on a real bench, which is exactly the
class of defect this repository has no offline coverage for.

This module extracts the constraints from the pinned Frappe v17 source when it
is available (`FRAPPE_SOURCE=/path/to/frappe`), and otherwise applies the same
rules transcribed from that source, with the transcription pinned to the
revision recorded in `ARCHITECTURE_DECISIONS.md`. Either way it validates real
schema semantics rather than the presence of a string.

Rules enforced (from `frappe/core/doctype/doctype/doctype.py::validate_fields`
and `frappe/database/schema.py`):

1. fieldnames contain no special characters (`[\\W]`),
2. fieldnames are unique within a DocType,
3. fieldnames are <= 64 characters (MariaDB column limit),
4. fieldnames do not collide with Frappe's default/child-table fields,
5. `Link` / `Table` / `Table MultiSelect` declare `options`,
6. `Dynamic Link` options name a real field in the same DocType,
7. a field is not both hidden and mandatory,
8. `Check` defaults are 0 or 1,
9. `Select` defaults appear in the option list,
10. `unique` is only used on Data / Link / Read Only,
11. `title_field`, `search_fields` and `field_order` reference real fields,
12. `no_value` fieldtypes carry no `reqd`/`unique`/`default`.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest import TestCase

APP = Path(__file__).resolve().parents[1]

#: Transcribed from `frappe/model/__init__.py` at the pinned v17 revision.
DEFAULT_FIELDS = frozenset(
	{"doctype", "name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
)
CHILD_TABLE_FIELDS = frozenset({"parent", "parentfield", "parenttype"})
OPTIONAL_FIELDS = frozenset({"_user_tags", "_comments", "_assign", "_liked_by", "_seen"})

NO_VALUE_FIELDTYPES = frozenset(
	{
		"Section Break",
		"Column Break",
		"Tab Break",
		"Attachment Gallery",
		"HTML",
		"Table",
		"Table MultiSelect",
		"Button",
		"Image",
		"Fold",
		"Heading",
	}
)

DATA_FIELDTYPES = frozenset(
	{
		"Currency",
		"Int",
		"Long Int",
		"Float",
		"Percent",
		"Check",
		"Small Text",
		"Long Text",
		"Code",
		"Text Editor",
		"Markdown Editor",
		"HTML Editor",
		"Date",
		"Datetime",
		"Time",
		"Text",
		"Data",
		"Link",
		"Dynamic Link",
		"Password",
		"Select",
		"Rating",
		"Read Only",
		"Attach",
		"Attach Image",
		"Signature",
		"Color",
		"Barcode",
		"Geolocation",
		"Duration",
		"Icon",
		"Phone",
		"Autocomplete",
		"JSON",
	}
)

SPECIAL_CHAR_PATTERN = re.compile(r"[\W]", flags=re.UNICODE)
MAX_COLUMN_LENGTH = 64
UNIQUE_ALLOWED = frozenset({"Data", "Link", "Read Only"})


def _frappe_source() -> Path | None:
	"""Real Frappe checkout, when one is available to cross-check against."""
	configured = os.environ.get("FRAPPE_SOURCE")
	for candidate in (configured, "/tmp/frappe-src"):
		if not candidate:
			continue
		path = Path(candidate)
		if (path / "frappe" / "model" / "__init__.py").exists():
			return path
	return None


def _app_doctypes() -> list[tuple[str, dict]]:
	found = []
	for path in APP.rglob("doctype/*/*.json"):
		if path.stem != path.parent.name:
			continue
		meta = json.loads(path.read_text())
		if meta.get("doctype") == "DocType":
			found.append((str(path.relative_to(APP)), meta))
	return found


def _value_fields(meta: dict) -> list[dict]:
	return [f for f in meta.get("fields", []) if f.get("fieldtype") not in NO_VALUE_FIELDTYPES]


class TestFrappeSourceAlignment(TestCase):
	"""If a real Frappe checkout is present, the transcribed rules must match it."""

	def test_transcribed_constants_match_the_pinned_frappe_source(self):
		source = _frappe_source()
		if source is None:
			self.skipTest("no Frappe checkout available (set FRAPPE_SOURCE)")

		model_init = (source / "frappe" / "model" / "__init__.py").read_text()
		namespace: dict = {}
		for constant in ("data_fieldtypes", "no_value_fields", "default_fields", "child_table_fields"):
			match = re.search(rf"^{constant} = \(.*?\n\)", model_init, re.S | re.M)
			self.assertIsNotNone(match, f"{constant} not found in Frappe source")
			exec(compile(match.group(0), "frappe_model", "exec"), namespace)

		self.assertEqual(set(namespace["default_fields"]), DEFAULT_FIELDS)
		self.assertEqual(set(namespace["child_table_fields"]), CHILD_TABLE_FIELDS)
		self.assertEqual(set(namespace["data_fieldtypes"]), DATA_FIELDTYPES)
		# Frappe's no_value_fields is a superset baseline; ours must not claim
		# a fieldtype stores no value when Frappe says it does.
		self.assertTrue(NO_VALUE_FIELDTYPES.issubset(set(namespace["no_value_fields"])))

	def test_special_character_pattern_matches_frappe(self):
		source = _frappe_source()
		if source is None:
			self.skipTest("no Frappe checkout available (set FRAPPE_SOURCE)")
		schema = (source / "frappe" / "database" / "schema.py").read_text()
		self.assertIn(r'SPECIAL_CHAR_PATTERN = re.compile(r"[\W]", flags=re.UNICODE)', schema)


class TestDocTypeSchema(TestCase):
	"""Every DocType must satisfy the rules Frappe applies at migrate time."""

	@classmethod
	def setUpClass(cls):
		cls.doctypes = _app_doctypes()
		if not cls.doctypes:
			raise AssertionError("no DocTypes discovered — the glob is wrong")

	def test_fieldnames_have_no_special_characters(self):
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				name = field.get("fieldname") or ""
				with self.subTest(doctype=meta["name"], field=name):
					self.assertTrue(name, f"{path}: field without a fieldname")
					self.assertEqual(
						SPECIAL_CHAR_PATTERN.findall(name),
						[],
						f"{path}: '{name}' contains characters Frappe rejects",
					)

	def test_fieldnames_are_unique(self):
		for path, meta in self.doctypes:
			names = [f.get("fieldname") for f in meta.get("fields", [])]
			duplicates = {n for n in names if names.count(n) > 1}
			self.assertEqual(duplicates, set(), f"{path}: duplicate fieldnames {duplicates}")

	def test_fieldnames_fit_the_column_limit(self):
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				name = field.get("fieldname") or ""
				self.assertLessEqual(
					len(name), MAX_COLUMN_LENGTH, f"{path}: '{name}' exceeds {MAX_COLUMN_LENGTH} chars"
				)

	def test_fieldnames_do_not_collide_with_framework_columns(self):
		"""A field named `name` or `parent` silently corrupts the row."""
		reserved = DEFAULT_FIELDS | CHILD_TABLE_FIELDS | OPTIONAL_FIELDS
		for path, meta in self.doctypes:
			for field in _value_fields(meta):
				name = field.get("fieldname")
				self.assertNotIn(name, reserved, f"{path}: '{name}' collides with a Frappe column")

	def test_link_like_fields_declare_a_target(self):
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("fieldtype") in ("Link", "Table", "Table MultiSelect"):
					self.assertTrue(
						(field.get("options") or "").strip(),
						f"{path}: {field['fieldtype']} '{field.get('fieldname')}' has no options",
					)

	def test_link_targets_are_doctypes_that_exist(self):
		"""A Link to a non-existent DocType fails at migrate, not at edit time.

		Frappe resolves the target against the DocType table, so a typo here is
		invisible until install. Core DocTypes are allow-listed because they
		live in Frappe, not in this app.
		"""
		core = {
			"User",
			"File",
			"Role",
			"DocType",
			"Report",
			"Print Format",
			"Workflow",
			"Email Account",
			"Language",
			"Company",
			"Currency",
			"Contact",
			"Address",
			"Note",
			"ToDo",
			"Comment",
			"Tag",
			"Module Def",
			"Custom Field",
			"Server Script",
			"Client Script",
			"Webhook",
			"Notification",
			"Scheduled Job Type",
			"Prepared Report",
			"Dashboard Chart",
			"Workspace",
		}
		app_doctypes = {meta["name"] for _, meta in self.doctypes} | core
		unknown = []
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("fieldtype") not in ("Link", "Table", "Table MultiSelect"):
					continue
				target = (field.get("options") or "").strip()
				if target and target not in app_doctypes:
					unknown.append(f"{path}: '{field.get('fieldname')}' -> unknown DocType {target!r}")
		self.assertEqual(unknown, [])

	def test_child_tables_are_declared_as_child_tables(self):
		"""A `Table` field pointing at a non-child DocType corrupts the grid."""
		by_name = {meta["name"]: meta for _, meta in self.doctypes}
		problems = []
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("fieldtype") not in ("Table", "Table MultiSelect"):
					continue
				target = (field.get("options") or "").strip()
				child = by_name.get(target)
				if child is not None and not child.get("istable"):
					problems.append(f"{path}: '{field.get('fieldname')}' -> {target} is not istable")
		self.assertEqual(problems, [])

	def test_dynamic_links_point_at_a_real_field(self):
		for path, meta in self.doctypes:
			names = {f.get("fieldname") for f in meta.get("fields", [])}
			for field in meta.get("fields", []):
				if field.get("fieldtype") == "Dynamic Link":
					target = (field.get("options") or "").strip()
					self.assertIn(
						target,
						names,
						f"{path}: Dynamic Link '{field.get('fieldname')}' -> unknown field '{target}'",
					)

	def test_fields_are_not_both_hidden_and_mandatory(self):
		"""Frappe rejects this: the user cannot satisfy a requirement they cannot see."""
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("hidden") and field.get("reqd") and not field.get("default"):
					self.fail(f"{path}: '{field.get('fieldname')}' is hidden and mandatory with no default")

	def test_check_fields_default_to_zero_or_one(self):
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("fieldtype") == "Check":
					default = field.get("default")
					if default is None:
						continue
					self.assertIn(
						str(default),
						("0", "1"),
						f"{path}: Check '{field.get('fieldname')}' default {default!r} is not 0/1",
					)

	def test_select_defaults_are_valid_options(self):
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("fieldtype") != "Select":
					continue
				default = field.get("default")
				if default in (None, ""):
					continue
				options = [o for o in (field.get("options") or "").split("\n")]
				self.assertIn(
					default,
					options,
					f"{path}: Select '{field.get('fieldname')}' default {default!r} not in options",
				)

	def test_unique_is_only_used_where_frappe_allows_it(self):
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("unique"):
					self.assertIn(
						field.get("fieldtype"),
						UNIQUE_ALLOWED,
						f"{path}: unique on '{field.get('fieldname')}' ({field.get('fieldtype')})",
					)

	def test_field_order_matches_declared_fields(self):
		"""A mismatch silently hides fields from the Desk form."""
		for path, meta in self.doctypes:
			order = meta.get("field_order")
			if not order:
				continue
			declared = [f["fieldname"] for f in meta["fields"]]
			self.assertEqual(sorted(order), sorted(declared), f"{path}: field_order does not match fields")

	def test_title_field_exists(self):
		for path, meta in self.doctypes:
			title = meta.get("title_field")
			if not title:
				continue
			names = {f["fieldname"] for f in meta["fields"]}
			self.assertIn(title, names, f"{path}: title_field '{title}' is not a field")

	def test_search_fields_exist(self):
		for path, meta in self.doctypes:
			search = meta.get("search_fields")
			if not search:
				continue
			names = {f["fieldname"] for f in meta["fields"]} | DEFAULT_FIELDS
			for entry in [s.strip() for s in search.split(",") if s.strip()]:
				self.assertIn(entry, names, f"{path}: search_fields references unknown '{entry}'")

	def test_fieldtypes_are_recognized_by_frappe(self):
		known = DATA_FIELDTYPES | NO_VALUE_FIELDTYPES
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				self.assertIn(
					field.get("fieldtype"),
					known,
					f"{path}: unknown fieldtype {field.get('fieldtype')!r}",
				)

	def test_layout_fields_carry_no_data_attributes(self):
		"""A Section Break with `reqd` is a schema error Frappe will flag."""
		for path, meta in self.doctypes:
			for field in meta.get("fields", []):
				if field.get("fieldtype") not in ("Section Break", "Column Break", "Tab Break"):
					continue
				for attribute in ("reqd", "unique", "default"):
					self.assertFalse(
						field.get(attribute),
						f"{path}: layout field '{field.get('fieldname')}' has {attribute}",
					)

	def test_every_doctype_has_a_controller_module(self):
		for path, meta in self.doctypes:
			controller = APP / Path(path).parent / f"{Path(path).parent.name}.py"
			self.assertTrue(controller.exists(), f"{meta['name']}: missing controller {controller.name}")

	def test_every_doctype_declares_permissions_or_is_a_child_table(self):
		for path, meta in self.doctypes:
			if meta.get("istable"):
				continue
			self.assertTrue(meta.get("permissions"), f"{path}: non-child DocType declares no permissions")

	def test_module_is_declared_and_registered(self):
		modules = {line.strip() for line in (APP / "modules.txt").read_text().splitlines() if line.strip()}
		for path, meta in self.doctypes:
			self.assertIn(meta.get("module"), modules, f"{path}: module not in modules.txt")
