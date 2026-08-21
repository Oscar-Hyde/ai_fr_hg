# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Readers for text-based formats. These need no third-party libraries."""

import json

from ai_fr_hg.ai.readers.base import BaseReader, ReadResult


class TextReader(BaseReader):
	label = "Plain Text"

	def read(self, content: bytes, filename: str) -> ReadResult:
		text = self.clean(self.decode(content))
		return ReadResult(text=text, page_count=1, metadata={"format": "text"})


class MarkdownReader(BaseReader):
	label = "Markdown"

	def read(self, content: bytes, filename: str) -> ReadResult:
		raw = self.decode(content)
		headings = [line.lstrip("#").strip() for line in raw.splitlines() if line.lstrip().startswith("#")]
		return ReadResult(
			text=self.clean(raw),
			page_count=1,
			metadata={
				"format": "markdown",
				"headings": headings[:50],
				"title": headings[0] if headings else None,
			},
			structure=[{"kind": "heading", "text": heading} for heading in headings[:50]],
		)


class JSONReader(BaseReader):
	label = "JSON"

	def read(self, content: bytes, filename: str) -> ReadResult:
		raw = self.decode(content)
		try:
			data = json.loads(raw)
		except ValueError:
			return ReadResult(
				text=self.clean(raw),
				page_count=1,
				metadata={"format": "json"},
				warnings=["File is not valid JSON; indexed as plain text."],
			)

		return ReadResult(
			text=self.clean(self._flatten(data)),
			page_count=1,
			metadata={
				"format": "json",
				"root_type": type(data).__name__,
				"keys": list(data)[:100] if isinstance(data, dict) else None,
			},
			structure=[{"kind": "root", "type": type(data).__name__}],
		)

	def _flatten(self, value, prefix: str = "", depth: int = 0) -> str:
		"""Render JSON as readable `path: value` lines so it embeds well."""
		if depth > 12:
			return f"{prefix}: ...\n"
		lines = []
		if isinstance(value, dict):
			for key, item in value.items():
				path = f"{prefix}.{key}" if prefix else str(key)
				lines.append(self._flatten(item, path, depth + 1))
		elif isinstance(value, list):
			for index, item in enumerate(value[:500]):
				path = f"{prefix}[{index}]"
				lines.append(self._flatten(item, path, depth + 1))
		else:
			lines.append(f"{prefix}: {value}\n")
		return "".join(lines)


class XMLReader(BaseReader):
	label = "XML"

	def read(self, content: bytes, filename: str) -> ReadResult:
		from xml.etree import ElementTree

		raw = self.decode(content)
		try:
			root = ElementTree.fromstring(raw)
		except ElementTree.ParseError:
			return ReadResult(
				text=self.clean(raw),
				page_count=1,
				metadata={"format": "xml"},
				warnings=["File is not well-formed XML; indexed as plain text."],
			)

		parts: list[str] = []
		for element in root.iter():
			tag = element.tag.rsplit("}", 1)[-1]
			if text := (element.text or "").strip():
				parts.append(f"{tag}: {text}")
			for key, value in element.attrib.items():
				parts.append(f"{tag}@{key}: {value}")

		return ReadResult(
			text=self.clean("\n".join(parts)),
			page_count=1,
			metadata={"format": "xml", "root": root.tag},
			structure=[{"kind": "root", "tag": root.tag}],
		)


class HTMLReader(BaseReader):
	label = "HTML"

	def read(self, content: bytes, filename: str) -> ReadResult:
		raw = self.decode(content)
		title = None
		text = ""

		try:
			from bs4 import BeautifulSoup

			soup = BeautifulSoup(raw, "html.parser")
			for tag in soup(["script", "style", "noscript"]):
				tag.decompose()
			title = soup.title.string.strip() if soup.title and soup.title.string else None
			text = soup.get_text("\n")
		except ImportError:
			# Frappe ships bleach/html utilities; fall back to a regex strip.
			import re

			stripped = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
			if match := re.search(r"(?is)<title[^>]*>(.*?)</title>", stripped):
				title = match.group(1).strip()
			text = re.sub(r"(?s)<[^>]+>", "\n", stripped)
			import html as html_module

			text = html_module.unescape(text)

		return ReadResult(
			text=self.clean(text),
			page_count=1,
			metadata={"format": "html", "title": title},
			structure=[{"kind": "document", "title": title}] if title else [{"kind": "document"}],
		)


class EmailReader(BaseReader):
	label = "Email"

	def read(self, content: bytes, filename: str) -> ReadResult:
		from email import message_from_bytes
		from email.policy import default

		message = message_from_bytes(content, policy=default)
		headers = {
			key: str(message.get(key) or "") for key in ("From", "To", "Cc", "Subject", "Date", "Message-ID")
		}

		body = ""
		attachments = []
		if message.is_multipart():
			for part in message.walk():
				disposition = part.get_content_disposition()
				if disposition == "attachment":
					attachments.append(part.get_filename())
					continue
				if part.get_content_type() == "text/plain":
					body += part.get_content()
				elif part.get_content_type() == "text/html" and not body:
					body += HTMLReader().read(part.get_content().encode(), "part.html").text
		else:
			body = message.get_content()

		header_block = "\n".join(f"{key}: {value}" for key, value in headers.items() if value)
		embedded = [{"kind": "attachment", "name": name, "location": "email"} for name in attachments if name]
		return ReadResult(
			text=self.clean(f"{header_block}\n\n{body}"),
			page_count=1,
			metadata={"format": "email", "attachments": attachments, **headers},
			embedded_objects=embedded,
			structure=[{"kind": "headers"}, {"kind": "body"}] + [{"kind": "attachment"} for _ in embedded],
		)


class ImageReader(BaseReader):
	"""Images are described by a vision model, with OCR as a fallback."""

	label = "Image"

	def read(self, content: bytes, filename: str) -> ReadResult:
		import base64

		import frappe

		metadata = {"format": "image", "filename": filename}
		warnings: list[str] = []
		text = ""

		try:
			from io import BytesIO

			from PIL import Image

			with Image.open(BytesIO(content)) as image:
				metadata.update({"width": image.width, "height": image.height, "mode": image.mode})
		except Exception:
			warnings.append("Pillow is not installed; image dimensions unavailable.")

		# 1. Preferred: describe the image with a local vision model.
		try:
			text = self._describe_with_vision(content, filename)
		except Exception as exc:
			warnings.append(f"Vision description unavailable: {exc}")

		# 2. Fallback: OCR, when enabled and installed.
		if not text and frappe.db.get_single_value("AI Platform Settings", "ocr_enabled"):
			try:
				from io import BytesIO

				import pytesseract
				from PIL import Image

				with Image.open(BytesIO(content)) as image:
					text = pytesseract.image_to_string(image)
				metadata["ocr"] = True
			except Exception as exc:
				warnings.append(f"OCR unavailable: {exc}")

		return ReadResult(
			text=self.clean(text),
			page_count=1,
			metadata=metadata,
			warnings=warnings,
		)

	def _describe_with_vision(self, content: bytes, filename: str) -> str:
		import base64

		import frappe

		from ai_fr_hg.ai.engine import resolve_model, run_chat
		from ai_fr_hg.ai.providers.base import ChatMessage

		model = resolve_model(None, "Vision")
		encoded = base64.b64encode(content).decode()

		message = ChatMessage(
			role="user",
			content=(
				"Describe this image in detail for a searchable knowledge base. "
				"Transcribe any visible text verbatim."
			),
		)
		# Both Ollama and OpenAI-compatible runtimes accept images on the message.
		payload = message.as_dict()
		payload["images"] = [encoded]

		result = run_chat(
			[payload],
			model=model.name,
			operation="Chat",
			allow_failover=False,
		)
		return result.content
