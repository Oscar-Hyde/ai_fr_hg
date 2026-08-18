# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Fast deterministic tests for mixed tree identities and copy naming."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules:
	mock_frappe = MagicMock()
	mock_frappe.ValidationError = type("ValidationError", (Exception,), {})
	mock_frappe.PermissionError = type("PermissionError", (Exception,), {})
	mock_frappe._ = lambda value: value

	def throw(message, exc=None):
		raise (exc or Exception)(message)

	mock_frappe.throw = throw
	sys.modules["frappe"] = mock_frappe
	utils = MagicMock()
	utils.cint = lambda value: int(value or 0)
	utils.get_datetime = lambda value: value
	utils.now_datetime = MagicMock()
	sys.modules["frappe.utils"] = utils

import frappe

# Test modules can share the minimal Frappe stub in one unittest process.
# Ensure exception attributes remain real classes regardless of import order.
if not isinstance(getattr(frappe, "PermissionError", None), type):
	frappe.PermissionError = type("PermissionError", (Exception,), {})
if not isinstance(getattr(frappe, "TimestampMismatchError", None), type):
	frappe.TimestampMismatchError = type("TimestampMismatchError", (Exception,), {})

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

from ai_fr_hg.ai.document_tree import (
	_coerce_nodes,
	_decode_page,
	_encode_page,
	_source_files_for_documents,
	_subtree_state,
	_tree_rows,
	bulk_delete_nodes,
	copy_name_candidates,
	delete_document,
	document_node_value,
	move_document,
	rename_document,
	rename_folder,
	split_node_value,
)
from ai_fr_hg.ai.exceptions import DocumentFetchError, FolderNotEmptyError
from ai_fr_hg.ai.organization import organization_name_key


class TestDocumentTreeIdentity(UnitTestCase):
	def test_mixed_node_values_are_globally_unambiguous(self):
		self.assertEqual(document_node_value("DOC-0001"), "document::DOC-0001")
		self.assertEqual(split_node_value("document::DOC-0001"), ("document", "DOC-0001"))
		self.assertEqual(split_node_value("Home/Contracts"), ("folder", "Home/Contracts"))
		self.assertEqual(split_node_value("AI Documents"), ("root", "Home"))

	def test_organization_keys_are_normalized_case_insensitive_and_fixed_length(self):
		composed = organization_name_key("RÉSUMÉ.PDF")
		decomposed = organization_name_key("re\u0301sume\u0301.pdf")
		self.assertEqual(composed, decomposed)
		self.assertEqual(len(composed), 64)
		self.assertNotEqual(composed, organization_name_key("resume.pdf"))

	def test_bulk_nodes_are_canonicalized_and_deduplicated_before_preflight(self):
		self.assertEqual(
			_coerce_nodes(
				[
					"Home/Contracts",
					"/Home/Contracts/",
					"\\Home\\Contracts\\",
					"document::DOC-0001",
					"document::DOC-0001",
				]
			),
			["Home/Contracts", "document::DOC-0001"],
		)

	def test_bulk_nodes_reject_root_and_empty_document_identity(self):
		for nodes in (["Home"], ["/Home/"], ["document::"]):
			with self.subTest(nodes=nodes):
				with self.assertRaises(frappe.ValidationError):
					_coerce_nodes(nodes)

	def test_nonrecursive_bulk_delete_rejects_nonempty_folder_before_queueing(self):
		folder_rows = ["Home/Selected", "Home/Selected/Child"]
		with (
			patch(
				"ai_fr_hg.ai.document_tree._preflight_bulk_snapshot",
				return_value=(folder_rows, [], []),
			) as preflight,
			patch.object(frappe, "enqueue") as enqueue,
		):
			with self.assertRaises(FolderNotEmptyError):
				bulk_delete_nodes(["Home/Selected"], recursive=False, enqueue=True)

		self.assertEqual(preflight.call_count, 2)
		enqueue.assert_not_called()

	def test_folder_rename_reauthorizes_subtree_after_lock_acquisition(self):
		with (
			patch("ai_fr_hg.ai.document_tree._lock_names"),
			patch("ai_fr_hg.ai.document_tree._lock"),
			patch(
				"ai_fr_hg.ai.document_tree._preflight_subtree",
				side_effect=[(["Home/Source"], []), frappe.PermissionError("late descendant")],
			) as preflight,
			patch("ai_fr_hg.ai.document_tree._files_in_folders", return_value=[]),
			patch("ai_fr_hg.ai.document_tree._preflight_files"),
			patch("ai_fr_hg.ai.document_tree._lock_subtree"),
			patch("ai_fr_hg.ai.folders.rename_folder") as native_rename,
		):
			with self.assertRaises(frappe.PermissionError):
				rename_folder("Home/Source", "Renamed")

		self.assertEqual(preflight.call_count, 2)
		native_rename.assert_not_called()

	def test_copy_names_preserve_extension_and_increment_deterministically(self):
		candidates = copy_name_candidates("Contract.pdf")
		self.assertEqual(next(candidates), "Contract (Copy).pdf")
		self.assertEqual(next(candidates), "Contract (Copy 2).pdf")
		self.assertEqual(next(candidates), "Contract (Copy 3).pdf")

	def test_copying_an_existing_copy_reuses_the_stable_base(self):
		candidates = copy_name_candidates("Contract (Copy 8).pdf")
		self.assertEqual(next(candidates), "Contract (Copy).pdf")
		self.assertEqual(next(candidates), "Contract (Copy 2).pdf")

	def test_copy_names_stay_within_frappe_data_limit(self):
		candidates = copy_name_candidates(f"{'a' * 180}.document.pdf")
		for number in range(1, 1_001):
			candidate = next(candidates)
			self.assertLessEqual(len(candidate), 140)
			self.assertTrue(candidate.endswith(".pdf"))
			expected_suffix = " (Copy)" if number == 1 else f" (Copy {number})"
			self.assertTrue(candidate.removesuffix(".pdf").endswith(expected_suffix))

	def test_copy_rejects_extension_that_cannot_fit_a_stem_and_suffix(self):
		candidates = copy_name_candidates(f"x.{'a' * 135}")
		with self.assertRaises(frappe.ValidationError):
			next(candidates)

	def test_subtree_fingerprint_is_deterministic_and_sensitive_to_stale_state(self):
		class Row(dict):
			__getattr__ = dict.__getitem__

		files = {
			"Home/A": Row(
				name="Home/A",
				folder="Home",
				file_name="A",
				file_url=None,
				modified="2026-01-01 00:00:00",
				is_folder=1,
			),
			"FILE-1": Row(
				name="FILE-1",
				folder="Home/A",
				file_name="contract.pdf",
				file_url="/private/files/contract.pdf",
				modified="2026-01-01 00:00:00",
				is_folder=0,
			),
		}
		documents = {
			"DOC-1": Row(
				name="DOC-1",
				folder="Home/A",
				organization_name_key="contract.pdf",
				organization_revision=0,
				source_file="/private/files/contract.pdf",
				source_file_record="FILE-1",
				modified="2026-01-01 00:00:00",
			)
		}

		def rows_by_name(doctype, names, _fields):
			source = files if doctype == "File" else documents
			return [source[name] for name in reversed(names) if name in source]

		requested_documents = [Row(name="DOC-1")]
		requested_files = [Row(name="FILE-1")]
		with patch("ai_fr_hg.ai.document_tree._rows_by_name", side_effect=rows_by_name):
			original = _subtree_state(["Home/A"], requested_documents, requested_files)
			self.assertEqual(original, _subtree_state(["Home/A"], requested_documents, requested_files))
			documents["DOC-1"]["organization_revision"] = 1
			self.assertNotEqual(original, _subtree_state(["Home/A"], requested_documents, requested_files))
			documents["DOC-1"]["organization_revision"] = 0
			del files["FILE-1"]
			self.assertNotEqual(original, _subtree_state(["Home/A"], requested_documents, requested_files))

	def test_copy_source_resolution_batches_stable_file_ids(self):
		documents = [
			SimpleNamespace(
				name=f"DOC-{index}",
				source_type="File",
				source_file=f"/private/files/{index}.pdf",
				source_file_record=f"FILE-{index}",
			)
			for index in range(801)
		]

		def get_all(doctype, *, filters, **_kwargs):
			self.assertEqual(doctype, "File")
			return [
				SimpleNamespace(name=name, file_url=f"/private/files/{name.removeprefix('FILE-')}.pdf")
				for name in filters["name"][1]
			]

		with patch.object(frappe, "get_all", side_effect=get_all) as fetch:
			resolved = _source_files_for_documents(documents)

		self.assertEqual(len(resolved), 801)
		self.assertEqual(fetch.call_count, 3)

	def test_legacy_copy_source_prefers_exact_document_attachment(self):
		document = SimpleNamespace(
			name="DOC-2",
			source_type="File",
			source_file="/private/files/shared.pdf",
			source_file_record=None,
		)
		oldest = SimpleNamespace(
			name="FILE-1",
			file_url=document.source_file,
			attached_to_doctype=None,
			attached_to_name=None,
		)
		attached = SimpleNamespace(
			name="FILE-2",
			file_url=document.source_file,
			attached_to_doctype="AI Document",
			attached_to_name=document.name,
		)
		def get_all(_doctype, *, limit_start=0, **_kwargs):
			return [] if limit_start else [oldest, attached]

		with patch.object(frappe, "get_all", side_effect=get_all) as fetch:
			resolved = _source_files_for_documents([document])

		self.assertEqual([row.name for row in resolved], ["FILE-2"])
		self.assertEqual(fetch.call_count, 2)

	def test_legacy_copy_source_rejects_pathological_duplicate_urls_with_bounded_pages(self):
		document = SimpleNamespace(
			name="DOC-3",
			source_type="File",
			source_file="/private/files/duplicate.pdf",
			source_file_record=None,
		)
		rows = [
			SimpleNamespace(
				name=f"FILE-{index:04}",
				file_url=document.source_file,
				attached_to_doctype=None,
				attached_to_name=None,
			)
			for index in range(401)
		]

		def get_all(_doctype, *, filters, limit_start=0, limit_page_length=0, **_kwargs):
			self.assertEqual(limit_page_length, 400)
			if filters.get("attached_to_doctype"):
				return []
			return rows[limit_start : limit_start + limit_page_length]

		with patch.object(frappe, "get_all", side_effect=get_all) as fetch:
			with self.assertRaises(DocumentFetchError):
				_source_files_for_documents([document])

		self.assertEqual(fetch.call_count, 4)


class TestPermissionSafeSearchPagination(UnitTestCase):
	@staticmethod
	def _document_row(name: str, folder: str):
		return SimpleNamespace(
			name=name,
			title=name,
			organization_name=name,
			folder=folder,
			status="Indexed",
			knowledge_base="KB-1",
			modified="2026-01-01",
			owner="owner@example.com",
		)

	def test_document_keyset_cursor_contains_visible_identity_not_hidden_offset(self):
		value = _encode_page("Home", "document_after", "DOC-0002")
		node_type, payload = split_node_value(value)

		self.assertEqual(node_type, "page")
		self.assertEqual(_decode_page(payload), ("Home", "document_after", "DOC-0002"))

	def test_hidden_parent_candidates_are_bounded_and_do_not_create_a_cursor(self):
		hidden_rows = [
			self._document_row(f"DOC-{index:04d}", "Home/Hidden")
			for index in range(1000)
		]
		ai_queries = []

		def get_list(doctype, **kwargs):
			if doctype == "File":
				return []
			ai_queries.append(kwargs)
			anchor_filter = kwargs["filters"].get("name")
			anchor = anchor_filter[1] if anchor_filter else ""
			start = next(
				(index for index, row in enumerate(hidden_rows) if row.name > anchor),
				len(hidden_rows),
			)
			length = kwargs["limit_page_length"]
			return hidden_rows[start : start + length]

		with patch.object(frappe, "get_list", side_effect=get_list):
			nodes, cursor = _tree_rows(
				"Home",
				knowledge_base=None,
				search="DOC",
				cursor_kind="folder",
				position=0,
				page_length=10,
			)

		self.assertEqual(nodes, [])
		self.assertIsNone(cursor)
		self.assertEqual(len(ai_queries), 4)
		self.assertTrue(all(query["limit_page_length"] == 250 for query in ai_queries))

	def test_search_continuation_starts_after_last_visible_document(self):
		rows = [
			self._document_row("DOC-0001", "Home/Hidden"),
			self._document_row("DOC-0002", "Home/Visible"),
			self._document_row("DOC-0003", "Home/Visible"),
		]
		ai_queries = []

		def get_list(doctype, **kwargs):
			if doctype == "File":
				return ["Home/Visible"] if kwargs.get("pluck") == "name" else []
			ai_queries.append(kwargs)
			anchor_filter = kwargs["filters"].get("name")
			anchor = anchor_filter[1] if anchor_filter else ""
			return [row for row in rows if row.name > anchor]

		with patch.object(frappe, "get_list", side_effect=get_list), patch.object(
			frappe, "has_permission", return_value=True
		):
			first_nodes, first_cursor = _tree_rows(
				"Home",
				knowledge_base=None,
				search="DOC",
				cursor_kind="folder",
				position=0,
				page_length=1,
			)
			second_nodes, second_cursor = _tree_rows(
				"Home",
				knowledge_base=None,
				search="DOC",
				cursor_kind=first_cursor[0],
				position=first_cursor[1],
				page_length=1,
			)

		self.assertEqual([node["document"] for node in first_nodes], ["DOC-0002"])
		self.assertEqual(first_cursor, ("document_after", "DOC-0002"))
		self.assertEqual([node["document"] for node in second_nodes], ["DOC-0003"])
		self.assertIsNone(second_cursor)
		self.assertEqual(ai_queries[1]["filters"]["name"], [">", "DOC-0002"])


class TestStableFileResolution(UnitTestCase):
	def test_ambiguous_legacy_url_is_rejected_instead_of_selecting_oldest_file(self):
		from ai_fr_hg.ai.exceptions import DocumentFetchError
		from ai_fr_hg.ai.ingestion import _file_doc

		with (
			patch.object(frappe, "get_all", return_value=["FILE-OLD", "FILE-NEW"]),
			patch.object(frappe, "get_doc") as get_doc,
		):
			with self.assertRaises(DocumentFetchError):
				_file_doc("/private/files/shared.pdf")
		get_doc.assert_not_called()

	def test_exact_file_record_bypasses_url_ambiguity_but_must_match_url(self):
		from ai_fr_hg.ai.exceptions import DocumentFetchError
		from ai_fr_hg.ai.ingestion import _file_doc

		exact = SimpleNamespace(name="FILE-NEW", file_url="/private/files/shared.pdf")
		with (
			patch.object(frappe, "get_all") as lookup,
			patch.object(frappe, "get_doc", return_value=exact),
		):
			self.assertIs(_file_doc(exact.file_url, exact.name), exact)
		lookup.assert_not_called()

		with patch.object(
			frappe,
			"get_doc",
			return_value=SimpleNamespace(name="FILE-NEW", file_url="/private/files/other.pdf"),
		):
			with self.assertRaises(DocumentFetchError):
				_file_doc(exact.file_url, exact.name)

	def test_legacy_document_attachment_disambiguates_duplicate_content_rows(self):
		from ai_fr_hg.ai.ingestion import _file_doc

		exact = SimpleNamespace(name="FILE-ATTACHED", file_url="/private/files/shared.pdf")
		with (
			patch.object(frappe, "get_all", return_value=[exact.name]) as lookup,
			patch.object(frappe, "get_doc", return_value=exact),
		):
			self.assertIs(_file_doc(exact.file_url, document_name="DOC-1"), exact)
		self.assertEqual(lookup.call_count, 1)
		self.assertEqual(lookup.call_args.kwargs["limit_page_length"], 2)


class TestLifecycleLockingAndPermissions(UnitTestCase):
	class Row(dict):
		__getattr__ = dict.__getitem__

	def test_same_folder_move_stops_before_physical_source_or_shared_owner_resolution(self):
		snapshot = self.Row(name="DOC-1", folder="Home/Documents")
		current = self.Row(snapshot)

		with (
			patch("ai_fr_hg.ai.document_tree._document", side_effect=[snapshot, current]),
			patch("ai_fr_hg.ai.document_tree._lock_names") as lock_names,
			patch("ai_fr_hg.ai.document_tree._lock") as lock_document,
			patch("ai_fr_hg.ai.document_tree._check_write_access"),
			patch("ai_fr_hg.ai.document_tree._folder"),
			patch("ai_fr_hg.ai.ingestion._file_doc") as resolve_file,
			patch("ai_fr_hg.ai.document_tree._remaining_source_owner") as remaining_owner,
		):
			result = move_document(
				"DOC-1",
				"Home/Documents",
				expected_modified="2026-01-01 00:00:00",
			)

		self.assertTrue(result["unchanged"])
		lock_names.assert_called_once_with("File", ["Home/Documents"])
		lock_document.assert_called_once_with(
			"AI Document",
			"DOC-1",
			"2026-01-01 00:00:00",
		)
		resolve_file.assert_not_called()
		remaining_owner.assert_not_called()

	def test_rename_locks_parent_and_source_before_document_save(self):
		events = []
		snapshot = self.Row(
			name="DOC-1",
			folder="Home/Source",
			source_type="File",
			source_file_record="FILE-1",
			source_file="/private/files/source.txt",
			organization_name="source.txt",
			title="source.txt",
			organization_revision=0,
			modified="2026-01-01 00:00:00",
		)
		current = self.Row(snapshot)
		current.save = lambda: events.append("save")
		source = SimpleNamespace(name="FILE-1", folder="Home/Source")

		def lock_names(doctype, names):
			events.append(("lock_names", doctype, tuple(name for name in names if name)))

		with (
			patch("ai_fr_hg.ai.document_tree._document", side_effect=[snapshot, current]),
			patch("ai_fr_hg.ai.ingestion._file_doc", return_value=source),
			patch("ai_fr_hg.ai.document_tree._lock_names", side_effect=lock_names),
			patch("ai_fr_hg.ai.document_tree._lock", side_effect=lambda doctype, name, *_: events.append(("lock", doctype, name))),
			patch("ai_fr_hg.ai.document_tree._folder"),
			patch("ai_fr_hg.ai.document_tree._document_collision", return_value=False),
			patch("ai_fr_hg.ai.document_tree._audit"),
		):
			rename_document("DOC-1", "renamed.txt")

		self.assertEqual(
			events[:3],
			[
				("lock_names", "File", ("Home/Source", "Home/Source")),
				("lock_names", "File", ("FILE-1",)),
				("lock", "AI Document", "DOC-1"),
			],
		)
		self.assertGreater(events.index("save"), events.index(("lock", "AI Document", "DOC-1")))

	def test_delete_locks_attachment_parents_and_files_before_document(self):
		events = []
		document = self.Row(
			name="DOC-1",
			folder="Home/Documents",
			source_file_record="FILE-1",
			source_file="/private/files/source.txt",
		)
		attachments = [SimpleNamespace(name="FILE-1", folder="Home/Documents")]

		def lock_names(doctype, names):
			events.append(("lock_names", doctype, tuple(name for name in names if name)))

		with (
			patch("ai_fr_hg.ai.document_tree._document", side_effect=[document, document]),
			patch.object(frappe, "get_all", return_value=attachments),
			patch.object(frappe.db, "exists", side_effect=[True, False]),
			patch.object(frappe.db, "get_value", return_value="Home/Documents"),
			patch("ai_fr_hg.ai.document_tree._lock_names", side_effect=lock_names),
			patch("ai_fr_hg.ai.document_tree._lock", side_effect=lambda doctype, name, *_: events.append(("lock", doctype, name))),
			patch("ai_fr_hg.ai.document_tree._folder"),
			patch.object(frappe, "delete_doc", side_effect=lambda *_args, **_kwargs: events.append("delete")),
			patch("ai_fr_hg.ai.document_tree._audit"),
		):
			delete_document("DOC-1")

		self.assertEqual(events[0][0:2], ("lock_names", "File"))
		self.assertIn("Home/Documents", events[0][2])
		self.assertEqual(events[1][0:2], ("lock_names", "File"))
		self.assertIn("FILE-1", events[1][2])
		self.assertEqual(events[2], ("lock", "AI Document", "DOC-1"))
		self.assertGreater(events.index("delete"), events.index(("lock", "AI Document", "DOC-1")))

	def test_file_upload_checks_document_write_before_row_lock_or_update(self):
		from ai_fr_hg.utils.file_hooks import on_file_upload

		file_doc = SimpleNamespace(
			name="FILE-2",
			file_name="upload.txt",
			file_url="/private/files/upload.txt",
			folder="Home/Documents",
			is_folder=0,
			attached_to_doctype="AI Document",
			attached_to_name="DOC-1",
			attached_to_field="source_file",
			flags=SimpleNamespace(folder=None),
		)
		with (
			patch("ai_fr_hg.ai.folders.assign_file_to_folder", return_value={"folder": "Home/Documents"}),
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_doc", return_value=SimpleNamespace(name="DOC-1")),
			patch.object(frappe, "has_permission", return_value=False),
			patch.object(frappe.db, "get_value") as row_lock,
			patch.object(frappe.db, "set_value") as update,
		):
			with self.assertRaises(frappe.PermissionError):
				on_file_upload(file_doc)

		row_lock.assert_not_called()
		update.assert_not_called()


class _TreeLockField:
	def __init__(self, name):
		self.name = name

	def isin(self, values):
		return ("in", self.name, list(values))


class _TreeLockQuery:
	def __init__(self, table):
		self.table = table
		self.condition = None
		self.ordered_by = None
		self.is_for_update = False

	def select(self, _field):
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


class TestPortableTreeLocking(UnitTestCase):
	def test_recursive_rows_use_sorted_bounded_query_builder_locks(self):
		from ai_fr_hg.ai import document_tree

		queries = []
		qb = SimpleNamespace()
		qb.DocType = lambda _doctype: SimpleNamespace(name=_TreeLockField("name"))

		def from_(table):
			query = _TreeLockQuery(table)
			queries.append(query)
			return query

		qb.from_ = from_
		names = [f"FILE-{number:04d}" for number in range(401, 0, -1)]
		with patch.object(frappe, "qb", qb), patch.object(frappe.db, "sql") as raw_sql:
			document_tree._lock_names("File", names + ["FILE-0001", ""])

		self.assertEqual(len(queries), 2)
		self.assertEqual([len(query.condition[2]) for query in queries], [400, 1])
		self.assertEqual(queries[0].condition[2], sorted(set(names))[:400])
		self.assertEqual(queries[1].condition[2], sorted(set(names))[400:])
		self.assertTrue(all(query.is_for_update for query in queries))
		self.assertTrue(all(query.ordered_by is query.table.name for query in queries))
		raw_sql.assert_not_called()


class TestTreeLikeEscaping(UnitTestCase):
	def test_search_treats_like_metacharacters_as_literal_text(self):
		queries = []

		def get_list(_doctype, **kwargs):
			queries.append(kwargs)
			return []

		with patch.object(frappe, "get_list", side_effect=get_list):
			nodes, cursor = _tree_rows(
				"Home",
				knowledge_base=None,
				search=r"100%_\Q",
				cursor_kind="folder",
				position=0,
				page_length=10,
			)

		self.assertEqual(nodes, [])
		self.assertIsNone(cursor)
		self.assertEqual(queries[0]["or_filters"][0][2], r"%100\%\_\\Q%")
		self.assertEqual(queries[1]["or_filters"][0][2], r"%100\%\_\\Q%")

	def test_subtree_prefix_treats_folder_metacharacters_as_literal(self):
		from ai_fr_hg.ai.document_tree import _folder_paths

		with patch.object(frappe, "get_all", return_value=[]) as query, patch(
			"ai_fr_hg.ai.document_tree._assert_folder_exists",
			return_value="Home/Reports_100%",
		):
			self.assertEqual(_folder_paths("Home/Reports_100%"), ["Home/Reports_100%"])

		self.assertEqual(
			query.call_args.kwargs["filters"]["name"],
			["like", r"Home/Reports\_100\%/%"],
		)


class TestTreeMigrationIdentity(UnitTestCase):
	def test_backfill_helper_never_selects_an_arbitrary_duplicate_candidate(self):
		from ai_fr_hg.patches.v0_0_9_ai_document_tree_organization import (
			_unambiguous_candidates,
		)

		unique = SimpleNamespace(name="FILE-UNIQUE")
		oldest = SimpleNamespace(name="FILE-OLD")
		newest = SimpleNamespace(name="FILE-NEW")
		resolved = _unambiguous_candidates(
			{
				"/private/files/unique.pdf": [unique],
				"/private/files/duplicate.pdf": [oldest, newest],
			}
		)

		self.assertEqual(resolved, {"/private/files/unique.pdf": unique})
		self.assertNotIn("/private/files/duplicate.pdf", resolved)
