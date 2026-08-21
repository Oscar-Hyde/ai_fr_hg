# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""An in-memory Frappe substitute that *executes* application code.

Why this exists
---------------
Most of this repository's offline coverage either (a) tests pure functions, or
(b) asserts that a string appears in a source file. The second kind is
tautological: it passes when the behaviour is broken and fails when the code is
correctly refactored. It cannot answer "does this work?".

A real bench (MariaDB + Redis + Frappe v17) is not installable in this
environment, so the honest alternative is a substitute that implements enough
of Frappe's *observable semantics* for the application's own functions to run
unmodified:

* documents with ``insert`` / ``db_set`` / ``validate`` / controller dispatch,
* a table-backed ``frappe.db`` with filters, ordering, paging and deletes,
* ``get_all`` / ``get_list`` / ``get_value`` / ``set_value`` / ``count``,
* single DocTypes (settings), savepoints, commit and rollback,
* permission hooks, ``session.user``, and audit-visible side effects.

What it is not
--------------
It is **not** a database. It does not prove SQL correctness, InnoDB isolation,
index behaviour, concurrency, or migrations. Anything depending on those stays
Phase 7 bench work, and tests written against this harness must not claim
otherwise. What it *does* prove is that the application's logic, control flow,
state transitions, and error handling behave as specified — which the
source-grep tests never did.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import types
from dataclasses import dataclass, field


class FrappeException(Exception):
	pass


class ValidationError(FrappeException):
	pass


class PermissionError_(FrappeException):
	pass


class DoesNotExistError(FrappeException):
	pass


class DuplicateEntryError(FrappeException):
	pass


def _matches(row: dict, filters) -> bool:
	"""Evaluate Frappe's filter dialects against one row."""
	if not filters:
		return True
	if isinstance(filters, list):
		return all(_matches_one(row, f[0], f[1], f[2]) for f in filters)
	for field_name, condition in filters.items():
		if isinstance(condition, list | tuple) and len(condition) == 2:
			if not _matches_one(row, field_name, condition[0], condition[1]):
				return False
		elif row.get(field_name) != condition:
			return False
	return True


def _like_matches(pattern: str, actual: str) -> bool:
	"""Model MariaDB's LIKE, including backslash escaping of the wildcards.

	`%` and `_` are wildcards; `\\%` and `\\_` are the literal characters. A
	naive translation that ignores the escapes would make `Home/A\\_1/%` match
	`Home/AX1/child`, which is exactly the RET-07 sibling-prefix bug this
	harness is used to test for — the fake would silently pass code that a
	real database rejects.
	"""
	regex = []
	index = 0
	while index < len(pattern):
		char = pattern[index]
		if char == "\\" and index + 1 < len(pattern):
			regex.append(re.escape(pattern[index + 1]))
			index += 2
			continue
		if char == "%":
			regex.append(".*")
		elif char == "_":
			regex.append(".")
		else:
			regex.append(re.escape(char))
		index += 1
	return re.fullmatch("".join(regex), actual, re.IGNORECASE | re.DOTALL) is not None


def _matches_one(row: dict, field_name: str, operator: str, value) -> bool:
	actual = row.get(field_name)
	if operator in ("=", "=="):
		return actual == value
	if operator == "!=":
		return actual != value
	if operator == "in":
		return actual in value
	if operator == "not in":
		return actual not in value
	if operator == "<":
		return actual is not None and actual < value
	if operator == ">":
		return actual is not None and actual > value
	if operator == "<=":
		return actual is not None and actual <= value
	if operator == ">=":
		return actual is not None and actual >= value
	if operator == "like":
		return _like_matches(str(value), str(actual or ""))
	if operator == "is":
		return (actual in (None, "")) if value == "not set" else (actual not in (None, ""))
	raise AssertionError(f"fakebench: unsupported operator {operator!r}")


class _Row(dict):
	"""Dict that also allows attribute access, like Frappe's `_dict`."""

	def __getattr__(self, item):
		try:
			return self[item]
		except KeyError as exc:
			raise AttributeError(item) from exc

	def __setattr__(self, key, value):
		self[key] = value


@dataclass
class FakeDocType:
	name: str
	fields: list[dict] = field(default_factory=list)
	is_single: bool = False
	controller: type | None = None

	def fieldnames(self) -> set[str]:
		return {f["fieldname"] for f in self.fields}


class FakeDocument:
	"""Minimal stand-in for `frappe.model.document.Document`."""

	def __init__(self, bench, doctype: str, values: dict | None = None):
		object.__setattr__(self, "_bench", bench)
		object.__setattr__(self, "_data", dict(values or {}))
		object.__setattr__(self, "_before_save", None)
		self._data.setdefault("doctype", doctype)
		self._data.setdefault("name", None)
		self._data.setdefault("owner", bench.session.user)
		self.flags = types.SimpleNamespace(ignore_permissions=False)

	# -- attribute plumbing ------------------------------------------------

	def __getattr__(self, item):
		if item.startswith("_"):
			raise AttributeError(item)
		data = object.__getattribute__(self, "_data")
		if item in data:
			return data[item]
		# Frappe returns None for a declared-but-unset field rather than
		# raising, and controllers rely on that (`self.confidence or 0`).
		bench = object.__getattribute__(self, "_bench")
		meta = bench.doctypes.get(data.get("doctype"))
		if meta and item in meta.fieldnames():
			return None
		raise AttributeError(item)

	def __setattr__(self, key, value):
		if key in ("flags",):
			object.__setattr__(self, key, value)
			return
		self._data[key] = value

	def get(self, key, default=None):
		return self._data.get(key, default)

	def as_dict(self) -> dict:
		return dict(self._data)

	def update(self, values: dict):
		self._data.update(values)
		return self

	def get_doc_before_save(self):
		return self._before_save

	# -- lifecycle ---------------------------------------------------------

	def _run(self, hook: str):
		method = getattr(self, hook, None)
		if callable(method):
			method()

	def insert(self, ignore_permissions: bool = False):
		bench = self._bench
		doctype = self._data["doctype"]
		self._run("before_validate")
		self._run("validate")
		self._run("before_insert")
		if not self._data.get("name"):
			self._data["name"] = bench.generate_name(doctype)
		bench.db.insert_row(doctype, dict(self._data))
		self._run("after_insert")
		self._run("on_update")
		return self

	def save(self, ignore_permissions: bool = False):
		bench = self._bench
		doctype = self._data["doctype"]
		existing = bench.db.get_row(doctype, self._data["name"])
		if existing is None:
			return self.insert(ignore_permissions=ignore_permissions)
		object.__setattr__(self, "_before_save", _Row(copy.deepcopy(existing)))
		self._run("before_validate")
		self._run("validate")
		bench.db.update_row(doctype, self._data["name"], dict(self._data))
		self._run("on_update")
		return self

	def db_set(self, field_or_map, value=None, update_modified: bool = True):
		values = field_or_map if isinstance(field_or_map, dict) else {field_or_map: value}
		self._data.update(values)
		self._bench.db.update_row(self._data["doctype"], self._data["name"], values, partial=True)

	def check_permission(self, permission_type: str = "read"):
		if not self._bench.has_permission(
			self._data["doctype"], permission_type, doc=self, user=self._bench.session.user
		):
			raise PermissionError_(f"No {permission_type} permission on {self._data['doctype']}")

	def delete(self):
		self._run("on_trash")
		self._bench.db.delete_row(self._data["doctype"], self._data["name"])


class FakeDB:
	"""Table-backed store implementing the `frappe.db` surface used by the app."""

	def __init__(self, bench):
		self.bench = bench
		self.tables: dict[str, dict[str, dict]] = {}
		self.singles: dict[str, dict] = {}
		self.committed = 0
		self.rolled_back = 0
		self.savepoints: list[str] = []
		self.sql_log: list[str] = []
		#: Set to raise on the next write, to exercise failure handling.
		self.fail_next_write: Exception | None = None

	# -- helpers -----------------------------------------------------------

	def _table(self, doctype: str) -> dict:
		return self.tables.setdefault(doctype, {})

	def _maybe_fail(self):
		if self.fail_next_write is not None:
			error = self.fail_next_write
			self.fail_next_write = None
			raise error

	def insert_row(self, doctype: str, values: dict):
		self._maybe_fail()
		table = self._table(doctype)
		name = values["name"]
		if name in table:
			raise DuplicateEntryError(f"{doctype} {name} already exists")
		values.setdefault("creation", self.bench.now())
		values.setdefault("modified", self.bench.now())
		table[name] = dict(values)

	def update_row(self, doctype: str, name: str, values: dict, partial: bool = False):
		self._maybe_fail()
		table = self._table(doctype)
		if name not in table:
			raise DoesNotExistError(f"{doctype} {name} not found")
		table[name].update(values)

	def get_row(self, doctype: str, name: str) -> dict | None:
		return self._table(doctype).get(name)

	def delete_row(self, doctype: str, name: str):
		self._table(doctype).pop(name, None)

	# -- frappe.db API -----------------------------------------------------

	def exists(self, doctype, name=None):
		if isinstance(doctype, dict):
			raise AssertionError("fakebench: dict form of exists() not used by this app")
		if isinstance(name, dict):
			return bool(self._filtered(doctype, name))
		return name in self._table(doctype)

	def _filtered(self, doctype: str, filters) -> list[dict]:
		return [r for r in self._table(doctype).values() if _matches(r, filters)]

	def get_value(self, doctype, filters=None, fieldname="name", as_dict=False, **kwargs):
		rows = (
			[self.get_row(doctype, filters)] if isinstance(filters, str) else self._filtered(doctype, filters)
		)
		rows = [r for r in rows if r]
		if not rows:
			return None
		row = rows[0]
		if as_dict:
			fields = fieldname if isinstance(fieldname, list) else [fieldname]
			return _Row({f: row.get(f) for f in fields})
		if isinstance(fieldname, list):
			return [row.get(f) for f in fieldname]
		return row.get(fieldname)

	def set_value(self, doctype, name, field_or_map, value=None, update_modified: bool = True):
		values = field_or_map if isinstance(field_or_map, dict) else {field_or_map: value}
		targets = [name] if isinstance(name, str) else [r["name"] for r in self._filtered(doctype, name)]
		for target in targets:
			if target in self._table(doctype):
				self.update_row(doctype, target, values, partial=True)

	def get_single_value(self, doctype, fieldname):
		return self.singles.get(doctype, {}).get(fieldname)

	def set_single_value(self, doctype, fieldname, value):
		self.singles.setdefault(doctype, {})[fieldname] = value

	def count(self, doctype, filters=None):
		return len(self._filtered(doctype, filters))

	def delete(self, doctype, filters=None):
		"""`frappe.db.delete` takes filters, or a bare name string."""
		self._maybe_fail()
		table = self._table(doctype)
		if isinstance(filters, str):
			table.pop(filters, None)
			return
		for row in self._filtered(doctype, filters):
			table.pop(row["name"], None)

	def commit(self):
		self.committed += 1

	def rollback(self, save_point=None):
		self.rolled_back += 1

	def savepoint(self, name):
		self.savepoints.append(name)

	def release_savepoint(self, name):
		if name in self.savepoints:
			self.savepoints.remove(name)

	def sql(self, query, values=None, as_dict=False):
		"""Record raw SQL. This harness does not execute SQL.

		Application logic that depends on a raw-SQL *result* cannot be verified
		here and must be covered on a real bench; recording the statement at
		least lets a test assert that the call was made.
		"""
		self.sql_log.append(" ".join(str(query).split()))
		return []

	def table_exists(self, doctype):
		return doctype in self.tables

	def has_column(self, doctype, column):
		meta = self.bench.doctypes.get(doctype)
		return bool(meta and column in meta.fieldnames())


class FakeBench:
	"""Assembles a `frappe` module object backed by the in-memory store."""

	def __init__(self):
		self.doctypes: dict[str, FakeDocType] = {}
		self.db = FakeDB(self)
		self.session = types.SimpleNamespace(user="Administrator")
		self.local = types.SimpleNamespace(request_ip="127.0.0.1", request=None)
		self.flags = types.SimpleNamespace(in_test=True)
		self._counter = 0
		self._clock = 0
		self.errors: list[dict] = []
		self.realtime: list[dict] = []
		self.enqueued: list[dict] = []
		self.permission_hooks: dict[str, callable] = {}
		self.roles: dict[str, list[str]] = {}
		self._cached_singles: dict[str, FakeDocument] = {}

	# -- registry ----------------------------------------------------------

	def register_doctype(self, name, fields=None, controller=None, is_single=False):
		self.doctypes[name] = FakeDocType(
			name=name, fields=fields or [], is_single=is_single, controller=controller
		)
		self.db.tables.setdefault(name, {})
		return self.doctypes[name]

	def load_doctype_json(self, path, controller=None):
		meta = json.loads(path.read_text())
		return self.register_doctype(meta["name"], meta.get("fields", []), controller=controller)

	def now(self):
		self._clock += 1
		return f"2026-08-21 12:00:{self._clock:02d}.000000"

	def generate_name(self, doctype):
		self._counter += 1
		return f"{doctype.replace(' ', '')[:12].upper()}-{self._counter:05d}"

	# -- frappe API --------------------------------------------------------

	def new_doc(self, doctype, **values):
		meta = self.doctypes.get(doctype)
		controller = meta.controller if meta else None
		if controller is not None:
			doc = controller.__new__(controller)
			FakeDocument.__init__(doc, self, doctype, values)
			return doc
		return FakeDocument(self, doctype, values)

	def get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			values = dict(doctype)
			return self.new_doc(values.pop("doctype"), **values)
		row = self.db.get_row(doctype, name)
		if row is None:
			raise DoesNotExistError(f"{doctype} {name} not found")
		doc = self.new_doc(doctype, **copy.deepcopy(row))
		object.__setattr__(doc, "_before_save", _Row(copy.deepcopy(row)))
		return doc

	def get_cached_doc(self, doctype, name=None):
		meta = self.doctypes.get(doctype)
		if meta and meta.is_single:
			values = self.db.singles.setdefault(doctype, {})
			return FakeDocument(self, doctype, {**values, "name": doctype})
		return self.get_doc(doctype, name)

	def get_all(
		self,
		doctype,
		filters=None,
		fields=None,
		order_by=None,
		limit_page_length=None,
		limit=None,
		start=0,
		pluck=None,
		group_by=None,
		as_list=False,
		or_filters=None,
		**kwargs,
	):
		rows = [dict(r) for r in self.db._filtered(doctype, filters)]
		if or_filters:
			# ANDed with `filters`, matching Frappe: a row must satisfy the
			# base filters and at least one or_filter. Previously this
			# argument fell into **kwargs and was discarded, so any code
			# relying on it (folder subtree scoping, RET-07) was tested
			# against an unfiltered result set and could not fail.
			rows = [row for row in rows if any(_matches(row, [clause]) for clause in or_filters)]
		if order_by:
			for clause in reversed([c.strip() for c in str(order_by).split(",")]):
				parts = clause.split()
				key = parts[0].strip("`")
				reverse = len(parts) > 1 and parts[1].lower() == "desc"
				rows.sort(key=lambda r, k=key: (r.get(k) is None, r.get(k)), reverse=reverse)
		page = limit_page_length if limit_page_length is not None else limit
		if start:
			rows = rows[start:]
		if page:
			rows = rows[:page]
		if pluck:
			return [r.get(pluck) for r in rows]
		if fields:
			plain = [f for f in fields if isinstance(f, str)]
			rows = [_Row({f: r.get(f) for f in plain}) for r in rows]
		else:
			rows = [_Row(r) for r in rows]
		return rows

	def get_list(self, doctype, **kwargs):
		# Row-level permission filtering, the way `get_list` differs from `get_all`.
		rows = self.get_all(doctype, **kwargs)
		hook = self.permission_hooks.get(doctype)
		if hook is None:
			return rows
		return [r for r in rows if hook(self.session.user, r)]

	def has_permission(self, doctype, ptype="read", doc=None, user=None, throw=False):
		hook = self.permission_hooks.get(doctype)
		allowed = True
		if hook is not None and doc is not None:
			allowed = bool(hook(user or self.session.user, doc))
		if not allowed and throw:
			raise PermissionError_(f"No {ptype} permission on {doctype}")
		return allowed

	def throw(self, message, exc=ValidationError, title=None):
		if isinstance(exc, type) and issubclass(exc, BaseException):
			raise exc(message)
		raise ValidationError(message)

	def log_error(self, title=None, message=None, **kwargs):
		self.errors.append({"title": title, "message": message})

	def get_traceback(self):
		import traceback

		return traceback.format_exc()

	def generate_hash(self, length=10):
		self._counter += 1
		return f"h{self._counter:0{max(1, length - 1)}d}"[:length]

	def as_json(self, value, **kwargs):
		return json.dumps(value, default=str)

	def publish_realtime(self, event=None, message=None, **kwargs):
		self.realtime.append({"event": event, "message": message})

	def enqueue(self, method, **kwargs):
		self.enqueued.append({"method": method, "kwargs": kwargs})

	def get_hooks(self, hook=None, *args, **kwargs):
		return {}

	def whitelist(self, *dargs, **dkwargs):
		"""`@frappe.whitelist()` and bare `@frappe.whitelist` both occur."""
		if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
			return dargs[0]

		def decorator(fn):
			return fn

		return decorator

	def logger(self, name=None):
		return types.SimpleNamespace(
			info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None
		)

	def get_request_header(self, key):
		return None

	def delete_doc(self, doctype, name, **kwargs):
		self.db.delete_row(doctype, name)

	def clear_cache(self, **kwargs):
		return None

	def get_meta(self, doctype):
		meta = self.doctypes.get(doctype)
		names = meta.fieldnames() if meta else set()
		return types.SimpleNamespace(
			name=doctype,
			issingle=bool(meta and meta.is_single),
			has_field=lambda f: f in names,
		)

	# -- module assembly ---------------------------------------------------

	def build_module(self) -> types.ModuleType:
		module = types.ModuleType("frappe")
		for name in (
			"new_doc",
			"get_doc",
			"get_cached_doc",
			"get_all",
			"get_list",
			"has_permission",
			"throw",
			"log_error",
			"get_traceback",
			"generate_hash",
			"as_json",
			"publish_realtime",
			"enqueue",
			"get_hooks",
			"logger",
			"get_request_header",
			"delete_doc",
			"clear_cache",
			"get_meta",
			"whitelist",
		):
			setattr(module, name, getattr(self, name))
		module.db = self.db
		module.session = self.session
		module.local = self.local
		module.flags = self.flags
		module._ = lambda value: value
		module.ValidationError = ValidationError
		module.PermissionError = PermissionError_
		module.DoesNotExistError = DoesNotExistError
		module.DuplicateEntryError = DuplicateEntryError
		module._dict = _Row
		module.msgprint = lambda *a, **k: None
		module.scrub = lambda v: str(v).replace(" ", "_").lower()
		module.parse_json = lambda v: json.loads(v) if isinstance(v, str) else v
		module.cache = lambda: types.SimpleNamespace(
			get_value=lambda *a, **k: None, set_value=lambda *a, **k: None
		)
		module.utils = self._utils_module()
		module.model = types.ModuleType("frappe.model")
		document_module = types.ModuleType("frappe.model.document")

		bench = self

		class Document(FakeDocument):
			def __init__(self, *args, **kwargs):
				FakeDocument.__init__(self, bench, kwargs.pop("doctype", "Document"), kwargs)

		document_module.Document = Document
		module.model.document = document_module
		module.types = types.ModuleType("frappe.types")
		module.types.DF = types.SimpleNamespace()
		return module

	def _utils_module(self) -> types.ModuleType:
		utils = types.ModuleType("frappe.utils")

		def cint(value, default=0):
			try:
				return int(float(value))
			except (TypeError, ValueError):
				return default

		def flt(value, precision=None):
			try:
				result = float(value)
			except (TypeError, ValueError):
				return 0.0
			return round(result, precision) if precision is not None else result

		def now_datetime():
			from datetime import datetime

			return datetime(2026, 8, 21, 12, 0, 0)

		def add_days(date, days):
			from datetime import datetime, timedelta

			base = (datetime.fromisoformat(str(date)[:10]) if date else datetime(2026, 8, 21)) + timedelta(
				days=days
			)
			return base.strftime("%Y-%m-%d")

		utils.cint = cint
		utils.flt = flt
		utils.now_datetime = now_datetime
		utils.add_days = add_days
		utils.today = lambda: "2026-08-21"
		utils.now = lambda: "2026-08-21 12:00:00"
		utils.get_datetime = lambda v=None: v
		utils.cstr = lambda v: "" if v is None else str(v)
		return utils


def import_app(module_path: str):
	"""Import an application module bound to the currently installed bench.

	App modules do `import frappe` at import time, so a module imported before
	`install()` keeps the previous bench. Always obtain modules through this
	helper inside a test.
	"""
	import importlib

	if module_path in sys.modules:
		del sys.modules[module_path]
	return importlib.import_module(module_path)


def install(bench: FakeBench) -> FakeBench:
	"""Install `bench` as the process-wide `frappe`, replacing any prior stub."""
	module = bench.build_module()
	for name in [n for n in sys.modules if n == "frappe" or n.startswith("frappe.")]:
		del sys.modules[name]
	sys.modules["frappe"] = module
	sys.modules["frappe.utils"] = module.utils
	sys.modules["frappe.model"] = module.model
	sys.modules["frappe.model.document"] = module.model.document
	sys.modules["frappe.types"] = module.types
	# Application modules cache `import frappe` at import time, so anything
	# already imported must be dropped to pick up this bench.
	for name in [n for n in sys.modules if n.startswith("ai_fr_hg.")]:
		del sys.modules[name]
	return bench
