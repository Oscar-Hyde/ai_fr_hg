# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIResource(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_resources.doctype.ai_resource_dependency.ai_resource_dependency import AIResourceDependency
		from ai_fr_hg.ai_resources.doctype.ai_resource_language.ai_resource_language import AIResourceLanguage
		from ai_fr_hg.ai_resources.doctype.ai_resource_provider.ai_resource_provider import AIResourceProvider
		from ai_fr_hg.ai_resources.doctype.ai_resource_source.ai_resource_source import AIResourceSource

		category: DF.Data
		deprecated: DF.Check
		dependencies: DF.Table[AIResourceDependency]
		description: DF.SmallText | None
		enabled: DF.Check
		is_builtin: DF.Check
		last_updated: DF.Datetime | None
		license: DF.Data | None
		license_url: DF.Data | None
		min_disk_gb: DF.Float
		min_frappe_version: DF.Data | None
		min_python_version: DF.Data | None
		min_ram_gb: DF.Float
		package_size_mb: DF.Float
		publisher: DF.Data
		release_notes: DF.LongText | None
		repository: DF.Link | None
		resource_code: DF.Data
		resource_name: DF.Data
		resource_type: DF.Literal[
			"Translation Package",
			"Translation Memory Pack",
			"AI Model",
			"AI Prompt Template",
			"AI Workflow Template",
			"Agent Capability",
			"Language Pack",
			"Knowledge Resource",
			"AI Extension",
		]
		security_restricted: DF.Check
		sha256: DF.Data | None
		signature: DF.Data | None
		signature_verified: DF.Check
		source_url: DF.Data | None
		sources: DF.Table[AIResourceSource]
		supported_languages: DF.Table[AIResourceLanguage]
		supported_providers: DF.Table[AIResourceProvider]
		version: DF.Data
	# end: auto-generated types

	def validate(self):
		self.resource_code = (self.resource_code or "").strip()
		self.resource_name = (self.resource_name or self.resource_code).strip()
		self.validate_url_scheme()

	def validate_url_scheme(self):
		source = (self.source_url or "").strip()
		if self.is_builtin or source.startswith("builtin://") or not source:
			return
		if not source.startswith(("http://", "https://")):
			frappe.throw(_("Resource source URL must use HTTP(S) or builtin://."))

	def compatibility(self):
		"""Return the live compatibility snapshot for this catalog entry."""
		from ai_fr_hg.ai.resources.catalog import evaluate_compatibility

		return evaluate_compatibility(self.as_dict())
