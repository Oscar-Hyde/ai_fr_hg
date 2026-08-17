// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Knowledge Candidate", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.status, frappe.ai.status_color(frm.doc.status));

		const decided = ["Approved", "Rejected"].includes(frm.doc.status);
		if (!decided) {
			frm.add_custom_button(__("Approve"), async () => {
				frappe.dom.freeze(__("Promoting to memory / skill..."));
				try {
					const result = await frm.call("approve");
					frappe.dom.unfreeze();
					frappe.show_alert({
						message: __("{0} promoted to {1}.", [result.message.promoted_name, __(result.message.promoted_to)]),
						indicator: "green",
					});
					frm.reload_doc();
				} catch (error) {
					frappe.dom.unfreeze();
				}
			}).addClass("btn-primary");

			frm.add_custom_button(__("Reject"), () => {
				frappe.confirm(__("Reject this candidate so it is never learned?"), async () => {
					await frm.call("reject");
					frappe.show_alert({ message: __("Rejected"), indicator: "orange" });
					frm.reload_doc();
				});
			});
		}

		if (frm.doc.status === "Conflict" && frm.doc.conflicts_summary) {
			frm.dashboard.set_headline(
				`<span class="text-warning">${frappe.utils.escape_html(
					__("Potential conflicts detected: ") + frm.doc.conflicts_summary
				)}</span>`
			);
		}
	},
});
