// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Prompt Template", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Preview Prompt"), () => {
			const preview = [
				frm.doc.system_prompt
					? `<b>${__("System Prompt")}:</b><br>${frappe.utils.escape_html(
							frm.doc.system_prompt
					  )}<br><br>`
					: "",
				frm.doc.user_prompt
					? `<b>${__("User Prompt")}:</b><br>${frappe.utils.escape_html(
							frm.doc.user_prompt
					  )}`
					: "",
				frm.doc.variables && frm.doc.variables.length
					? `<br><br><b>${__("Variables")}:</b><br>${frm.doc.variables
							.map((v) => `{{ ${frappe.utils.escape_html(v.variable)} }}`)
							.join(", ")}`
					: "",
			].join("");
			frappe.msgprint(`<div class="small">${preview}</div>`, __("Template Preview"));
		});
	},
});
