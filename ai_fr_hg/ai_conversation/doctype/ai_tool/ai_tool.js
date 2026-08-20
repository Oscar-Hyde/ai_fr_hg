// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Tool", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(
			frm.doc.enabled ? __("Enabled") : __("Disabled"),
			frm.doc.enabled ? "green" : "grey"
		);

		if (frm.doc.tool_type === "Builtin") {
			frm.set_df_property("handler", "read_only", 1);
		}

		if (frm.doc.json_schema) {
			frm.add_custom_button(__("Preview JSON Schema"), () => {
				frappe.msgprint(
					`<pre class="small">${frappe.utils.escape_html(
						JSON.stringify(JSON.parse(frm.doc.json_schema), null, 2)
					)}</pre>`,
					__("Tool Schema")
				);
			});
		}
	},
});
