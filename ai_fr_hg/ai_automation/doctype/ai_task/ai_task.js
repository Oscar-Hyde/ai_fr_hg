// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

const TASK_COLORS = {
	Open: "grey",
	"Pending Approval": "orange",
	Approved: "blue",
	Rejected: "red",
	"In Progress": "blue",
	Completed: "green",
	Failed: "red",
	Cancelled: "orange",
};

function isManager() {
	return frappe.user_roles.some((role) => ["AI Manager", "System Manager", "Administrator"].includes(role));
}

frappe.ui.form.on("AI Task", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(__(frm.doc.status), TASK_COLORS[frm.doc.status] || "grey");

		const requester = frm.doc.requested_by || frm.doc.owner;
		const mine = requester === frappe.session.user;
		const manager = isManager();

		const add = (label, method, style) => {
			const btn = frm.add_custom_button(__(label), async () => {
				try {
					await frm.call(method);
					frm.reload_doc();
				} catch (error) {
					// Server already surfaced the message.
				}
			});
			if (style) btn.addClass(style);
		};

		if (frm.doc.status === "Open") {
			add("Submit for Approval", "submit_task");
			if (manager || !frm.doc.requires_approval) add("Run Now", "run_now", "btn-primary");
			if (mine || manager) add("Cancel", "cancel_task", "btn-danger");
		}
		if (frm.doc.status === "Pending Approval") {
			if (manager && frappe.session.user !== requester) add("Approve", "approve", "btn-primary");
			if (manager) add("Reject", "reject");
			if (mine || manager) add("Cancel", "cancel_task", "btn-danger");
		}
		if (frm.doc.status === "Approved" && (mine || manager)) {
			add("Run Now", "run_now", "btn-primary");
			add("Cancel", "cancel_task", "btn-danger");
		}
		if (frm.doc.status === "In Progress" && (mine || manager)) {
			add("Cancel", "cancel_task", "btn-danger");
		}
		if (frm.doc.status === "Failed" && (mine || manager)) {
			add("Retry", "retry");
		}

		if (frm.doc.execution_log) {
			frm.add_custom_button(__("Execution Log"), () =>
				frappe.set_route("Form", "AI Execution Log", frm.doc.execution_log)
			);
		}
		if (frm.doc.pipeline_run) {
			frm.add_custom_button(__("Pipeline Run"), () =>
				frappe.set_route("Form", "AI Pipeline Run", frm.doc.pipeline_run)
			);
		}
		if (frm.doc.error_message) {
			frm.dashboard.set_headline(
				`<span class="text-danger">${frappe.utils.escape_html(frm.doc.error_message)}</span>`
			);
		}
		frappe.realtime.on("ai_task_progress", (data) => {
			if (data && data.task === frm.doc.name) frm.reload_doc();
		});
	},
});
