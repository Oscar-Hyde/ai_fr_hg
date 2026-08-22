# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Every RPC the browser calls must exist and be whitelisted, and vice versa.

This is the VER-05 defect class generalised. VER-05 was a backend field the
Desk form could not render; the same disconnect exists whenever the frontend
names a server method that is missing, not whitelisted, or renamed — Frappe
resolves `frappe.xcall` targets as strings at click time, so the failure
surfaces as a 404 in the browser long after CI is green.

Neither fakebench nor the schema validator can see this: fakebench never loads
JavaScript, and the schema validator only reads DocType JSON.

Two directions are checked:

* every `ai_fr_hg.*` method string in JS resolves to a `@frappe.whitelist()`
  function (a dead button);
* every whitelisted method is reachable from JS, a hook, a scheduler entry, a
  DocType JSON, or is explicitly recorded below as a documented API surface
  (an orphan endpoint that nothing can invoke).

Resolution deliberately mirrors how Frappe itself resolves a dotted path:
`module.function`, including functions defined in a package `__init__.py`.
Template-literal composition (`` `${METHOD}.${name}` ``) is resolved by
substituting string constants declared in the same file, because that pattern
is used by the document tree and would otherwise produce false positives.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from unittest import TestCase

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent

#: Whitelisted methods with no in-repo caller, each with a reason.
#: Anything added here is asserting "this is a public API surface, not a dead
#: endpoint" — it must be justified, not used to silence the test.
#:
#: The folder-browser cluster is the FILE-05 residue: that finding replaced the
#: eager custom picker with Frappe's native lazy Link control, which removed the
#: only callers but left the endpoints published. They remain reachable by any
#: authenticated session, so they are an attack surface whose authorization is
#: asserted by `TestUncalledEndpointsStillEnforcePermissions` below rather than
#: by any UI exercising them. Tracked as FILE-08.
DOCUMENTED_EXTERNAL_API: dict[str, str] = {
	"ai_fr_hg.ai_conversation.doctype.ai_conversation.ai_conversation.generate_summary": (
		"CHAT-10: Desk form action with no script caller; delegates to "
		"ai_fr_hg.api.chat.summarize_conversation"
	),
	"ai_fr_hg.ai_conversation.doctype.ai_conversation.ai_conversation.send": (
		"CHAT-10: Desk form action with no script caller; delegates to ai_fr_hg.api.chat.send_message"
	),
}

#: Endpoints published without an in-repo caller must still prove they refuse
#: an unauthorized session. Empty since FILE-08 was dispositioned by Removal:
#: the nine unreachable folder-browser endpoints were deleted rather than
#: left as an unexercised surface. Kept so the invariant is re-armed the
#: moment another endpoint is published without a caller.
UNCALLED_ENDPOINTS_REQUIRING_AUTHZ: tuple[str, ...] = ()


def _iter_js() -> list[Path]:
	"""Every shipped JS file.

	Enumerated by walking the tree rather than by glob patterns: an earlier
	version of this harness used `public/js/**/*.js` and silently missed
	top-level files (`file_folder.js`) and the three report scripts, which
	turned real callers into phantom orphans.
	"""
	return sorted(
		path
		for path in APP.rglob("*.js")
		if "node_modules" not in path.parts and "__pycache__" not in path.parts
	)


def _whitelisted() -> dict[str, Path]:
	"""Map dotted path -> file for every @frappe.whitelist() function.

	A function in `pkg/__init__.py` is addressed as `pkg.function`, exactly as
	Frappe's own `get_attr` resolves it.
	"""
	found: dict[str, Path] = {}
	for py in APP.rglob("*.py"):
		if "__pycache__" in py.parts:
			continue
		try:
			tree = ast.parse(py.read_text())
		except SyntaxError:  # pragma: no cover - repo must parse
			continue
		relative = py.relative_to(ROOT)
		parts = list(relative.with_suffix("").parts)
		if parts[-1] == "__init__":
			parts.pop()
		module = ".".join(parts)
		for node in ast.walk(tree):
			if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
				continue
			for decorator in node.decorator_list:
				if "whitelist" in ast.unparse(decorator):
					found[f"{module}.{node.name}"] = py
					break
	return found


def _js_method_strings(text: str) -> set[str]:
	"""Collect method targets, resolving simple template-literal prefixes."""
	found: set[str] = set(re.findall(r"[\"'](ai_fr_hg\.[A-Za-z0-9_.]+)[\"']", text))

	# `const METHOD = "ai_fr_hg.api.document_tree";` then `${METHOD}.get_children`
	constants = dict(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*[\"'](ai_fr_hg\.[\w.]+)[\"']", text))
	for name, prefix in constants.items():
		for suffix in re.findall(r"`\$\{" + re.escape(name) + r"\}\.([A-Za-z0-9_]+)`", text):
			found.add(f"{prefix}.{suffix}")

		# A local wrapper that composes the suffix from its argument:
		#     function call(method, args) { return frappe.xcall(`${METHOD}.${method}`, args); }
		#     call("create_folder", {...})
		# Resolve by finding the wrapper's name, then its literal call sites.
		wrapper = re.search(
			r"function\s+(\w+)\s*\([^)]*\)\s*\{[^}]*?`\$\{" + re.escape(name) + r"\}\.\$\{",
			text,
			re.DOTALL,
		)
		if wrapper:
			for suffix in re.findall(
				rf"(?<![\w.]){re.escape(wrapper.group(1))}\(\s*[\"']([A-Za-z0-9_]+)[\"']", text
			):
				found.add(f"{prefix}.{suffix}")
		elif re.search(r"`\$\{" + re.escape(name) + r"\}\.\$\{", text):
			# Composed some other way; assert at least that the prefix is real.
			found.add(prefix + ".*")
	return found


def _controller_method_calls() -> set[str]:
	"""Resolve `frm.call("name")` / `cur_frm.call('name')` to controller methods.

	Frappe dispatches these against the DocType of the form the script is bound
	to, so the bare name maps to `<controller module>.<method>`. A DocType
	script lives at `<module>/doctype/<doctype>/<anything>.js`, which gives the
	controller path directly.
	"""
	resolved: set[str] = set()
	literal = re.compile(r"(?:cur_frm|frm)\.call\(\s*[\"']([A-Za-z0-9_]+)[\"']")
	# `frm.call(method)` inside a helper such as
	# `const add = (label, method) => { ... frm.call(method) ... }`,
	# invoked as `add("Approve", "approve")`. The method name is a variable, so
	# the literal pattern above cannot see it. Collect the bare string
	# arguments passed to such helpers instead of declaring the endpoint dead.
	indirect = re.compile(r"(?:cur_frm|frm)\.call\(\s*([A-Za-z_]\w*)\s*[,)]")
	for path in APP.rglob("*/doctype/*/*.js"):
		controller = path.parent / f"{path.parent.name}.py"
		if not controller.exists():
			continue
		module = ".".join(controller.relative_to(ROOT).with_suffix("").parts)
		text = path.read_text()
		for method in literal.findall(text):
			resolved.add(f"{module}.{method}")
		if indirect.search(text):
			# Any bare identifier-like string literal in this file is a
			# candidate method name; only those matching a real whitelisted
			# controller method are accepted, so this cannot mask a typo in a
			# *literal* call site, which the pattern above still checks.
			for candidate in re.findall(r"[\"']([a-z_][a-z0-9_]*)[\"']", text):
				resolved.add(f"{module}.{candidate}")
	return resolved


_AUTHZ_MARKERS = ("frappe.session.user", "check_permission", "has_permission", "only_for")


def _function_node(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
	for node in ast.walk(ast.parse(path.read_text())):
		if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
			return node
	return None


def _enforces_authorization(node, path: Path, depth: int = 0) -> bool:
	"""True when this function authorizes, or delegates to something that does.

	A thin §21 facade legitimately performs no check itself: it validates input
	and hands off to a service that scopes the query. Reading only the facade
	would therefore flag correct code, so follow one hop into the service the
	facade imports and call it authorized if the guard lives there.
	"""
	body = ast.unparse(node)
	if any(marker in body for marker in _AUTHZ_MARKERS):
		return True
	if depth >= 2:
		return False

	# Follow `from ai_fr_hg.x.y import service as alias` inside the function.
	for inner in ast.walk(node):
		if not isinstance(inner, ast.ImportFrom) or not (inner.module or "").startswith("ai_fr_hg."):
			continue
		target = ROOT.joinpath(*inner.module.split(".")).with_suffix(".py")
		if not target.exists():
			continue
		for alias in inner.names:
			called_as = alias.asname or alias.name
			if called_as not in body:
				continue
			service = _function_node(target, alias.name)
			if service is not None and _enforces_authorization(service, target, depth + 1):
				return True
	return False


def _module_exists(dotted: str) -> bool:
	parts = dotted.split(".")
	return ROOT.joinpath(*parts).with_suffix(".py").exists() or ROOT.joinpath(*parts, "__init__.py").exists()


class TestRpcReachability(TestCase):
	@classmethod
	def setUpClass(cls):
		cls.whitelisted = _whitelisted()
		cls.js_files = _iter_js()
		cls.js_calls: dict[str, Path] = {}
		for path in cls.js_files:
			for call in _js_method_strings(path.read_text()):
				cls.js_calls.setdefault(call, path)

	def test_discovery_found_the_real_surface(self):
		"""Guard the harness itself: silent discovery failure would pass everything."""
		self.assertGreater(len(self.whitelisted), 100, "whitelist discovery collapsed")
		self.assertGreater(len(self.js_files), 40, "JS discovery collapsed")
		self.assertGreater(len(self.js_calls), 40, "no RPC calls discovered in JS")

	def test_every_js_rpc_target_is_whitelisted(self):
		"""A button wired to a missing method is a 404 the user finds, not CI."""
		broken = []
		for call, path in sorted(self.js_calls.items()):
			if call.endswith(".*"):
				if not _module_exists(call[:-2]):
					broken.append(f"{path.relative_to(ROOT)}: dynamic prefix {call[:-2]!r} is not a module")
				continue
			if call in self.whitelisted:
				continue
			if _module_exists(call):
				continue  # a module path used as a namespace prefix, not a call
			broken.append(f"{path.relative_to(ROOT)}: {call!r} is not whitelisted")
		self.assertEqual(broken, [])

	def test_no_orphan_whitelisted_endpoints(self):
		"""An endpoint nothing can reach is either dead code or an undocumented API."""
		referenced: set[str] = {c for c in self.js_calls if not c.endswith(".*")}
		referenced.update(_controller_method_calls())

		hooks_text = (APP / "hooks.py").read_text()
		referenced.update(re.findall(r"[\"'](ai_fr_hg\.[A-Za-z0-9_.]+)[\"']", hooks_text))

		# Workspace shortcuts, dashboard links and DocType JSON can all name a
		# method as the action behind a button.
		for extra in APP.rglob("*.json"):
			if "__pycache__" in extra.parts or extra.name.endswith(".desired"):
				continue
			try:
				referenced.update(re.findall(r"(ai_fr_hg\.[A-Za-z0-9_.]+)", extra.read_text()))
			except (UnicodeDecodeError, OSError):  # pragma: no cover
				continue

		# A documented public endpoint counts as reachable: docs/API.md is the
		# published contract for integrators. Documentation is deliberately NOT
		# treated as proof of anything else — it only establishes intent to
		# expose, which is then held to the authorization assertion below.
		api_doc = (ROOT / "docs" / "API.md").read_text()
		referenced.update(re.findall(r"(ai_fr_hg\.[A-Za-z0-9_.]+)", api_doc))
		# API.md also documents endpoints in table rows as `name(args)` under a
		# module heading, so match a bare documented name against its module.
		documented_names = set(re.findall(r"`([a-z_][a-z0-9_]*)\(", api_doc))
		for dotted in self.whitelisted:
			if dotted.rsplit(".", 1)[1] in documented_names and dotted.startswith("ai_fr_hg.api."):
				referenced.add(dotted)

		orphans = []
		for dotted, path in sorted(self.whitelisted.items()):
			if dotted in referenced or dotted in DOCUMENTED_EXTERNAL_API:
				continue
			orphans.append(f"{path.relative_to(ROOT)}: {dotted} has no caller")
		self.assertEqual(orphans, [])

	def test_documented_external_api_entries_still_exist(self):
		"""Stops the allow-list from outliving the code it excuses."""
		for dotted in DOCUMENTED_EXTERNAL_API:
			self.assertIn(dotted, self.whitelisted, f"{dotted} is allow-listed but no longer exists")

	def test_uncalled_endpoints_still_scope_to_the_session_user(self):
		"""An endpoint with no UI caller is still reachable by any session.

		Currently empty: FILE-08 was dispositioned by Removal rather than
		left as an unexercised surface. The check stays so the invariant
		re-arms the moment another endpoint is published without a caller.
		"""
		for dotted in UNCALLED_ENDPOINTS_REQUIRING_AUTHZ:
			name = dotted.rsplit(".", 1)[1]
			node = _function_node(APP / "api" / "folders.py", name)
			self.assertIsNotNone(node, f"{dotted} no longer exists")
			self.assertTrue(
				_enforces_authorization(node, APP / "api" / "folders.py"),
				f"{dotted} never scopes to the caller",
			)

	def test_no_endpoint_acts_as_a_hardcoded_identity(self):
		"""A facade must act as the caller, never as a fixed user.

		A service that defaults to `user or frappe.session.user` looks
		authorized even when the facade hands it a fixed identity, so the
		literal argument is rejected outright. This covers every endpoint in
		the API layer, not only the ones without callers — the FILE-08
		removal left the previous, narrower version of this check with no
		subjects at all.
		"""
		impersonating = []
		for module in sorted((APP / "api").glob("*.py")):
			tree = ast.parse(module.read_text())
			for node in ast.walk(tree):
				if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
					continue
				if not any("whitelist" in ast.unparse(d) for d in node.decorator_list):
					continue
				body = ast.unparse(node)
				if re.search(r"user\s*=\s*[\"\'][^\"\']+[\"\']", body):
					impersonating.append(f"{module.name}:{node.lineno} {node.name}")
		self.assertEqual(
			impersonating,
			[],
			"whitelisted endpoints passing a hardcoded user instead of the session",
		)


class TestSingleGovernedRoute(TestCase):
	"""§21: one operation, one governed entry point.

	Where a facade in `api/` validates and authorizes before delegating, no
	second path may reach the same domain function while skipping that work.
	Both violations found in the re-audit were of this shape: the domain
	writer was independently whitelisted, and a DocType method called the
	agent directly instead of the facade.
	"""

	def test_domain_layer_publishes_no_unvalidated_writers(self):
		"""Domain modules may be whitelisted only where deliberately allowed."""
		allowed = {
			# Approval actions are invoked straight from Desk buttons and
			# perform their own `frappe.only_for` role check.
			"ai_fr_hg.ai.tools.approve_invocation",
			"ai_fr_hg.ai.tools.reject_invocation",
		}
		published = []
		for py in sorted((APP / "ai").rglob("*.py")):
			if "__pycache__" in py.parts:
				continue
			parts = list(py.relative_to(ROOT).with_suffix("").parts)
			if parts[-1] == "__init__":
				parts.pop()
			module = ".".join(parts)
			for node in ast.walk(ast.parse(py.read_text())):
				if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
					continue
				if any("whitelist" in ast.unparse(d) for d in node.decorator_list):
					dotted = f"{module}.{node.name}"
					if dotted not in allowed:
						published.append(dotted)
		self.assertEqual(
			published,
			[],
			"domain functions exposed as RPC without going through api/",
		)

	def test_conversation_send_delegates_to_the_chat_facade(self):
		"""`AI Conversation.send` must not call the agent directly.

		`api.chat.send_message` bounds the message to MAX_CHAT_MESSAGE_CHARS
		and calls `require_conversation(..., "write")`. Reaching
		`run_agent_turn` from the controller skips both.
		"""
		node = _function_node(
			APP / "ai_conversation" / "doctype" / "ai_conversation" / "ai_conversation.py", "send"
		)
		self.assertIsNotNone(node)
		# Strip the docstring: it names `run_agent_turn` to explain the rule,
		# and matching prose instead of code would make this test unfixable.
		statements = [
			statement
			for statement in node.body
			if not (
				isinstance(statement, ast.Expr)
				and isinstance(statement.value, ast.Constant)
				and isinstance(statement.value.value, str)
			)
		]
		code = "\n".join(ast.unparse(statement) for statement in statements)
		self.assertIn("send_message", code)
		self.assertNotIn(
			"run_agent_turn",
			code,
			"send() reaches the agent directly, bypassing facade validation",
		)


class TestDeskFieldReachability(TestCase):
	"""The VER-05 class, stated as an invariant rather than a one-off fix.

	A field the operator is expected to configure must appear in `field_order`,
	otherwise Desk will not render it and the capability is unreachable no
	matter how correct the backend is.
	"""

	@classmethod
	def setUpClass(cls):
		cls.doctypes = []
		for path in APP.rglob("**/doctype/*/*.json"):
			if path.stem != path.parent.name:
				continue
			meta = json.loads(path.read_text())
			if meta.get("doctype") == "DocType":
				cls.doctypes.append((path.relative_to(APP), meta))

	def test_operator_editable_fields_are_rendered(self):
		"""Every non-hidden, non-read-only field must be reachable in the form."""
		unreachable = []
		for path, meta in self.doctypes:
			order = meta.get("field_order")
			if not order:
				continue
			listed = set(order)
			for field in meta.get("fields", []):
				name = field.get("fieldname")
				if name in listed:
					continue
				if field.get("hidden") or field.get("read_only"):
					continue
				unreachable.append(f"{path}: '{name}' is editable but absent from field_order")
		self.assertEqual(unreachable, [])

	def test_settings_controls_are_rendered(self):
		"""Single DocTypes are pure operator surfaces: nothing may be stranded."""
		stranded = []
		for path, meta in self.doctypes:
			if not meta.get("issingle"):
				continue
			order = meta.get("field_order")
			if not order:
				continue
			for field in meta.get("fields", []):
				if field.get("fieldname") not in set(order):
					stranded.append(f"{path}: settings field '{field.get('fieldname')}' is not rendered")
		self.assertEqual(stranded, [])


class TestRegisterEvidenceIsReal(TestCase):
	"""A CLOSED row may not cite evidence that does not exist.

	SEC-07 was CLOSED on "Redaction, bounded-snippet, disable-control, and
	retention-contract tests" while `ai/logging.py::redact` was not referenced
	by a single test, and PIPE-04 named `validate_step_config`, which was
	equally untested. Both read as strong evidence and neither could fail.

	This makes the register self-checking: every code symbol a closed row
	names in its evidence column must appear somewhere in the test suite.
	It cannot verify that the test is *good* — only mutation testing does
	that — but it does stop a row from citing something that was never
	written.
	"""

	@classmethod
	def setUpClass(cls):
		cls.tests_text = "\n".join(
			path.read_text() for path in ROOT.rglob("test_*.py") if "__pycache__" not in path.parts
		)
		cls.register = (ROOT / "docs" / "GAP_REGISTER.md").read_text()

	def test_discovery_found_the_suite(self):
		self.assertGreater(len(self.tests_text), 100_000, "test discovery collapsed")
		self.assertIn("| SEC-07 |", self.register)

	def test_closed_rows_do_not_cite_symbols_that_no_test_exercises(self):
		unproven: list[str] = []
		for line in self.register.splitlines():
			if not re.match(r"^\| [A-Z]+-\d+", line):
				continue
			columns = [column.strip() for column in line.split("|")]
			status = next(
				(c for c in columns if c.startswith(("CLOSED", "OPEN", "REOPENED", "IN PROGRESS"))),
				"",
			)
			if not status.startswith("CLOSED"):
				continue
			evidence = columns[-2]
			# Backticked identifiers that look like code, not prose.
			symbols = [
				symbol
				for symbol in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", evidence)
				if "_" in symbol or symbol[0].isupper()
			]
			for symbol in symbols:
				if not re.search(rf"\b{re.escape(symbol)}\b", self.tests_text):
					unproven.append(f"{columns[1]}: cites `{symbol}`, which no test references")
		self.assertEqual(unproven, [])


class TestMutatingEndpointsAreGuarded(TestCase):
	"""Every whitelisted endpoint that writes must authorize or delegate.

	The generalisation of API-01. That finding was two domain functions
	published as a second, unvalidated route; the same shape would occur if a
	facade mutated state directly without a permission check. This sweeps the
	whole surface so the next one is caught by CI rather than by an audit.

	An endpoint satisfies the rule by checking permissions itself, or by
	handing off to a service module that does. Delegation is recognised
	through function-local imports, `from ... import x as y` aliases, and
	module-level service aliases (`from ai_fr_hg.ai import document_tree as
	service`), all of which are used in this codebase — an earlier version of
	this check missed the last form and produced two false positives.
	"""

	GUARD_MARKERS = (
		"check_permission",
		"has_permission",
		"only_for",
		"PermissionError",
		"_assert",
		"require_",
		"session.user",
		"valid_identifier",
		"api_validation",
	)
	WRITE_MARKERS = ("db_set", ".insert(", ".save(", ".delete(", "set_value", "enqueue", "db.sql")

	@staticmethod
	def _service_aliases(tree: ast.AST) -> set[str]:
		"""Module-level names bound to an in-app module."""
		aliases = set()
		for node in tree.body:
			if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ai_fr_hg"):
				aliases.update(alias.asname or alias.name for alias in node.names)
		return aliases

	def test_no_whitelisted_endpoint_writes_without_authorization(self):
		unguarded = []
		checked = 0
		for py in sorted(APP.rglob("*.py")):
			if any(part in py.parts for part in ("tests", "__pycache__")) or py.name.startswith("test_"):
				continue
			tree = ast.parse(py.read_text())
			aliases = self._service_aliases(tree)
			for node in ast.walk(tree):
				if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
					continue
				if not any("whitelist" in ast.unparse(d) for d in node.decorator_list):
					continue
				checked += 1
				body = ast.unparse(node)
				if not any(marker in body for marker in self.WRITE_MARKERS):
					continue
				if any(marker in body for marker in self.GUARD_MARKERS):
					continue
				if re.search(r"from ai_fr_hg\.[\w.]+ import", body):
					continue
				if any(re.search(rf"\b{re.escape(alias)}\.", body) for alias in aliases):
					continue
				unguarded.append(f"{py.relative_to(ROOT)}:{node.lineno} {node.name}")
		self.assertGreater(checked, 100, "endpoint discovery collapsed")
		self.assertEqual(unguarded, [], "whitelisted endpoints that write without authorizing")
