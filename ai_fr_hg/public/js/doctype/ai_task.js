// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Task", {
	refresh(frm) {
		if (frm.is_new()) return;

		const colors = {
			Pending: "grey",
			Scheduled: "blue",
			Running: "blue",
			Completed: "green",
			Failed: "red",
		};
		frm.page.set_indicator(
			__(frm.doc.status),
			colors[frm.doc.status] || "grey"
		);
	},
});