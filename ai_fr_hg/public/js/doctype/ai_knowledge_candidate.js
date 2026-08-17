// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Knowledge Candidate", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.status, frappe.ai.status_color(frm.doc.status));

		const decided = ["Approved", "Rejected"].includes(frm.doc.status);
		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Validate & Test"), async () => {
				const result = await frm.call("validate_and_test");
				frappe.show_alert({
					message: __("Candidate test result: {0}", [result.message.status]),
					indicator: result.message.valid ? "green" : "red",
				});
				frm.reload_doc();
			}).addClass("btn-primary");
		}

		if (["Validated", "Conflict"].includes(frm.doc.status)) {
			const promote = async (notes = null) => {
				frappe.dom.freeze(__("Promoting to memory / skill..."));
				try {
					const result = await frm.call("approve", { notes });
					frappe.show_alert({
						message: __("{0} promoted to {1}.", [
							result.message.promoted_name,
							__(result.message.promoted_to),
						]),
						indicator: "green",
					});
					frm.reload_doc();
				} finally {
					frappe.dom.unfreeze();
				}
			};

			frm.add_custom_button(__("Approve"), () => {
				if (frm.doc.status !== "Conflict") {
					return promote();
				}
				frappe.prompt(
					{
						fieldname: "notes",
						fieldtype: "Small Text",
						label: __("Conflict Override Notes"),
						reqd: 1,
					},
					(values) => promote(values.notes),
					__("Approve Conflicting Candidate"),
					__("Approve")
				);
			}).addClass("btn-primary");
		}

		if (!decided) {
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
