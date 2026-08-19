// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Audit Log", {
	refresh(frm) {
		if (frm.is_new()) return;

		const severity_colors = {
			Info: "blue",
			Warning: "orange",
			Critical: "red",
		};
		frm.page.set_indicator(
			__(frm.doc.severity || "Info"),
			severity_colors[frm.doc.severity] || "blue"
		);

		frm.add_custom_button(__("View Related"), () => {
			if (frm.doc.reference_doctype && frm.doc.reference_name) {
				frappe.set_route("Form", frm.doc.reference_doctype, frm.doc.reference_name);
			}
		});

		frm.disable_save();
	},
});
