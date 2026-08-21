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


class DocumentProcessingCancelled(DocumentProcessingError):
	"""A document worker observed a durable cancellation request."""


class DocumentFetchError(DocumentProcessingError):
	"""A URL source could not be fetched safely."""


class PipelineError(AIError):
	"""Raised when a pipeline step fails and the policy is to stop."""


class PipelineStepRecordedError(PipelineError):
	"""A canonical step owner already persisted its failure/audit outcome."""


class PipelineApprovalRequired(PipelineStepRecordedError):
	"""A pipeline tool step created a durable approval request and must pause."""

	def __init__(
		self, message: str | None = None, invocation: str | None = None, child_run: str | None = None
	):
		self.invocation = invocation
		self.child_run = child_run
		super().__init__(message or _("A pipeline step is waiting for approval."))


class TaskError(AIError):
	"""Raised when an AI Task cannot be executed or transitioned."""


class TaskIllegalTransition(TaskError):
	"""Raised when a caller requests a status change the state machine forbids."""


class FolderError(AIError):
	"""Base class for folder / file organization errors."""


class FolderNotFoundError(FolderError):
	"""Raised when a folder does not exist or is not a folder."""


class FolderAlreadyExistsError(FolderError):
	"""Raised when a folder or file name already exists in the target parent."""


class CircularFolderError(FolderError):
	"""Raised when a folder would become its own descendant."""


class FolderPermissionError(FolderError):
	"""Raised when the user lacks permission for the folder operation."""


class FolderNotEmptyError(FolderError):
	"""Raised when a non-empty folder is deleted without confirmation."""


class FileNotFoundError(FolderError):
	"""Raised when a file does not exist."""


class AmbiguousFileIdentityError(FolderError):
	"""Raised when a URL-only request cannot resolve one stable File record.

	Duplicate File rows may share a file_url; the platform must never select an
	arbitrary record, so callers must supply the exact File identity.
	"""


class InvalidFolderNameError(FolderError):
	"""Raised when a folder or file name is invalid."""


class HierarchicalReductionError(DocumentProcessingError):
	"""INT-03: Bounded hierarchical reduction exceeded safe levels — explicit failure, never silent truncation."""


class TurnCancelledError(AIError):
	"""Raised when a chat turn is cooperatively cancelled (CHAT-07).

	``partial`` holds any tokens already produced so the assistant message can
	be persisted as Cancelled rather than discarded.
	"""

	def __init__(self, message: str | None = None, partial: str = ""):
		self.partial = partial or ""
		super().__init__(message or _("This turn was cancelled."))
