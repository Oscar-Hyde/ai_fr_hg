# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Readers for enterprise office formats.

Each reader keeps its parsing library optional. When the library is absent the
reader raises :class:`MissingDependency` with the exact install command, and
the ingestion pipeline records that on the document instead of failing hard.
"""

import csv
import io

from ai_fr_hg.ai.readers.base import BaseReader, ReadResult


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
		for index, page in enumerate(reader.pages):
			try:
				pages.append(page.extract_text() or "")
			except Exception as exc:
				pages.append("")
				warnings.append(f"Page {index + 1} could not be read: {exc}")

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
			warnings.append("No text layer found. This is likely a scanned PDF; enable OCR to index it.")

		return ReadResult(
			text=text,
			metadata={k: v for k, v in metadata.items() if v},
			page_count=len(reader.pages),
			pages=pages,
			warnings=warnings,
		)


class DocxReader(BaseReader):
	label = "Word Document"
	requires = "python-docx"

	def read(self, content: bytes, filename: str) -> ReadResult:
		docx = self.require("docx", "python-docx")

		document = docx.Document(io.BytesIO(content))
		parts: list[str] = []

		for paragraph in document.paragraphs:
			if not paragraph.text.strip():
				continue
			style = (paragraph.style.name or "") if paragraph.style else ""
			if style.startswith("Heading"):
				level = "".join(c for c in style if c.isdigit()) or "1"
				parts.append(f"{'#' * min(int(level), 6)} {paragraph.text.strip()}")
			else:
				parts.append(paragraph.text.strip())

		for index, table in enumerate(document.tables):
			parts.append(f"\n[Table {index + 1}]")
			for row in table.rows:
				cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
				if any(cells):
					parts.append(" | ".join(cells))

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
		)


class XlsxReader(BaseReader):
	label = "Excel Workbook"
	requires = "openpyxl"

	MAX_ROWS_PER_SHEET = 5000

	def read(self, content: bytes, filename: str) -> ReadResult:
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
		)


class PptxReader(BaseReader):
	label = "PowerPoint Presentation"
	requires = "python-pptx"

	def read(self, content: bytes, filename: str) -> ReadResult:
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
