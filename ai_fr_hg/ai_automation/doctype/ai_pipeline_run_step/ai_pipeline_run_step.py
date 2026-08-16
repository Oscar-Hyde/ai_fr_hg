# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIPipelineRunStep(Document):
	_DOCTYPE_NAME = "AI Pipeline Run Step"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		duration_ms: DF.Int
		error_message: DF.SmallText | None
		output: DF.Code | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		status: DF.Literal["Pending", "Running", "Success", "Failed", "Skipped"]
		step_name: DF.Data | None
		step_type: DF.Data | None
	# end: auto-generated types
