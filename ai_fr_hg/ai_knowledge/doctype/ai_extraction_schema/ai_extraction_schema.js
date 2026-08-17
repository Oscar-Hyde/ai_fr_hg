// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Extraction Schema", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Test on Sample Text"), () => {
			frappe.prompt(
				{
					fieldtype: "Text",
					fieldname: "text",
					label: __("Sample Text"),
					reqd: 1,
				},
				async (values) => {
					frappe.dom.freeze(__("Extracting..."));
					try {
						const result = await frm.call("test_extraction", { text: values.text });
						frappe.dom.unfreeze();
						frappe.msgprint({
							title: __("Extracted Data"),
							wide: true,
							message: `<pre>${frappe.utils.escape_html(
								JSON.stringify(result.message, null, 2)
							)}</pre>`,
						});
					} catch (error) {
						frappe.dom.unfreeze();
					}
				},
				__("Test Extraction"),
				__("Extract")
			);
		}).addClass("btn-primary");
	},
});
