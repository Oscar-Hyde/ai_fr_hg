# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIPipelineStep(Document):
	_DOCTYPE_NAME = "AI Pipeline Step"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		config: DF.Code | None
		enabled: DF.Check
		extraction_schema: DF.Link | None
		input_field: DF.Data | None
		knowledge_base: DF.Link | None
		method: DF.Data | None
		model: DF.Link | None
		on_error: DF.Literal["Stop", "Continue", "Retry"]
		output_field: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		prompt_template: DF.Link | None
		retry_count: DF.Int
		step_name: DF.Data
		step_type: DF.Literal[
			"Extract Text",
			"Chunk",
			"Embed",
			"Summarize",
			"Classify",
			"Extract Data",
			"Compare",
			"Translate",
			"Prompt",
			"Tool",
			"Pipeline",
			"Custom Method",
		]
		sub_pipeline: DF.Link | None
		tool: DF.Link | None
	# end: auto-generated types
