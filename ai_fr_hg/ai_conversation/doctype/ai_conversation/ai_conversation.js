// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Conversation", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.status, frm.doc.status === "Active" ? "green" : "grey");

		frm.add_custom_button(__("View Messages"), () =>
			frappe.set_route("List", "AI Message", {
				conversation: frm.doc.name,
			})
		);

		frm.add_custom_button(__("Open in Assistant"), () => {
			frappe.route_options = { conversation: frm.doc.name };
			frappe.set_route("ai-assistant", frm.doc.name);
		});

		if (frm.doc.agent) {
			frm.add_custom_button(__("View Agent"), () =>
				frappe.set_route("Form", "AI Agent", frm.doc.agent)
			);
		}
	},
});
