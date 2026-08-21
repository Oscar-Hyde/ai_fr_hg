# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe-free Wave 4 extraction contracts: detection, archive policy, evidence."""

from __future__ import annotations

import io
import zipfile
from unittest import TestCase

from ai_fr_hg.ai.extraction import (
	build_evidence,
	detect_format,
	extract_bytes,
)
from ai_fr_hg.ai.readers.archive import (
	MAX_ARCHIVE_MEMBERS,
	ArchiveCorruptError,
	ArchiveLimitError,
	validate_zip_container,
	zip_kind,
)
from ai_fr_hg.ai.readers.base import ReadResult
from ai_fr_hg.ai.readers.plain import EmailReader, TextReader


def _zip_bytes(members: dict[str, bytes]) -> bytes:
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		for name, payload in members.items():
			archive.writestr(name, payload)
	return buffer.getvalue()


class TestReaderInventory(TestCase):
	def test_builtin_registry_is_thirty_seven_and_includes_odp(self):
		from ai_fr_hg.ai.readers import BUILTIN_READERS

		self.assertEqual(len(BUILTIN_READERS), 37)
		self.assertIn("odp", BUILTIN_READERS)
		self.assertNotIn("doc", BUILTIN_READERS)
		self.assertNotIn("xls", BUILTIN_READERS)
		self.assertNotIn("ppt", BUILTIN_READERS)
		self.assertNotIn("msg", BUILTIN_READERS)
		self.assertNotIn("zip", BUILTIN_READERS)


class TestDetectFormat(TestCase):
	def test_pdf_magic(self):
		identity = detect_format(b"%PDF-1.7\n1 0 obj", "report.pdf")
		self.assertEqual(identity.magic, "pdf")
		self.assertEqual(identity.family, "pdf")
		self.assertFalse(identity.mismatch)
		self.assertEqual(identity.reason, "aligned")

	def test_pdf_bytes_named_txt_is_mismatch(self):
		identity = detect_format(b"%PDF-1.4\n%", "notes.txt")
		self.assertEqual(identity.magic, "pdf")
		self.assertEqual(identity.extension, "txt")
		self.assertTrue(identity.mismatch)
		self.assertEqual(identity.reason, "extension_magic_mismatch")

	def test_email_like_magic(self):
		payload = b"From: alice@example.com\nTo: bob@example.com\nSubject: Hello\n\nBody\n"
		identity = detect_format(payload, "note.eml")
		self.assertEqual(identity.magic, "email")
		self.assertFalse(identity.mismatch)

	def test_json_magic_ignores_utf8_bom(self):
		identity = detect_format(b'\xef\xbb\xbf{"ok": true}', "data.json")
		self.assertEqual(identity.magic, "json")
		self.assertFalse(identity.mismatch)

	def test_png_magic(self):
		identity = detect_format(b"\x89PNG\r\n\x1a\nrest", "photo.png")
		self.assertEqual(identity.magic, "png")

	def test_docx_zip_kind(self):
		content = _zip_bytes({"word/document.xml": b"<w:document/>", "[Content_Types].xml": b"<Types/>"})
		self.assertEqual(zip_kind(content), "docx")
		identity = detect_format(content, "memo.docx")
		self.assertEqual(identity.magic, "docx")
		self.assertFalse(identity.mismatch)

	def test_odp_zip_kind(self):
		content = _zip_bytes(
			{
				"mimetype": b"application/vnd.oasis.opendocument.presentation",
				"META-INF/manifest.xml": b"<manifest/>",
			}
		)
		self.assertEqual(zip_kind(content), "odp")
		identity = detect_format(content, "deck.odp")
		self.assertEqual(identity.magic, "odp")
		self.assertFalse(identity.mismatch)

	def test_unknown_bytes_are_extension_only(self):
		identity = detect_format(b"plain words without a header", "notes.txt")
		self.assertIsNone(identity.magic)
		self.assertEqual(identity.reason, "extension_only")
		self.assertFalse(identity.mismatch)


class TestArchiveGuard(TestCase):
	def test_non_zip_extension_is_skipped(self):
		validate_zip_container(b"not a zip", "notes.txt")

	def test_member_bomb_is_rejected(self):
		members = {f"word/part{i}.xml": b"x" for i in range(MAX_ARCHIVE_MEMBERS + 1)}
		content = _zip_bytes(members)
		with self.assertRaises(ArchiveLimitError):
			validate_zip_container(content, "bomb.docx")

	def test_path_traversal_is_rejected(self):
		content = _zip_bytes({"../evil.txt": b"payload", "word/document.xml": b"<w:document/>"})
		with self.assertRaises(ArchiveCorruptError):
			validate_zip_container(content, "traverse.docx")

	def test_compression_ratio_is_rejected(self):
		# Re-pack with DEFLATE so zeros compress far above MAX_RATIO.
		buffer = io.BytesIO()
		with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
			archive.writestr("word/document.xml", b"\x00" * (256 * 1024))
		content = buffer.getvalue()
		with self.assertRaises(ArchiveLimitError):
			validate_zip_container(content, "ratio.docx")

	def test_absolute_path_is_rejected(self):
		content = _zip_bytes({"/tmp/evil.xml": b"x"})
		with self.assertRaises(ArchiveCorruptError):
			validate_zip_container(content, "abs.docx")


class TestReadResultCounts(TestCase):
	def test_word_and_character_count_are_live_properties(self):
		result = ReadResult(text="alpha beta gamma")
		self.assertEqual(result.word_count, 3)
		self.assertEqual(result.character_count, 16)
		result.text = "one"
		self.assertEqual(result.word_count, 1)
		self.assertEqual(result.character_count, 3)

	def test_empty_text_is_zero(self):
		result = ReadResult()
		self.assertEqual(result.word_count, 0)
		self.assertEqual(result.character_count, 0)


class TestExtractBytes(TestCase):
	def test_plain_text_attaches_evidence(self):
		outcome = extract_bytes(b"Hello local world.", "notes.txt", reader=TextReader())
		self.assertEqual(outcome.identity.extension, "txt")
		self.assertIn("Hello local world.", outcome.result.text)
		self.assertEqual(outcome.result.word_count, 3)
		evidence = outcome.evidence.as_dict()
		self.assertEqual(evidence["reader"], "Plain Text")
		self.assertEqual(evidence["provenance"]["word_count"], 3)
		self.assertEqual(evidence["provenance"]["bytes"], len(b"Hello local world."))
		self.assertEqual(len(evidence["provenance"]["checksum_sha256"]), 64)
		self.assertEqual(outcome.result.metadata["extraction_evidence"]["reader"], "Plain Text")

	def test_pdf_named_txt_prefers_magic_and_warns(self):
		# No PDF library required: pass TextReader so the test stays frappe-free,
		# but detection still records the mismatch for persistence.
		outcome = extract_bytes(b"%PDF-1.7\nnot actually parsed", "notes.txt", reader=TextReader())
		self.assertTrue(outcome.identity.mismatch)
		self.assertEqual(outcome.identity.magic, "pdf")
		self.assertTrue(any("does not match detected format pdf" in w for w in outcome.result.warnings))
		self.assertTrue(outcome.evidence.detector["mismatch"])

	def test_email_attachments_are_embedded_objects(self):
		message = (
			b"From: a@example.com\nTo: b@example.com\nSubject: Files\n"
			b"MIME-Version: 1.0\n"
			b'Content-Type: multipart/mixed; boundary="bnd"\n\n'
			b"--bnd\nContent-Type: text/plain\n\nHello\n\n"
			b"--bnd\nContent-Type: application/octet-stream\n"
			b'Content-Disposition: attachment; filename="invoice.pdf"\n\n'
			b"PDFBYTES\n--bnd--\n"
		)
		outcome = extract_bytes(message, "mail.eml", reader=EmailReader())
		names = [item["name"] for item in outcome.result.embedded_objects]
		self.assertIn("invoice.pdf", names)
		self.assertGreaterEqual(outcome.evidence.structure["block_count"], 2)

	def test_build_evidence_is_bounded(self):
		result = ReadResult(
			text="hello world",
			page_count=2,
			structure=[{"kind": "page"}] * 3 + [{"kind": "heading"}],
			embedded_objects=[{"kind": "image", "name": f"i{i}"} for i in range(60)],
		)
		evidence = build_evidence(
			detect_format(b"%PDF-1.4", "a.pdf"),
			result,
			"PDF",
			b"%PDF-1.4",
		)
		self.assertEqual(len(evidence.embedded_objects), 50)
		self.assertEqual(evidence.structure["block_count"], 4)
		self.assertEqual(evidence.structure["kinds"]["page"], 3)
		self.assertNotIn("hello world", str(evidence.as_dict()))
