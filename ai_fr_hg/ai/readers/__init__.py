# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Document reader registry.

A reader turns bytes of a given file format into text plus metadata. Readers
are chosen by file extension and are pluggable: another app contributes new
formats through the `ai_document_readers` hook::

    ai_document_readers = {
        "dwg": "my_app.readers.dwg.DWGReader",
    }

All readers degrade gracefully: when an optional parsing library is missing the
reader reports that clearly instead of crashing the ingestion pipeline.
"""

from ai_fr_hg.ai.readers.base import BaseReader, ReadResult
from ai_fr_hg.ai.readers.code import LANGUAGES as CODE_LANGUAGES
from ai_fr_hg.ai.readers.code import CodeReader
from ai_fr_hg.ai.readers.container import ArchiveReader
from ai_fr_hg.ai.readers.office import (
	CSVReader,
	DocxReader,
	OdpReader,
	OdsReader,
	OdtReader,
	PDFReader,
	PptxReader,
	XlsxReader,
)
from ai_fr_hg.ai.readers.plain import (
	EmailReader,
	HTMLReader,
	ImageReader,
	JSONReader,
	MarkdownReader,
	TextReader,
	XMLReader,
)

BUILTIN_READERS: dict[str, type[BaseReader]] = {
	# plain text family
	"txt": TextReader,
	"log": TextReader,
	"rst": TextReader,
	"yaml": TextReader,
	"yml": TextReader,
	"ini": TextReader,
	"cfg": TextReader,
	"toml": TextReader,
	"md": MarkdownReader,
	"markdown": MarkdownReader,
	"json": JSONReader,
	"xml": XMLReader,
	"html": HTMLReader,
	"htm": HTMLReader,
	"eml": EmailReader,
	# office family
	"pdf": PDFReader,
	"docx": DocxReader,
	"xlsx": XlsxReader,
	"xlsm": XlsxReader,
	"pptx": PptxReader,
	"odt": OdtReader,
	"ods": OdsReader,
	"odp": OdpReader,
	"csv": CSVReader,
	"tsv": CSVReader,
	# images (OCR / vision)
	"png": ImageReader,
	"jpg": ImageReader,
	"jpeg": ImageReader,
	"webp": ImageReader,
	"gif": ImageReader,
	"bmp": ImageReader,
	"tiff": ImageReader,
}

# Source code: one reader owns every recognized programming language, so
# structure preservation cannot drift between formats.
BUILTIN_READERS.update({extension: CodeReader for extension in CODE_LANGUAGES})

# User archives. Office containers (docx/xlsx/...) are *not* listed here: they
# are single documents owned by their own readers, not containers of files.
BUILTIN_READERS.update(
	{
		"zip": ArchiveReader,
		"tar": ArchiveReader,
		"gz": ArchiveReader,
		"tgz": ArchiveReader,
		"bz2": ArchiveReader,
		"tbz2": ArchiveReader,
		"xz": ArchiveReader,
		"txz": ArchiveReader,
	}
)


def get_readers() -> dict[str, type[BaseReader]]:
	"""Built-in readers merged with those contributed by installed apps."""
	readers = dict(BUILTIN_READERS)
	try:
		import frappe

		hooks = frappe.get_hooks("ai_document_readers") or {}
	except (ImportError, AttributeError):
		# No bench (or no hook registry yet): the built-in registry is complete
		# on its own, so reader lookup must still work.
		return readers
	for extension, dotted_path in hooks.items():
		if isinstance(dotted_path, list):
			dotted_path = dotted_path[-1]
		try:
			readers[extension.lower().lstrip(".")] = frappe.get_attr(dotted_path)
		except Exception:
			frappe.log_error(
				title="AI reader registry",
				message=f"Could not load document reader {dotted_path} for .{extension}",
			)
	return readers


def get_reader(filename: str) -> BaseReader | None:
	"""Return a reader instance for `filename`, or None when unsupported."""
	extension = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
	reader_class = get_readers().get(extension)
	return reader_class() if reader_class else None


def supported_extensions() -> list[str]:
	return sorted(get_readers())
