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

import frappe

from ai_fr_hg.ai.readers.base import BaseReader, ReadResult
from ai_fr_hg.ai.readers.office import (
	CSVReader,
	DocxReader,
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
	"py": TextReader,
	"js": TextReader,
	"ts": TextReader,
	"sql": TextReader,
	"sh": TextReader,
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
	"msg": EmailReader,
	# office family
	"pdf": PDFReader,
	"docx": DocxReader,
	"xlsx": XlsxReader,
	"xlsm": XlsxReader,
	"pptx": PptxReader,
	"odt": OdtReader,
	"ods": OdsReader,
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


def get_readers() -> dict[str, type[BaseReader]]:
	"""Built-in readers merged with those contributed by installed apps."""
	readers = dict(BUILTIN_READERS)
	for extension, dotted_path in (frappe.get_hooks("ai_document_readers") or {}).items():
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
