// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Usage Snapshot", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.snapshot_data) {
			frm.add_custom_button(__("View Data"), () => {
				frappe.msgprint(
					`<pre class="small">${frappe.utils.escape_html(
						JSON.stringify(JSON.parse(frm.doc.snapshot_data), null, 2)
					)}</pre>`,
					__("Usage Snapshot")
				);
			});
		}

		frm.disable_save();
	},
});
