# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _


class AIError(frappe.ValidationError):
	"""Base class for every error raised by the AI platform."""


class ProviderError(AIError):
	"""Raised when a provider endpoint cannot fulfil a request."""


class ProviderOfflineError(ProviderError):
	"""Raised when a provider endpoint is unreachable."""


class ProviderTimeoutError(ProviderError):
	"""Raised when a provider endpoint does not respond in time."""


class DeadlineExceededError(ProviderTimeoutError):
	"""Raised when the request's overall time budget is exhausted.

	Derives from :class:`ProviderTimeoutError` so existing timeout handling
	keeps working, but is distinct enough to report honestly to the user: the
	runtime did not necessarily fail, we simply ran out of time to wait for it.
	"""


class ModelNotAvailableError(AIError):
	"""Raised when the requested model is not present on the runtime."""


class LocalOnlyViolation(AIError):
	"""Raised when a configured endpoint would leave the local network."""


class QuotaExceededError(AIError):
	"""Raised when a resource policy limit is hit."""


class ToolExecutionError(AIError):
	"""Raised when a tool invocation fails."""


class DocumentProcessingError(AIError):
	"""Raised when a document cannot be read or indexed."""


class DocumentSourcePermissionError(DocumentProcessingError):
	"""The requesting user is not authorised to read the configured source."""


class UnsupportedDocumentError(DocumentProcessingError):
	"""No registered reader can safely process the source format."""


class CorruptDocumentError(DocumentProcessingError):
	"""A registered reader rejected malformed or unreadable content."""


class DocumentResourceLimitError(DocumentProcessingError):
	"""A source exceeded a deterministic ingestion size or redirect limit."""


class DocumentFetchError(DocumentProcessingError):
	"""A URL source could not be fetched safely."""


class PipelineError(AIError):
	"""Raised when a pipeline step fails and the policy is to stop."""


class PipelineStepRecordedError(PipelineError):
	"""A canonical step owner already persisted its failure/audit outcome."""


class PipelineApprovalRequired(PipelineStepRecordedError):
	"""A pipeline tool step created a durable approval request and must stop."""
