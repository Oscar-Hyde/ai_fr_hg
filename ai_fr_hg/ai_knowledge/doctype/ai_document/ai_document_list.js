// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Document list. Tree View is `ai_document_tree.js` on this DocType.
 *
 * App bundles can be refreshed independently from a DocType script.  Do not
 * let that normal asset-loading window turn a native form/list action into an
 * uncaught JavaScript error.
 */
async function prompt_for_folder(options) {
	const picker = frappe.ai?.folder?.prompt_for_folder;
	if (picker) return picker(options);
	frappe.msgprint(__("The folder selector is still loading. Reload Desk and try again."));
	return null;
}

frappe.listview_settings["AI Document"] = {
	add_fields: ["status", "chunk_count", "embedded_chunk_count", "knowledge_base", "folder", "source_folder"],

	get_indicator(doc) {
		return [__(doc.status), frappe.ai.status_color(doc.status), "status,=," + doc.status];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Knowledge Explorer"), () =>
			frappe.set_route("knowledge-explorer")
		);
		listview.page.add_inner_button(__("Document Tree"), () => frappe.set_route("Tree", "AI Document"));

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

		// The public facade accepts stable AI Document identities; client code
		// never resolves source URLs or performs partial provenance writes.
		listview.page.add_actions_menu_item(__("Move Documents to Folder…"), async () => {
			const selected = listview.get_checked_items(true);
			if (!selected.length) {
				frappe.msgprint(__("Select at least one document."));
				return;
			}
			const target = await prompt_for_folder({
				default_folder: "Home",
				title: __("Move Documents to Folder"),
			});
			if (!target) return;

			const result = await frappe.xcall("ai_fr_hg.api.document_tree.bulk_move_nodes", {
				nodes: selected.map((name) => `document::${name}`),
				target_folder: target,
			});
			frappe.show_alert({
				message:
					result.status === "Queued"
						? __("Bulk move queued.")
						: __("{0} document(s) moved.", [result.moved?.length || 0]),
				indicator: result.status === "Queued" ? "blue" : "green",
			});
			listview.refresh();
		});
	},
};
