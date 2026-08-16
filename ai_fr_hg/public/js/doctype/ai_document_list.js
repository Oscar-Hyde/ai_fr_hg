// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Document"] = {
	add_fields: ["status", "chunk_count", "embedded_chunk_count", "knowledge_base"],

	get_indicator(doc) {
		return [__(doc.status), frappe.ai.status_color(doc.status), "status,=," + doc.status];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Knowledge Explorer"), () =>
			frappe.set_route("knowledge-explorer")
		);

		listview.page.add_actions_menu_item(__("Re-process"), () => {
			const selected = listview.get_checked_items(true);
			if (!selected.length) {
				frappe.msgprint(__("Select at least one document."));
				return;
			}
			frappe.confirm(__("Re-process {0} document(s)?", [selected.length]), async () => {
				for (const name of selected) {
					await frappe.xcall("ai_fr_hg.api.knowledge.reprocess_document", {
						document: name,
						force: 1,
					});
				}
				frappe.show_alert({
					message: __("{0} document(s) queued.", [selected.length]),
					indicator: "blue",
				});
				listview.refresh();
			});
		});
	},
};
