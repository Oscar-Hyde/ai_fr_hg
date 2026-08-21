# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Readers for enterprise office formats.

Each reader keeps its parsing library optional. When the library is absent the
reader raises :class:`MissingDependency` with the exact install command, and
the ingestion pipeline records that on the document instead of failing hard.
"""

import csv
import io

from ai_fr_hg.ai.readers.archive import (
	ArchiveCorruptError,
	ArchiveLimitError,
	validate_zip_container,
)
from ai_fr_hg.ai.readers.base import BaseReader, ReadResult


def _validate_zip_archive(content: bytes, filename: str) -> None:
	"""Delegate to the single ZIP-container authority, translating domain errors."""
	try:
		validate_zip_container(content, filename)
	except ArchiveLimitError as exc:
		from ai_fr_hg.ai.exceptions import DocumentResourceLimitError

		raise DocumentResourceLimitError(str(exc)) from exc
	except ArchiveCorruptError as exc:
		from ai_fr_hg.ai.exceptions import CorruptDocumentError

		raise CorruptDocumentError(str(exc)) from exc


class PDFReader(BaseReader):
	label = "PDF"
	requires = "pypdf"

	def read(self, content: bytes, filename: str) -> ReadResult:
		pypdf = self.require("pypdf")

		reader = pypdf.PdfReader(io.BytesIO(content))
		warnings: list[str] = []

		if reader.is_encrypted:
			try:
				reader.decrypt("")
			except Exception:
				warnings.append("PDF is encrypted and could not be opened.")
				return ReadResult(metadata={"format": "pdf", "encrypted": True}, warnings=warnings)

		pages: list[str] = []
		embedded: list[dict] = []
		structure: list[dict] = []
		for index, page in enumerate(reader.pages):
			try:
				page_text = page.extract_text() or ""
			except Exception as exc:
				page_text = ""
				warnings.append(f"Page {index + 1} could not be read: {exc}")
			pages.append(page_text)
			structure.append({"kind": "page", "index": index + 1, "characters": len(page_text)})
			try:
				for image in list(getattr(page, "images", None) or [])[:20]:
					embedded.append(
						{
							"kind": "image",
							"name": str(getattr(image, "name", None) or f"page-{index + 1}-image"),
							"location": f"page {index + 1}",
						}
					)
			except Exception:
				pass

		text = self.clean("\n\n".join(f"[Page {i + 1}]\n{p}" for i, p in enumerate(pages) if p.strip()))

		metadata = {"format": "pdf", "pages": len(reader.pages)}
		try:
			if info := reader.metadata:
				metadata.update(
					{
						"title": info.title,
						"author": info.author,
						"subject": info.subject,
						"creator": info.creator,
					}
				)
		except Exception:
			pass

		if not text.strip():
			warnings.append(
				"No text layer found. Scanned-PDF OCR is not supported; OCR the PDF before uploading it."
			)

		return ReadResult(
			text=text,
			metadata={k: v for k, v in metadata.items() if v},
			page_count=len(reader.pages),
			pages=pages,
			warnings=warnings,
			structure=structure[:500],
			embedded_objects=embedded[:50],
		)


class DocxReader(BaseReader):
	label = "Word Document"
	requires = "python-docx"

	def read(self, content: bytes, filename: str) -> ReadResult:
		_validate_zip_archive(content, filename)
		docx = self.require("docx", "python-docx")

		document = docx.Document(io.BytesIO(content))
		parts: list[str] = []
		structure: list[dict] = []
		embedded: list[dict] = []

		for paragraph in document.paragraphs:
			if not paragraph.text.strip():
				continue
			style = (paragraph.style.name or "") if paragraph.style else ""
			if style.startswith("Heading"):
				level = "".join(c for c in style if c.isdigit()) or "1"
				line = f"{'#' * min(int(level), 6)} {paragraph.text.strip()}"
				structure.append({"kind": "heading", "text": paragraph.text.strip(), "level": int(level)})
			else:
				line = paragraph.text.strip()
				structure.append({"kind": "paragraph", "text": line})
			parts.append(line)
			for rel in getattr(paragraph, "hyperlinks", []) or []:
				target = getattr(rel, "url", None) or getattr(rel, "target", None)
				if target:
					embedded.append({"kind": "hyperlink", "name": target, "location": "body"})

		for index, table in enumerate(document.tables):
			parts.append(f"\n[Table {index + 1}]")
			structure.append({"kind": "table", "index": index + 1})
			for row in table.rows:
				cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
				if any(cells):
					parts.append(" | ".join(cells))

		for section_index, section in enumerate(getattr(document, "sections", []) or [], start=1):
			for label, part in (("header", section.header), ("footer", section.footer)):
				try:
					text = "\n".join(p.text.strip() for p in part.paragraphs if p.text.strip())
				except Exception:
					text = ""
				if text:
					parts.append(f"\n[{label.title()} {section_index}]\n{text}")
					structure.append({"kind": label, "index": section_index, "text": text[:500]})

		try:
			for comment in getattr(document, "comments", []) or []:
				body = getattr(comment, "text", None) or str(comment)
				if body:
					parts.append(f"[Comment] {body}")
					embedded.append({"kind": "comment", "name": str(body)[:200], "location": "comments"})
		except Exception:
			pass

		metadata = {"format": "docx", "tables": len(document.tables)}
		try:
			properties = document.core_properties
			metadata.update(
				{
					"title": properties.title,
					"author": properties.author,
					"subject": properties.subject,
					"created": str(properties.created) if properties.created else None,
				}
			)
		except Exception:
			pass

		return ReadResult(
			text=self.clean("\n".join(parts)),
			metadata={k: v for k, v in metadata.items() if v},
			page_count=1,
			structure=structure[:500],
			embedded_objects=embedded[:50],
		)


class XlsxReader(BaseReader):
	label = "Excel Workbook"
	requires = "openpyxl"

	MAX_ROWS_PER_SHEET = 5000

	def read(self, content: bytes, filename: str) -> ReadResult:
		_validate_zip_archive(content, filename)
		openpyxl = self.require("openpyxl")

		workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
		parts: list[str] = []
		warnings: list[str] = []
		sheet_names = []

		for sheet in workbook.worksheets:
			sheet_names.append(sheet.title)
			parts.append(f"\n[Sheet: {sheet.title}]")
			for index, row in enumerate(sheet.iter_rows(values_only=True)):
				if index >= self.MAX_ROWS_PER_SHEET:
					warnings.append(f"Sheet '{sheet.title}' truncated at {self.MAX_ROWS_PER_SHEET} rows.")
					break
				cells = ["" if cell is None else str(cell).strip() for cell in row]
				if any(cells):
					parts.append(" | ".join(cells))

		workbook.close()
		return ReadResult(
			text=self.clean("\n".join(parts)),
			metadata={"format": "xlsx", "sheets": sheet_names},
			page_count=len(sheet_names),
			warnings=warnings,
			structure=[
				{"kind": "sheet", "name": title, "index": i + 1} for i, title in enumerate(sheet_names)
			],
		)


class PptxReader(BaseReader):
	label = "PowerPoint Presentation"
	requires = "python-pptx"

	def read(self, content: bytes, filename: str) -> ReadResult:
		_validate_zip_archive(content, filename)
		pptx = self.require("pptx", "python-pptx")

		presentation = pptx.Presentation(io.BytesIO(content))
		parts: list[str] = []
		slides: list[str] = []

		for index, slide in enumerate(presentation.slides, start=1):
			slide_parts = [f"[Slide {index}]"]
			for shape in slide.shapes:
				if shape.has_text_frame and shape.text_frame.text.strip():
					slide_parts.append(shape.text_frame.text.strip())
				if getattr(shape, "has_table", False):
					for row in shape.table.rows:
						cells = [cell.text.strip() for cell in row.cells]
						if any(cells):
							slide_parts.append(" | ".join(cells))
			if notes_slide := (slide.notes_slide if slide.has_notes_slide else None):
				if notes := notes_slide.notes_text_frame.text.strip():
					slide_parts.append(f"[Notes] {notes}")

			rendered = "\n".join(slide_parts)
			slides.append(rendered)
			parts.append(rendered)

		return ReadResult(
			text=self.clean("\n\n".join(parts)),
			metadata={"format": "pptx", "slides": len(slides)},
			page_count=len(slides),
			pages=slides,
			structure=[{"kind": "slide", "index": i + 1} for i in range(len(slides))],
		)


def _odf_plain_text(element) -> str:
	data = getattr(element, "data", None)
	if data:
		return str(data)
	return "".join(_odf_plain_text(child) for child in getattr(element, "childNodes", []) or [])


class OdtReader(BaseReader):
	label = "OpenDocument Text"
	requires = "odfpy"

	def read(self, content: bytes, filename: str) -> ReadResult:
		_validate_zip_archive(content, filename)
		opendocument = self.require("odf.opendocument", "odfpy")
		odf_text = self.require("odf.text", "odfpy")
		document = opendocument.load(io.BytesIO(content))
		parts: list[str] = []
		for node in list(document.getElementsByType(odf_text.H)) + list(
			document.getElementsByType(odf_text.P)
		):
			text = _odf_plain_text(node).strip()
			if text:
				parts.append(text)
		return ReadResult(
			text=self.clean("\n".join(parts)),
			metadata={"format": "odt", "paragraphs": len(parts)},
			page_count=1,
			structure=[{"kind": "paragraph", "index": i + 1} for i in range(min(len(parts), 200))],
		)


class OdsReader(BaseReader):
	label = "OpenDocument Spreadsheet"
	requires = "odfpy"
	MAX_ROWS_PER_SHEET = 5000

	def read(self, content: bytes, filename: str) -> ReadResult:
		_validate_zip_archive(content, filename)
		opendocument = self.require("odf.opendocument", "odfpy")
		odf_table = self.require("odf.table", "odfpy")
		document = opendocument.load(io.BytesIO(content))
		parts: list[str] = []
		warnings: list[str] = []
		sheets = document.getElementsByType(odf_table.Table)
		for sheet in sheets:
			title = sheet.getAttribute("name") or "Sheet"
			parts.append(f"\n[Sheet: {title}]")
			for index, row in enumerate(sheet.getElementsByType(odf_table.TableRow)):
				if index >= self.MAX_ROWS_PER_SHEET:
					warnings.append(f"Sheet '{title}' truncated at {self.MAX_ROWS_PER_SHEET} rows.")
					break
				cells = [_odf_plain_text(cell).strip() for cell in row.getElementsByType(odf_table.TableCell)]
				if any(cells):
					parts.append(" | ".join(cells))
		return ReadResult(
			text=self.clean("\n".join(parts)),
			metadata={"format": "ods", "sheets": len(sheets)},
			page_count=len(sheets),
			warnings=warnings,
			structure=[{"kind": "sheet", "index": i + 1} for i in range(len(sheets))],
		)


class OdpReader(BaseReader):
	label = "OpenDocument Presentation"
	requires = "odfpy"

	def read(self, content: bytes, filename: str) -> ReadResult:
		_validate_zip_archive(content, filename)
		opendocument = self.require("odf.opendocument", "odfpy")
		odf_draw = self.require("odf.draw", "odfpy")
		odf_text = self.require("odf.text", "odfpy")
		document = opendocument.load(io.BytesIO(content))
		pages = document.getElementsByType(odf_draw.Page)
		parts: list[str] = []
		slides: list[str] = []
		for index, page in enumerate(pages, start=1):
			texts = []
			for node in list(page.getElementsByType(odf_text.P)) + list(
				page.getElementsByType(odf_text.Span)
			):
				value = _odf_plain_text(node).strip()
				if value:
					texts.append(value)
			rendered = "\n".join([f"[Slide {index}]", *texts])
			slides.append(rendered)
			parts.append(rendered)
		return ReadResult(
			text=self.clean("\n\n".join(parts)),
			metadata={"format": "odp", "slides": len(slides)},
			page_count=len(slides),
			pages=slides,
			structure=[{"kind": "slide", "index": i + 1} for i in range(len(slides))],
		)


class CSVReader(BaseReader):
	label = "Delimited Text"

	MAX_ROWS = 20_000

	def read(self, content: bytes, filename: str) -> ReadResult:
		raw = self.decode(content)
		delimiter = "\t" if filename.lower().endswith(".tsv") else None

		if delimiter is None:
			try:
				delimiter = csv.Sniffer().sniff(raw[:8192], delimiters=",;\t|").delimiter
			except csv.Error:
				delimiter = ","

		reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
		rows = []
		warnings: list[str] = []
		header = None

		for index, row in enumerate(reader):
			if index == 0:
				header = row
			if index >= self.MAX_ROWS:
				warnings.append(f"File truncated at {self.MAX_ROWS} rows.")
				break
			if any(cell.strip() for cell in row):
				rows.append(" | ".join(cell.strip() for cell in row))

		return ReadResult(
			text=self.clean("\n".join(rows)),
			metadata={
				"format": "csv",
				"delimiter": delimiter,
				"columns": header,
				"row_count": len(rows),
			},
			page_count=1,
			warnings=warnings,
		)
