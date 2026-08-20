// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Execution Log", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(
			frm.doc.status,
			frm.doc.status === "Success" ? "green" : frm.doc.status === "Failed" ? "red" : "grey"
		);

		if (frm.doc.input_preview) {
			frm.add_custom_button(__("Preview Input"), () => {
				frappe.msgprint(
					`<pre class="small">${frappe.utils.escape_html(frm.doc.input_preview)}</pre>`,
					__("Input Preview")
				);
			});
		}

		if (frm.doc.output_preview) {
			frm.add_custom_button(__("Preview Output"), () => {
				frappe.msgprint(
					`<pre class="small">${frappe.utils.escape_html(frm.doc.output_preview)}</pre>`,
					__("Output Preview")
				);
			});
		}

		if (frm.doc.error_message) {
			frm.dashboard.add_section(
				`<div class="alert alert-danger small">${frappe.utils.escape_html(
					frm.doc.error_message
				)}</div>`
			);
		}

		frm.disable_save();
	},
});
