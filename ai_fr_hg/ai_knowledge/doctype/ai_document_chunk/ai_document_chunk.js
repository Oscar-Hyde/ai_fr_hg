// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Document Chunk", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(
			__("Chunk {0}", [frm.doc.chunk_index + 1]),
			frm.doc.embedding ? "green" : "grey"
		);

		if (frm.doc.document) {
			frm.add_custom_button(__("Document"), () =>
				frappe.set_route("Form", "AI Document", frm.doc.document)
			);
		}

		if (frm.doc.knowledge_base) {
			frm.add_custom_button(__("Knowledge Base"), () =>
				frappe.set_route("Form", "AI Knowledge Base", frm.doc.knowledge_base)
			);
		}

		// Show embedding dimension info
		if (frm.doc.embedding_dimensions > 0) {
			frm.set_df_property("embedding", "hidden", 1);
		}

		frm.disable_save();
	},
});
