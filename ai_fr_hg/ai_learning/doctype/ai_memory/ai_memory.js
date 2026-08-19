// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Memory", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.status, frm.doc.status === "Active" ? "green" : "grey");

		if (frm.doc.status === "Active") {
			frm.add_custom_button(__("Archive"), () => {
				frappe.confirm(
					__("Archive this memory so it no longer influences AI answers?"),
					async () => {
						await frm.call("archive");
						frappe.show_alert({
							message: __("Memory archived"),
							indicator: "orange",
						});
						frm.reload_doc();
					}
				);
			});
		}

		if (frm.doc.source_candidate) {
			frm.add_custom_button(__("Source Candidate"), () =>
				frappe.set_route("Form", "AI Knowledge Candidate", frm.doc.source_candidate)
			);
		}

		if (frm.doc.embedding_dimensions > 0) {
			frm.set_df_property("embedding_dimensions", "hidden", 0);
			frm.set_df_property("embedding_model", "hidden", 0);
		}
	},
});
