# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIDocumentChunk(Document):
	_DOCTYPE_NAME = "AI Document Chunk"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		character_count: DF.Int
		checksum: DF.Data | None
		chunk_index: DF.Int
		content: DF.LongText
		document: DF.Link
		embedded_on: DF.Datetime | None
		embedding: DF.LongText | None
		embedding_dimensions: DF.Int
		embedding_format: DF.Literal["Base64 Float32", "JSON"]
		embedding_model: DF.Link | None
		embedding_norm: DF.Float
		heading: DF.Data | None
		knowledge_base: DF.Link
		page_number: DF.Int
		token_count: DF.Int
	# end: auto-generated types
