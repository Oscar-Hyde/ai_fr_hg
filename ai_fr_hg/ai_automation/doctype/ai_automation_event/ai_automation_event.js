// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Automation Event", {
	refresh(frm) {
		if (frm.is_new()) return;
		const colors = {
			Queued: "grey",
			Running: "blue",
			Success: "green",
			Failed: "red",
			Skipped: "orange",
			Coalesced: "orange",
		};
		frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "grey");
		if (frm.doc.rule) {
			frm.add_custom_button(__("Open Rule"), () =>
				frappe.set_route("Form", "AI Automation Rule", frm.doc.rule)
			);
		}
	},
});
