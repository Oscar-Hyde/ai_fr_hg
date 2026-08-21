// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Pure Desk workflow contracts for Phase 7 evidence (TRN-04 / PAT-04).
 * Desk form/page scripts stay Frappe-classic (no ESM import); these helpers
 * are the tested source of the same rules.
 */

export function translationShouldShowStop(status) {
	return ["Queued", "Translating"].includes(status);
}

export function translationRealtimeShouldReload(data, translationName) {
	return Boolean(data && data.translation === translationName);
}

export function reconnectTranslationFromServer(doc) {
	return {
		status: doc.status || "",
		progress: doc.processing_progress ?? 0,
		message: doc.processing_message || "",
		cancel_requested: Number(doc.cancel_requested || 0),
		total_tokens: Number(doc.total_tokens || 0),
	};
}

export function patternExplorerPermissionDenied(error) {
	return /permission|not permitted|not allowed|cannot explore/i.test(
		(error && error.message) || ""
	);
}

export function patternExplorerErrorView(error) {
	const denied = patternExplorerPermissionDenied(error);
	return {
		kind: denied ? "permission-denied" : "failure",
		entities: [],
		message: denied
			? "You do not have permission to list pattern entities."
			: (error && error.message) || "Could not load pattern entities.",
	};
}

const TASK_ACTIONS = {
	Open: ["submit", "run", "cancel"],
	"Pending Approval": ["approve", "reject", "cancel"],
	Approved: ["run", "cancel"],
	"In Progress": ["cancel"],
	Failed: ["retry"],
};

export function taskActionsFor(
	status,
	{ isManager = false, isRequester = false, requiresApproval = false } = {}
) {
	const allowed = TASK_ACTIONS[status] || [];
	return allowed.filter((action) => {
		if (action === "approve") return isManager && !isRequester;
		if (action === "reject") return isManager;
		if (action === "run" && status === "Open") return isManager || !requiresApproval;
		return isManager || isRequester;
	});
}

export function pipelineRunIndicator(status) {
	return (
		{
			Queued: "grey",
			Running: "blue",
			"Waiting Approval": "orange",
			Completed: "green",
			Failed: "red",
			Cancelled: "orange",
		}[status] || "grey"
	);
}

export function pipelineCanCancel(status) {
	return ["Queued", "Running", "Waiting Approval"].includes(status);
}
