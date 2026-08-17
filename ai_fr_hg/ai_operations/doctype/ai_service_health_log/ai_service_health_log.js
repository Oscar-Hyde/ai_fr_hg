// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Service Health Log", {
	refresh(frm) {
		if (frm.is_new()) return;

		const colors = {
			Healthy: "green",
			Degraded: "orange",
			Unhealthy: "red",
		};
		frm.page.set_indicator(
			__(frm.doc.status),
			colors[frm.doc.status] || "grey"
		);

		if (frm.doc.error_message) {
			frm.dashboard.add_section(
				`<div class="alert alert-danger small">${frappe.utils.escape_html(frm.doc.error_message)}</div>`
			);
		}

		frm.disable_save();
	},
});