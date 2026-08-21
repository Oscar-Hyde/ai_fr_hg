# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from ai_fr_hg.ai.semantic import RELATIONSHIP_TYPES, normalize_relationship_type


class AIEntityRelationship(Document):
	_DOCTYPE_NAME = "AI Entity Relationship"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		confidence: DF.Percent
		document: DF.Link
		evidence_quote: DF.SmallText | None
		first_offset: DF.Int
		knowledge_base: DF.Link
		model_used: DF.Data | None
		object: DF.Data
		object_entity: DF.Link | None
		relationship_type: DF.Literal[
			"works_for",
			"located_in",
			"part_of",
			"reports_to",
			"owns",
			"partner_of",
			"mentions",
			"related_to",
		]
		source_checksum: DF.Data | None
		subject: DF.Data
		subject_entity: DF.Link | None
	# end: auto-generated types

	def validate(self):
		self.relationship_type = normalize_relationship_type(self.relationship_type)
		self.subject = (self.subject or "").strip()[:500]
		self.object = (self.object or "").strip()[:500]
		if not self.subject or not self.object:
			frappe.throw(_("Both subject and object are required."))
		if self.subject.casefold() == self.object.casefold():
			frappe.throw(_("A relationship cannot link an entity to itself."))
		# Evidence is mandatory: an inferred relationship with no supporting
		# span cannot be audited and must not be stored.
		quote = (self.evidence_quote or "").strip()
		if not quote:
			frappe.throw(_("A relationship requires an evidence quote from the source document."))
		self.evidence_quote = quote[:500]
		self.confidence = max(0.0, min(100.0, flt(self.confidence)))
		if self.first_offset is not None and self.first_offset < 0:
			self.first_offset = 0
		self.sync_knowledge_base()

	def sync_knowledge_base(self):
		"""Carry the parent document's knowledge base for row-level permissions."""
		if self.knowledge_base:
			return
		knowledge_base = frappe.db.get_value("AI Document", self.document, "knowledge_base")
		if not knowledge_base:
			frappe.throw(_("The source document no longer exists."))
		self.knowledge_base = knowledge_base

	@classmethod
	def relationship_types(cls) -> tuple[str, ...]:
		return RELATIONSHIP_TYPES
