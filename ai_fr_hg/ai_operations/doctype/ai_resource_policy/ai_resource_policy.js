// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Resource Policy", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(
			frm.doc.enabled ? __("Enabled") : __("Disabled"),
			frm.doc.enabled ? "green" : "grey"
		);

		if (frm.doc.role) {
			frm.add_custom_button(__("Users with this Role"), () =>
				frappe.set_route("List", "User", { role: frm.doc.role })
			);
		}
	},
});