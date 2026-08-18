// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Folder Settings", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.folder) {
			frm.add_custom_button(__("Open Files"), () => {
				frappe.set_route("List", "File", ...frm.doc.folder.split("/"));
			});
		}
	},
});
