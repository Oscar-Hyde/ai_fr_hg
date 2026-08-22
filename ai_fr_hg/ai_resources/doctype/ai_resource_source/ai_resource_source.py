# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIResourceSource(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		checksum: DF.Data | None
		enabled: DF.Check
		is_default: DF.Check
		last_checked: DF.Datetime | None
		notes: DF.SmallText | None
		offline_supported: DF.Check
		package_size_mb: DF.Float
		priority: DF.Int
		requires_authorization: DF.Check
		repository: DF.Link | None
		signature: DF.Data | None
		source_name: DF.Data
		source_type: DF.Literal["Built-in", "HTTP", "File", "Enterprise"]
		source_url: DF.Data | None
	# end: auto-generated types
