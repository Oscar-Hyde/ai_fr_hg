// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Document"] = {
	add_fields: ["status", "chunk_count", "embedded_chunk_count", "knowledge_base", "folder", "source_folder"],

	get_indicator(doc) {
		return [__(doc.status), frappe.ai.status_color(doc.status), "status,=," + doc.status];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Knowledge Explorer"), () =>
			frappe.set_route("knowledge-explorer")
		);
		listview.page.add_inner_button(__("File Manager"), () =>
			frappe.set_route("ai-file-manager")
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

		// Folder organization bulk actions (File & Folder §5, §7)
		listview.page.add_actions_menu_item(__("Move to Folder…"), async () => {
			const selected = listview.get_checked_items(true);
			if (!selected.length) {
				frappe.msgprint(__("Select at least one document."));
				return;
			}
			const target = await frappe.ai.folder.pick_folder({ default_folder: "Home" });
			let moved = 0;
			let errors = [];
			for (const name of selected) {
				try {
					const doc = await frappe.db.get_value("AI Document", name, ["source_file", "folder"]);
					const file_url = doc.message?.source_file || doc.source_file;
					if (!file_url) {
						errors.push(`${name}: no source file`);
						continue;
					}
					const file_name = await frappe.db.get_value("File", { file_url }, "name");
					const real_name = file_name.message?.name || file_name?.name || file_name;
					if (!real_name) {
						errors.push(`${name}: file record not found`);
						continue;
					}
					await frappe.xcall("ai_fr_hg.api.folders.move_file", {
						file_name: real_name,
						target_folder: target,
					});
					// Also update AI Document folder provenance
					await frappe.db.set_value("AI Document", name, { folder: target, source_folder: target });
					moved++;
				} catch (e) {
					errors.push(`${name}: ${e.message}`);
				}
			}
			if (errors.length) {
				frappe.msgprint({
					title: __("Move completed with errors"),
					message: `${moved} moved, ${errors.length} errors:<br>${errors.join("<br>")}`,
					indicator: "orange",
				});
			} else {
				frappe.show_alert({ message: __("{0} document(s) moved to {1}", [moved, target]), indicator: "green" });
			}
			listview.refresh();
		});
	},

	// Show folder in list row via formatters is handled by standard_filter; indicator already shows status
	formatters: {
		folder(value) {
			if (!value) return "";
			return `<span title="${frappe.utils.escape_html(value)}">📁 ${frappe.utils.escape_html(value.split("/").pop())}</span>`;
		},
	},
};
