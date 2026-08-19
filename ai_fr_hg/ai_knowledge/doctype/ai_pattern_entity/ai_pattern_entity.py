# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ai_fr_hg.ai.patterns import (
	MAX_VALUE_LENGTH,
	PATTERN_ENTITY_TYPES,
	canonicalize_pattern_value,
	persistable_pattern_type,
)


class AIPatternEntity(Document):
	_DOCTYPE_NAME = "AI Pattern Entity"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		context_quote: DF.SmallText | None
		document: DF.Link
		entity_type: DF.Literal["email", "url", "phone", "ip", "hash", "date", "identifier", "money", "custom"]
		first_offset: DF.Int
		knowledge_base: DF.Link
		normalized_value: DF.Data | None
		occurrences: DF.Int
		source_checksum: DF.Data | None
		value: DF.Data
	# end: auto-generated types

	def validate(self):
		self.entity_type = persistable_pattern_type(self.entity_type or "custom")
		self.value = (self.value or "").strip()[:MAX_VALUE_LENGTH]
		if not self.value:
			frappe.throw(_("Value is required."))
		self.occurrences = max(1, cint(self.occurrences))
		# The canonical identity is server-authored so deduplication stays
		# deterministic even for hand-curated rows.
		if not (self.normalized_value or "").strip():
			self.normalized_value = canonicalize_pattern_value(self.entity_type, self.value)[:500]
		self.normalize_provenance()
		self.sync_knowledge_base()

	def normalize_provenance(self):
		if self.first_offset is not None and self.first_offset < 0:
			self.first_offset = 0
		quote = (self.context_quote or "").strip()
		self.context_quote = quote[:220] or None

	def sync_knowledge_base(self):
		"""Carry the parent document's knowledge base for row-level permissions."""
		if self.knowledge_base:
			return
		knowledge_base = frappe.db.get_value("AI Document", self.document, "knowledge_base")
		if not knowledge_base:
			frappe.throw(_("The source document no longer exists."))
		self.knowledge_base = knowledge_base

	@classmethod
	def pattern_entity_types(cls) -> tuple[str, ...]:
		"""The persistable type registry, mirrored for UI and API consumers."""
		return PATTERN_ENTITY_TYPES
