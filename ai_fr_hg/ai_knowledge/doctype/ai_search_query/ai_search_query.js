// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Search Query", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.results_count !== undefined) {
			frm.dashboard.set_headline(
				__("{0} result(s) found in {1} ms", [
					frm.doc.results_count || 0,
					frm.doc.duration_ms || 0,
				])
			);
		}

		frm.disable_save();
	},
});
