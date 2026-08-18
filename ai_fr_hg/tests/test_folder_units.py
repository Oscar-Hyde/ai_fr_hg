# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for folder organization deterministic logic.

These do not touch the database or a model runtime; they exercise path
resolution, name validation, circular-nesting prevention and ranking via
mocked Frappe so they run fast everywhere (Master §25, File & Folder §10).
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Provide a minimal frappe mock so unit tests run without bench
if "frappe" not in sys.modules:
    mock_frappe = MagicMock()
    mock_frappe.ValidationError = type("ValidationError", (Exception,), {})
    mock_frappe._ = lambda x: x
    mock_frappe.throw = lambda msg, exc=None: (_ for _ in ()).throw((exc or Exception)(msg)) if exc else (_ for _ in ()).throw(Exception(msg))
    sys.modules["frappe"] = mock_frappe
    sys.modules["frappe.utils"] = MagicMock()
    sys.modules["frappe.model"] = MagicMock()
    sys.modules["frappe.model.document"] = MagicMock()

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

from ai_fr_hg.ai.exceptions import (
	CircularFolderError,
	FolderAlreadyExistsError,
	InvalidFolderNameError,
)


class TestFolderNameValidation(UnitTestCase):
	def test_clean_name_accepts_normal_names(self):
		from ai_fr_hg.ai.folders import _clean_name

		self.assertEqual(_clean_name("Invoices"), "Invoices")
		self.assertEqual(_clean_name("  Contracts  "), "Contracts")
		self.assertEqual(_clean_name("My Project 2024"), "My Project 2024")

	def test_clean_name_rejects_empty_and_reserved(self):
		from ai_fr_hg.ai.folders import _clean_name

		for bad in ["", "   ", ".", "..", None]:
			with self.subTest(bad=bad), self.assertRaises(InvalidFolderNameError):
				_clean_name(bad)

	def test_clean_name_rejects_path_separators_and_specials(self):
		from ai_fr_hg.ai.folders import _clean_name

		for bad in ["a/b", "a\\b", "a:bad", "a*star", 'a"quote', "a|pipe", "a<less"]:
			with self.subTest(bad=bad), self.assertRaises(InvalidFolderNameError):
				_clean_name(bad)

	def test_clean_name_rejects_too_long(self):
		from ai_fr_hg.ai.folders import _clean_name

		long = "a" * 141
		with self.assertRaises(InvalidFolderNameError):
			_clean_name(long)
		# Exactly max length is allowed
		self.assertEqual(_clean_name("a" * 140), "a" * 140)


class TestFolderPathHelpers(UnitTestCase):
	def test_normalize_folder_path(self):
		from ai_fr_hg.ai.folders import _normalize_folder_path

		self.assertEqual(_normalize_folder_path(None), "Home")
		self.assertEqual(_normalize_folder_path(""), "Home")
		self.assertEqual(_normalize_folder_path("Home"), "Home")
		self.assertEqual(_normalize_folder_path("Home/Attachments"), "Home/Attachments")
		self.assertEqual(_normalize_folder_path("Attachments"), "Home/Attachments")
		self.assertEqual(_normalize_folder_path("Home/Projects"), "Home/Projects")
		self.assertEqual(_normalize_folder_path("Home/Projects/"), "Home/Projects")
		self.assertEqual(_normalize_folder_path("Home\\Projects"), "Home/Projects")

	def test_parent_and_depth(self):
		from ai_fr_hg.ai.folders import _depth, _parent_from_path

		self.assertEqual(_parent_from_path("Home"), None)
		self.assertEqual(_parent_from_path("Home/A"), "Home")
		self.assertEqual(_parent_from_path("Home/A/B"), "Home/A")
		self.assertEqual(_depth("Home"), 0)
		self.assertEqual(_depth("Home/A"), 1)
		self.assertEqual(_depth("Home/A/B"), 2)

	def test_is_descendant(self):
		from ai_fr_hg.ai.folders import _is_descendant

		self.assertTrue(_is_descendant("Home/A", "Home/A"))
		self.assertTrue(_is_descendant("Home/A/B", "Home/A"))
		self.assertTrue(_is_descendant("Home/A/B/C", "Home/A"))
		self.assertFalse(_is_descendant("Home/A", "Home/B"))
		self.assertFalse(_is_descendant("Home/A", "Home/A/B"))
		self.assertFalse(_is_descendant("Home/Other", "Home/A"))

	def test_circular_detection(self):
		from ai_fr_hg.ai.folders import _assert_no_circular

		# Should not raise for unrelated folders
		_assert_no_circular("Home/A", "Home/B")
		_assert_no_circular("Home/Projects", "Home/Contracts")

		# Same folder
		with self.assertRaises(CircularFolderError):
			_assert_no_circular("Home/A", "Home/A")
		# Move into own descendant
		with self.assertRaises(CircularFolderError):
			_assert_no_circular("Home/A", "Home/A/B")
		with self.assertRaises(CircularFolderError):
			_assert_no_circular("Home/A", "Home/A/B/C")


class TestFolderUniquenessAndDepth(UnitTestCase):
	@patch("ai_fr_hg.ai.folders.frappe")
	def test_assert_unique_throws_on_collision(self, frappe_mock):
		from ai_fr_hg.ai.folders import _assert_unique_in_parent

		# Make throw actually raise the typed error
		def fake_throw(msg, exc=None):
			import frappe as _f
			# Fallback to real throw behavior: raise exc
			if exc:
				raise exc(msg)
			raise Exception(msg)

		frappe_mock.throw.side_effect = fake_throw
		frappe_mock._ = lambda x: x
		frappe_mock.db.count.return_value = 1
		with self.assertRaises(FolderAlreadyExistsError):
			_assert_unique_in_parent("Invoices", "Home", is_folder=True)

		frappe_mock.db.count.return_value = 0
		# Should not raise
		_assert_unique_in_parent("Invoices", "Home", is_folder=True)

	@patch("ai_fr_hg.ai.folders.frappe")
	def test_depth_limit(self, frappe_mock):
		from ai_fr_hg.ai.folders import _depth, _MAX_FOLDER_DEPTH

		self.assertGreaterEqual(_MAX_FOLDER_DEPTH, 10)
		# Depth helper works for nested paths
		deep = "Home/" + "/".join(["L" + str(i) for i in range(19)])
		self.assertEqual(_depth(deep), 19)
		too_deep = "Home/" + "/".join(["L" + str(i) for i in range(22)])
		self.assertGreater(_depth(too_deep), _MAX_FOLDER_DEPTH)


class TestTabPresentation(UnitTestCase):
	@patch("ai_fr_hg.ai.folders.frappe")
	def test_tabs_are_backed_by_queries_not_static_placeholders(self, frappe_mock):
		"""Tabs must be a saved filter/view over real folder data (§3.3)."""
		from ai_fr_hg.ai.folders import get_tabs

		frappe_mock.session.user = "test@example.com"
		frappe_mock.get_roles.return_value = ["AI User"]
		frappe_mock.db.escape.side_effect = lambda x: f"'{x}'"
		frappe_mock.get_all.side_effect = lambda doctype, **kwargs: [
			type("O", (), {"name": "Home/Contracts", "file_name": "Contracts"}),
			type("O", (), {"name": "Home/Projects", "file_name": "Projects"}),
		]
		# Mock get_doc for permission checks
		mock_doc = type("Doc", (), {"name": "Home/Contracts", "file_name": "Contracts"})()
		frappe_mock.get_doc.return_value = mock_doc
		frappe_mock.has_permission.return_value = True

		tabs = get_tabs(user="test@example.com")
		# Must contain core tabs that map to real queries
		tab_ids = {t["id"] for t in tabs}
		self.assertIn("recent", tab_ids)
		self.assertIn("favorites", tab_ids)
		self.assertIn("shared", tab_ids)
		# And folder tabs for real top-level folders
		folder_tabs = [t for t in tabs if t["type"] == "folder"]
		self.assertTrue(len(folder_tabs) >= 1)
		# Each folder tab must carry a real folder reference
		for ft in folder_tabs:
			self.assertIn("folder", ft)
			self.assertTrue(ft["folder"].startswith("Home/"))


class _Row(dict):
	__getattr__ = dict.__getitem__


class TestPermissionAwareFolderReads(UnitTestCase):
	def test_stable_file_identity_hydrates_only_visible_ai_document(self):
		from ai_fr_hg.ai import folders

		file_row = _Row(
			name="FILE-2",
			file_url="/private/files/shared.pdf",
			attached_to_doctype=None,
			attached_to_name=None,
		)
		visible_document = _Row(name="DOC-2", status="Completed", knowledge_base="KB-1")
		with patch.object(folders.frappe, "get_list", return_value=[visible_document]) as get_list, patch.object(
			folders.frappe, "get_all"
		) as get_all:
			result = folders._visible_ai_document_for_file(file_row)

		self.assertIs(result, visible_document)
		self.assertEqual(get_list.call_args.kwargs["filters"], {"source_file_record": "FILE-2"})
		get_all.assert_not_called()

	def test_duplicate_content_url_never_hydrates_an_arbitrary_legacy_document(self):
		from ai_fr_hg.ai import folders

		file_row = _Row(
			name="FILE-2",
			file_url="/private/files/shared.pdf",
			attached_to_doctype=None,
			attached_to_name=None,
		)
		with patch.object(folders.frappe, "get_list", return_value=[]) as get_list, patch.object(
			folders.frappe,
			"get_all",
			return_value=["FILE-1", "FILE-2"],
		) as get_all:
			result = folders._visible_ai_document_for_file(file_row)

		self.assertIsNone(result)
		self.assertEqual(get_list.call_count, 1)
		self.assertEqual(get_all.call_args.kwargs["limit_page_length"], 2)

	def test_exact_legacy_attachment_disambiguates_duplicate_content(self):
		from ai_fr_hg.ai import folders

		file_row = _Row(
			name="FILE-2",
			file_url="/private/files/shared.pdf",
			attached_to_doctype="AI Document",
			attached_to_name="DOC-2",
		)
		visible_document = _Row(name="DOC-2", status="Completed", knowledge_base="KB-1")
		with patch.object(
			folders.frappe,
			"get_list",
			side_effect=[[], [visible_document]],
		) as get_list, patch.object(folders.frappe, "get_all") as get_all:
			result = folders._visible_ai_document_for_file(file_row)

		self.assertIs(result, visible_document)
		self.assertEqual(get_list.call_args_list[1].kwargs["filters"]["name"], "DOC-2")
		get_all.assert_not_called()

	def test_folder_listing_uses_dense_permission_aware_page_and_stable_hydration(self):
		from ai_fr_hg.ai import folders

		file_row = _Row(
			name="FILE-2",
			file_name="shared.pdf",
			is_folder=0,
			folder="Home/Visible",
			file_url="/private/files/shared.pdf",
		)
		visible_document = _Row(name="DOC-2", status="Completed", knowledge_base="KB-1")

		def get_list(doctype, **kwargs):
			if doctype == "AI Document":
				self.assertEqual(kwargs["filters"], {"source_file_record": "FILE-2"})
				return [visible_document]
			if kwargs["fields"] == ["count(name) as total"]:
				return [_Row(total=1)]
			return [file_row]

		folder_doc = SimpleNamespace(name="Home/Visible", doctype="File", is_folder=1)
		with patch.object(folders, "cint", side_effect=lambda value: int(value or 0)), patch.object(
			folders, "_assert_folder_exists"
		), patch.object(folders, "_get_folder_doc", return_value=folder_doc), patch.object(
			folders, "_check_permission"
		), patch.object(folders, "get_folder_path", return_value=[]), patch.object(
			folders.frappe, "get_list", side_effect=get_list
		) as list_query, patch.object(folders.frappe, "get_doc") as get_doc:
			result = folders.list_folder_contents("Home/Visible", limit=999, offset=-5)

		self.assertEqual(result["total"], 1)
		self.assertEqual([row.name for row in result["items"]], ["FILE-2"])
		self.assertEqual(result["items"][0]["ai_document"], "DOC-2")
		get_doc.assert_not_called()
		file_page_call = next(
			call
			for call in list_query.call_args_list
			if call.args[0] == "File" and call.kwargs["fields"] != ["count(name) as total"]
		)
		self.assertEqual(file_page_call.kwargs["limit_page_length"], 200)
		self.assertEqual(file_page_call.kwargs["limit_start"], 0)

	def test_folder_statistics_never_use_raw_counts_or_sql_aggregates(self):
		from ai_fr_hg.ai import folders

		folder_doc = SimpleNamespace(
			name="Home/Reports_100%",
			file_name="Reports_100%",
			folder="Home",
			is_private=1,
			is_home_folder=0,
			owner="owner@example.com",
			creation="2026-01-01",
			modified="2026-01-02",
		)
		with patch.object(folders, "_get_folder_doc", return_value=folder_doc), patch.object(
			folders, "_check_permission"
		), patch.object(folders, "get_folder_path", return_value=[]), patch.object(
			folders, "_permission_aware_count", side_effect=[2, 3, 5]
		) as count, patch.object(folders, "_permission_aware_file_size", return_value=77) as size, patch.object(
			folders.frappe.db, "exists", return_value=False
		):
			folders.frappe.db.count.reset_mock()
			folders.frappe.db.sql.reset_mock()
			result = folders.get_folder_info("Home/Reports_100%")

		self.assertEqual(
			result["stats"],
			{"folder_count": 2, "file_count": 3, "total_descendants": 5, "total_size": 77},
		)
		self.assertEqual(count.call_count, 3)
		descendant_call = count.call_args_list[2]
		self.assertEqual(descendant_call.kwargs["or_filters"][1][2], r"Home/Reports\_100\%/%")
		size.assert_called_once_with({"folder": "Home/Reports_100%", "is_folder": 0})
		folders.frappe.db.count.assert_not_called()
		folders.frappe.db.sql.assert_not_called()


class TestLiteralLikeEscaping(UnitTestCase):
	def test_like_metacharacters_and_backslashes_are_literal(self):
		from ai_fr_hg.ai.folders import _escape_like

		self.assertEqual(_escape_like(r"Home/Reports_100%\Q"), r"Home/Reports\_100\%\\Q")


class TestFolderProvenanceSynchronization(UnitTestCase):
	def test_ambiguous_legacy_url_is_not_claimed_by_file_move(self):
		from ai_fr_hg.ai import folders

		file_doc = SimpleNamespace(
			name="FILE-2",
			file_url="/private/files/shared.pdf",
			attached_to_doctype=None,
			attached_to_name=None,
		)

		def get_all(doctype, **kwargs):
			if doctype == "File":
				return ["FILE-1", "FILE-2"]
			self.assertEqual(kwargs["filters"]["source_file_record"], "FILE-2")
			return []

		with patch.object(folders.frappe, "get_doc", return_value=file_doc) as get_doc, patch.object(
			folders.frappe, "get_all", side_effect=get_all
		), patch.object(folders.frappe, "get_meta") as get_meta, patch.object(
			folders.frappe.db, "sql"
		) as lock, patch.object(folders.frappe.db, "set_value") as update:
			folders._update_document_folder_provenance("FILE-2", "Home/New")

		get_meta.assert_called_once_with("AI Document")
		get_doc.assert_called_once_with("File", "FILE-2")
		lock.assert_not_called()
		update.assert_not_called()

	def test_stable_link_requires_document_write_before_lock_or_update(self):
		from ai_fr_hg.ai import folders
		from ai_fr_hg.ai.exceptions import FolderPermissionError

		file_doc = SimpleNamespace(
			name="FILE-2",
			file_url="/private/files/shared.pdf",
			attached_to_doctype=None,
			attached_to_name=None,
		)
		document = SimpleNamespace(name="DOC-HIDDEN", doctype="AI Document")

		def get_doc(doctype, name):
			return file_doc if doctype == "File" else document

		def get_all(doctype, **kwargs):
			if doctype == "File":
				return ["FILE-1", "FILE-2"]
			filters = kwargs["filters"]
			return ["DOC-HIDDEN"] if filters.get("source_file_record") == "FILE-2" else []

		with patch.object(folders.frappe, "get_doc", side_effect=get_doc), patch.object(
			folders.frappe, "get_all", side_effect=get_all
		), patch.object(folders, "_check_permission", side_effect=FolderPermissionError("denied")), patch.object(
			folders.frappe.db, "sql"
		) as lock, patch.object(folders.frappe.db, "set_value") as update:
			with self.assertRaises(FolderPermissionError):
				folders._update_document_folder_provenance("FILE-2", "Home/New")

		lock.assert_not_called()
		update.assert_not_called()


class _LockField:
	def __init__(self, name):
		self.name = name

	def isin(self, values):
		return ("in", self.name, list(values))


class _LockQuery:
	def __init__(self, table):
		self.table = table
		self.selected = None
		self.condition = None
		self.ordered_by = None
		self.is_for_update = False

	def select(self, field):
		self.selected = field
		return self

	def where(self, condition):
		self.condition = condition
		return self

	def orderby(self, field):
		self.ordered_by = field
		return self

	def for_update(self):
		self.is_for_update = True
		return self

	def run(self):
		return [(name,) for name in self.condition[2]]


class TestPortableDocumentLocking(UnitTestCase):
	def test_ai_document_rows_use_sorted_bounded_query_builder_locks(self):
		from ai_fr_hg.ai import folders

		queries = []
		qb = SimpleNamespace()
		qb.DocType = lambda _doctype: SimpleNamespace(name=_LockField("name"))

		def from_(table):
			query = _LockQuery(table)
			queries.append(query)
			return query

		qb.from_ = from_
		names = [f"DOC-{number:04d}" for number in range(401, 0, -1)]
		with patch.object(folders.frappe, "qb", qb), patch.object(folders.frappe.db, "sql") as raw_sql:
			folders._lock_ai_document_rows(*names, "DOC-0001", None)

		self.assertEqual(len(queries), 2)
		self.assertEqual([len(query.condition[2]) for query in queries], [400, 1])
		self.assertEqual(queries[0].condition[2], sorted(set(names))[:400])
		self.assertEqual(queries[1].condition[2], sorted(set(names))[400:])
		self.assertTrue(all(query.is_for_update for query in queries))
		self.assertTrue(all(query.ordered_by is query.table.name for query in queries))
		raw_sql.assert_not_called()


class TestSharedSourceOwnership(UnitTestCase):
	def test_stable_file_link_is_selected_before_any_legacy_url_match(self):
		from ai_fr_hg.ai import folders

		def get_all(_doctype, **kwargs):
			filters = kwargs["filters"]
			if filters.get("source_file_record") == "FILE-1":
				return ["DOC-STABLE"]
			self.fail("legacy lookup must not run after a stable owner is found")

		with patch.object(folders.frappe, "get_all", side_effect=get_all) as query:
			owner = folders._remaining_source_owner(
				"FILE-1",
				"/private/files/shared.pdf",
				"DOC-MOVING",
			)

		self.assertEqual(owner, "DOC-STABLE")
		self.assertEqual(query.call_count, 1)
		self.assertEqual(query.call_args.kwargs["limit_page_length"], 1)

	def test_url_only_remaining_reference_fails_closed(self):
		from ai_fr_hg.ai import folders
		from ai_fr_hg.ai.exceptions import DocumentFetchError

		def get_all(_doctype, **kwargs):
			filters = kwargs["filters"]
			return [] if filters.get("source_file_record") == "FILE-1" else ["DOC-LEGACY"]

		with patch.object(folders.frappe, "get_all", side_effect=get_all):
			with self.assertRaises(DocumentFetchError):
				folders._remaining_source_owner(
					"FILE-1",
					"/private/files/shared.pdf",
					"DOC-MOVING",
				)

	def test_unshared_file_has_no_replacement_owner(self):
		from ai_fr_hg.ai import folders

		with patch.object(folders.frappe, "get_all", return_value=[]):
			self.assertIsNone(
				folders._remaining_source_owner(
					"FILE-1",
					"/private/files/unshared.pdf",
					"DOC-MOVING",
				)
			)
