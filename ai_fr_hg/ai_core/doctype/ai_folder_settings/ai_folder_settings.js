// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Folder Settings", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Open Folder"), () => {
				frappe.set_route("File", frm.doc.folder);
			});
			frm.add_custom_button(
				__("View Contents"),
				async () => {
					const result = await frappe.xcall("ai_fr_hg.api.folders.list_folder_contents", {
						folder: frm.doc.folder,
					});
					frappe.msgprint({
						title: frm.doc.folder,
						wide: true,
						message: `<pre>${frappe.utils.escape_html(JSON.stringify(result.items.slice(0, 20), null, 2))}</pre>`,
					});
				},
				__("Actions")
			);
		}
	},
});
