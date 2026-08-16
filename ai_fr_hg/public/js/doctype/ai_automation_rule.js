// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Automation Rule", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Test on a Document"), () => {
			frappe.prompt(
				{
					fieldtype: "Link",
					fieldname: "docname",
					label: __("Document"),
					options: frm.doc.document_type,
					reqd: 1,
				},
				async (values) => {
					frappe.dom.freeze(__("Running rule..."));
					try {
						const result = await frm.call("test_rule", { docname: values.docname });
						frappe.dom.unfreeze();
						frappe.msgprint({
							title: __("Rule Result"),
							indicator: result.message.status === "Success" ? "green" : "red",
							message: `<pre>${frappe.utils.escape_html(
								JSON.stringify(result.message, null, 2)
							)}</pre>`,
						});
						frm.reload_doc();
					} catch (error) {
						frappe.dom.unfreeze();
					}
				},
				__("Test Automation Rule"),
				__("Run")
			);
		}).addClass("btn-primary");

		if (frm.doc.failure_count) {
			frm.dashboard.add_indicator(__("{0} failures", [frm.doc.failure_count]), "red");
		}
		if (frm.doc.last_error) {
			frm.dashboard.set_headline(
				`<span class="text-danger">${frappe.utils.escape_html(frm.doc.last_error)}</span>`
			);
		}
	},

	document_type(frm) {
		frm.set_value("target_field", "");
		frm.set_value("source_field", "");
	},
});
