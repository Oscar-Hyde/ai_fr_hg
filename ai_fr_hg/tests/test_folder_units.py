# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for folder organization deterministic logic.

These do not touch the database or a model runtime; they exercise path
resolution, name validation, circular-nesting prevention and ranking via
mocked Frappe so they run fast everywhere (Master §25, File & Folder §10).
"""

import sys
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
