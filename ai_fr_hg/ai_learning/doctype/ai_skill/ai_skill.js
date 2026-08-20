// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Skill", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(
			frm.doc.enabled ? __("Enabled") : __("Disabled"),
			frm.doc.enabled ? "green" : "grey"
		);

		if (frm.doc.enabled) {
			frm.add_custom_button(__("Disable"), () => {
				frappe.confirm(
					__("Disable this skill so it is no longer injected into prompts?"),
					async () => {
						await frm.call("disable");
						frappe.show_alert({
							message: __("Skill disabled"),
							indicator: "orange",
						});
						frm.reload_doc();
					}
				);
			});
		} else {
			frm.add_custom_button(__("Enable"), () => {
				frappe.confirm(__("Re-enable this skill?"), async () => {
					await frm.call("enable");
					frappe.show_alert({
						message: __("Skill enabled"),
						indicator: "green",
					});
					frm.reload_doc();
				});
			}).addClass("btn-primary");
		}

		if (frm.doc.source_candidate) {
			frm.add_custom_button(__("Source Candidate"), () =>
				frappe.set_route("Form", "AI Knowledge Candidate", frm.doc.source_candidate)
			);
		}
	},
});
