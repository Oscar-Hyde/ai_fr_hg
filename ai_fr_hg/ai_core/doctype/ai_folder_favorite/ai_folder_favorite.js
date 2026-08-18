// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Folder Favorite", {
	refresh(frm) {
		if (frm.doc.folder) {
			frm.add_custom_button(__("Open Folder"), () => {
				frappe.set_route("Form", "File", frm.doc.folder);
			});
		}
	},
});
