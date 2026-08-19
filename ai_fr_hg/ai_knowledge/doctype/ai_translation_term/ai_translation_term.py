# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AITranslationTerm(Document):
	_DOCTYPE_NAME = "AI Translation Term"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		case_sensitive: DF.Check
		do_not_translate: DF.Check
		notes: DF.SmallText | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		term_ar: DF.Data | None
		term_en: DF.Data | None
		term_he: DF.Data | None
	# end: auto-generated types
