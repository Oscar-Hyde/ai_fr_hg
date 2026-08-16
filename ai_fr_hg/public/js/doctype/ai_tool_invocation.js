// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Tool Invocation", {
	refresh(frm) {
		frm.page.set_indicator(frm.doc.status, frappe.ai.status_color(frm.doc.status));

		if (frm.doc.status !== "Pending Approval") return;
		if (!frappe.user.has_role(["AI Manager", "System Manager"])) return;

		frm.add_custom_button(__("Approve and Run"), () => {
			frappe.confirm(
				__("Run the tool {0} with the recorded arguments?", [frm.doc.tool]),
				async () => {
					frappe.dom.freeze(__("Running..."));
					try {
						const result = await frappe.xcall("ai_fr_hg.ai.tools.approve_invocation", {
							invocation: frm.doc.name,
						});
						frappe.dom.unfreeze();
						frappe.msgprint({
							title: __("Tool Result"),
							indicator: result.status === "Success" ? "green" : "red",
							message: `<pre>${frappe.utils.escape_html(
								JSON.stringify(result.result || result.error, null, 2)
							)}</pre>`,
						});
						frm.reload_doc();
					} catch (error) {
						frappe.dom.unfreeze();
					}
				}
			);
		}).addClass("btn-primary");

		frm.add_custom_button(__("Reject"), async () => {
			await frappe.xcall("ai_fr_hg.ai.tools.reject_invocation", {
				invocation: frm.doc.name,
			});
			frm.reload_doc();
		});
	},
});
