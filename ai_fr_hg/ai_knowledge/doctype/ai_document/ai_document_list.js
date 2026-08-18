// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
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
		listview.page.add_inner_button(__("Files"), () => frappe.set_route("List", "File", "Home"));

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

		// This is a native List View bulk action.  Source File resolution is
		// presentation-only; the canonical bulk service owns all validation,
		// permission checks, persistence and provenance updates.
		listview.page.add_actions_menu_item(__("Move Source Files to Folder…"), async () => {
			const selected = listview.get_checked_items(true);
			if (!selected.length) {
				frappe.msgprint(__("Select at least one document."));
				return;
			}
			const target = await prompt_for_folder({
				default_folder: "Home",
				title: __("Move Source Files to Folder"),
			});
			if (!target) return;

			const file_names = [];
			const missing = [];
			for (const name of selected) {
				const document = await frappe.db.get_value("AI Document", name, "source_file");
				const file_url = document.message?.source_file || document.source_file;
				const file = file_url && (await frappe.db.get_value("File", { file_url }, "name"));
				const file_name = file?.message?.name || file?.name || file;
				if (file_name) file_names.push(file_name);
				else missing.push(name);
			}
			if (!file_names.length) {
				frappe.msgprint(__("None of the selected documents has a source File."));
				return;
			}

			const result = await frappe.xcall("ai_fr_hg.api.folders.bulk_move", {
				file_names,
				target_folder: target,
				enqueue: file_names.length > 20 ? 1 : 0,
			});
			const skipped = missing.length ? ` ${__("{0} document(s) had no source File.", [missing.length])}` : "";
			frappe.show_alert({
				message:
					result.status === "Queued"
						? __("Bulk move queued.") + skipped
						: __("{0} source file(s) moved.", [result.moved?.length || 0]) + skipped,
				indicator: result.status === "Queued" ? "blue" : "green",
			});
			listview.refresh();
		});
	},
};
