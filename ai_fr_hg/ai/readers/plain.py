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
			key: str(message.get(key) or "")
			for key in (
				"From",
				"To",
				"Cc",
				"Subject",
				"Date",
				"Message-ID",
				# Threading headers (RFC 5322 §3.6.4). Without these the
				# conversation a message belongs to cannot be reconstructed.
				"In-Reply-To",
				"References",
			)
		}

		body = ""
		attachments: list[str] = []
		attachment_parts: list[tuple[str, bytes]] = []
		warnings: list[str] = []
		if message.is_multipart():
			for part in message.walk():
				disposition = part.get_content_disposition()
				if disposition == "attachment":
					name = part.get_filename()
					attachments.append(name)
					if name and len(attachment_parts) < self.MAX_ATTACHMENTS:
						try:
							payload = part.get_payload(decode=True) or b""
						except Exception:
							payload = b""
						if payload and len(payload) <= self.MAX_ATTACHMENT_BYTES:
							attachment_parts.append((name, payload))
						elif payload:
							warnings.append(
								f"Attachment '{name}' is larger than "
								f"{self.MAX_ATTACHMENT_BYTES} bytes and was not read."
							)
					continue
				if part.get_content_type() == "text/plain":
					body += part.get_content()
				elif part.get_content_type() == "text/html" and not body:
					body += HTMLReader().read(part.get_content().encode(), "part.html").text
		else:
			body = message.get_content()

		header_block = "\n".join(f"{key}: {value}" for key, value in headers.items() if value)
		embedded = [{"kind": "attachment", "name": name, "location": "email"} for name in attachments if name]

		# Read attachment *content*, not just its filename. Preserving only the
		# name satisfies "preserve attachments" nominally while losing the
		# information the attachment actually carries.
		attachment_text, attachment_structure, attachment_warnings = self._read_attachments(attachment_parts)
		warnings.extend(attachment_warnings)

		# Normalized conversation identity, so downstream consumers do not each
		# re-parse RFC 5322 threading headers.
		references = self._message_ids(headers.get("References"))
		in_reply_to = self._message_ids(headers.get("In-Reply-To"))
		thread = {
			"message_id": (headers.get("Message-ID") or "").strip() or None,
			"in_reply_to": in_reply_to[0] if in_reply_to else None,
			"references": references,
			# Root of the thread when the chain is present, else this message.
			"root_message_id": (
				references[0] if references else ((headers.get("Message-ID") or "").strip() or None)
			),
			"is_reply": bool(in_reply_to or references),
		}

		text = self.clean(f"{header_block}\n\n{body}")
		if attachment_text:
			# Attachment text is appended verbatim after the cleaned message so
			# an attached source file keeps its indentation.
			text = f"{text}\n\n{attachment_text}"

		return ReadResult(
			text=text,
			page_count=1,
			metadata={
				"format": "email",
				"attachments": attachments,
				"attachments_read": len(attachment_structure),
				"thread": thread,
				**headers,
			},
			warnings=warnings,
			embedded_objects=embedded,
			structure=[{"kind": "headers"}, {"kind": "body"}]
			+ [{"kind": "attachment"} for _ in embedded]
			+ attachment_structure,
		)

	#: Bounds mirroring the archive policy: an email is a container too.
	MAX_ATTACHMENTS = 20
	MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
	MAX_ATTACHMENT_TEXT = 100_000

	def _read_attachments(self, parts: list[tuple[str, bytes]]) -> tuple[str, list[dict], list[str]]:
		"""Extract text from attachments using each format's own reader.

		A failing or unsupported attachment is recorded and skipped; it must
		never fail the message that carried it.
		"""
		from ai_fr_hg.ai.readers import get_reader

		blocks: list[str] = []
		structure: list[dict] = []
		warnings: list[str] = []

		for name, payload in parts:
			entry: dict = {"kind": "attachment_content", "name": name[:200], "bytes": len(payload)}
			reader = get_reader(name)
			if reader is None:
				entry["skipped_reason"] = "unsupported_format"
				structure.append(entry)
				continue
			# Guard against an attached archive recursing back into email.
			if isinstance(reader, EmailReader):
				entry["skipped_reason"] = "nested_email"
				structure.append(entry)
				continue
			try:
				result = reader.read(payload, name)
			except Exception as exc:
				entry["skipped_reason"] = "read_failed"
				entry["error"] = str(exc)[:300]
				warnings.append(f"Attachment '{name}' could not be read: {str(exc)[:160]}")
				structure.append(entry)
				continue
			attachment_text = (result.text or "").strip()
			if len(attachment_text) > self.MAX_ATTACHMENT_TEXT:
				attachment_text = attachment_text[: self.MAX_ATTACHMENT_TEXT]
				warnings.append(f"Attachment '{name}' text was truncated.")
			entry["read"] = True
			entry["reader"] = reader.label
			entry["characters"] = len(attachment_text)
			structure.append(entry)
			if attachment_text:
				blocks.append(f"[Attachment: {name}]\n{attachment_text}")

		return "\n\n".join(blocks), structure, warnings

	@staticmethod
	def _message_ids(raw: str | None) -> list[str]:
		"""Parse a whitespace/comma separated list of RFC 5322 message ids."""
		if not raw:
			return []
		import re as _re

		found = _re.findall(r"<[^<>@\s]+@[^<>\s]+>", raw)
		if found:
			# Preserve order, drop duplicates.
			seen: set[str] = set()
			ordered = []
			for item in found:
				if item not in seen:
					seen.add(item)
					ordered.append(item)
			return ordered[:50]
		token = raw.strip()
		return [token] if token else []


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
			if text:
				# A generated description is not a transcription. Mark the
				# provenance so downstream consumers never treat model prose as
				# text that was actually present in the image.
				metadata["text_source"] = "vision_model"
				metadata["confidence_available"] = False
		except Exception as exc:
			warnings.append(f"Vision description unavailable: {exc}")

		# 2. Fallback: OCR, when enabled and installed.
		if not text and frappe.db.get_single_value("AI Platform Settings", "ocr_enabled"):
			try:
				text, ocr_meta, ocr_warnings = self._ocr(content)
				metadata.update(ocr_meta)
				warnings.extend(ocr_warnings)
			except Exception as exc:
				warnings.append(f"OCR unavailable: {exc}")

		return ReadResult(
			text=self.clean(text),
			page_count=1,
			metadata=metadata,
			warnings=warnings,
		)

	#: OCR words below this mean confidence trigger a low-confidence warning.
	LOW_CONFIDENCE_THRESHOLD = 60.0

	def _ocr(self, content: bytes) -> tuple[str, dict, list[str]]:
		"""Run OCR and report per-word confidence.

		Uses ``image_to_data`` rather than ``image_to_string`` so that
		confidence is measurable; an OCR transcription without a confidence
		signal cannot be judged for reliability by any downstream consumer.
		"""
		from io import BytesIO

		import pytesseract
		from PIL import Image

		metadata: dict = {"ocr": True, "text_source": "ocr"}
		warnings: list[str] = []

		with Image.open(BytesIO(content)) as image:
			data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

		words: list[str] = []
		confidences: list[float] = []
		for token, raw_confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
			token = (token or "").strip()
			if not token:
				continue
			words.append(token)
			try:
				value = float(raw_confidence)
			except (TypeError, ValueError):
				continue
			# Tesseract reports -1 for entries it did not score.
			if value >= 0:
				confidences.append(value)

		text = " ".join(words)
		metadata["confidence_available"] = bool(confidences)
		if confidences:
			mean_confidence = sum(confidences) / len(confidences)
			metadata["ocr_confidence"] = round(mean_confidence, 2)
			metadata["ocr_confidence_min"] = round(min(confidences), 2)
			metadata["ocr_word_count"] = len(words)
			metadata["ocr_low_confidence_words"] = sum(
				1 for value in confidences if value < self.LOW_CONFIDENCE_THRESHOLD
			)
			if mean_confidence < self.LOW_CONFIDENCE_THRESHOLD:
				warnings.append(
					f"OCR mean confidence is {mean_confidence:.1f}%, below the "
					f"{self.LOW_CONFIDENCE_THRESHOLD:.0f}% threshold; the transcription may be unreliable."
				)
		elif words:
			warnings.append("OCR produced text but no confidence scores were reported.")

		return text, metadata, warnings

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
