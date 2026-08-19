# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""A trilingual termbase enforced during translation."""

import frappe
from frappe import _
from frappe.model.document import Document


class AITranslationGlossary(Document):
	_DOCTYPE_NAME = "AI Translation Glossary"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_knowledge.doctype.ai_translation_term.ai_translation_term import (
			AITranslationTerm,
		)

		description: DF.SmallText | None
		enabled: DF.Check
		glossary_name: DF.Data
		knowledge_base: DF.Link | None
		terms: DF.Table[AITranslationTerm]
	# end: auto-generated types

	def validate(self):
		seen: set[tuple[str, str, str]] = set()
		for row in self.get("terms") or []:
			for field in ("term_en", "term_ar", "term_he"):
				row.set(field, (row.get(field) or "").strip())

			filled = [row.term_en, row.term_ar, row.term_he]
			if not any(filled):
				frappe.throw(_("Row {0}: enter the term in at least one language.").format(row.idx))
			if not row.do_not_translate and sum(1 for value in filled if value) < 2:
				frappe.throw(
					_(
						"Row {0}: a translatable term needs at least two languages, "
						"or tick Keep As Is to protect it in every language."
					).format(row.idx)
				)

			key = (row.term_en.lower(), row.term_ar.lower(), row.term_he.lower())
			if key in seen:
				frappe.throw(_("Row {0}: this term is already in the glossary.").format(row.idx))
			seen.add(key)
