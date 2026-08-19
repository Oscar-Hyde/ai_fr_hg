# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AITranslationSegment(Document):
	_DOCTYPE_NAME = "AI Translation Segment"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		fingerprint: DF.Data | None
		heading: DF.Data | None
		issues: DF.SmallText | None
		kind: DF.Literal["paragraph", "heading", "list", "table", "marker", "code", "rule", "blank"]
		page_number: DF.Int
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		quality_score: DF.Percent
		reused: DF.Check
		reviewed: DF.Check
		segment_index: DF.Int
		separator: DF.Data | None
		source_characters: DF.Int
		source_text: DF.Text | None
		status: DF.Literal["Pending", "Translated", "Reused", "Copied", "Flagged", "Failed", "Reviewed"]
		translated_characters: DF.Int
		translated_text: DF.Text | None
	# end: auto-generated types
