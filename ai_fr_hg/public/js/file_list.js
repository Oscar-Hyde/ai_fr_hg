// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Native File view extension for AI_FR_HG folder organization.
 *
 * Frappe's FileView remains the browsing surface.  This file only replaces the
 * FileView actions that would otherwise bypass the canonical folder service.
 * It adds actions through Frappe's standard menu and list bulk-action APIs;
 * it does not render a parallel tree, grid, toolbar, search box, or sidebar.
 */

(() => {
	if (typeof frappe === "undefined") return;

	function selected_names(listview) {
		return (listview.get_checked_items(true) || []).map(String);
	}

	function selected_or_message(listview, singular = false) {
		const names = selected_names(listview);
		if (!names.length || (singular && names.length !== 1)) {
			frappe.msgprint(
				singular ? __("Select exactly one file or folder.") : __("Select at least one file or folder.")
			);
			return null;
		}
		return names;
	}

	async function select_destination(default_folder, title) {
		const picker = frappe.ai?.folder?.prompt_for_folder;
		if (!picker) {
			frappe.throw(__("The folder selector is not available. Reload Desk and try again."));
		}
		return picker({ default_folder, title });
	}

	async function item_info(name) {
		return frappe.xcall("ai_fr_hg.api.folders.get_file_info", { file_name: name });
	}

	async function create_folder(parent_folder) {
		return new Promise((resolve) => {
			frappe.prompt(
				[
					{
						fieldname: "folder_name",
						fieldtype: "Data",
						label: __("Folder Name"),
						reqd: 1,
					},
				],
				async (values) => {
					try {
						await frappe.xcall("ai_fr_hg.api.folders.create_folder", {
							folder_name: values.folder_name,
							parent_folder,
						});
						resolve(true);
					} catch (error) {
						frappe.msgprint({
							title: __("Could not create folder"),
							message: error.message,
							indicator: "red",
						});
						resolve(false);
					}
				},
				__("New Folder"),
				__("Create")
			);
		});
	}

	function install_native_paste_override() {
		const manager = frappe.file_manager;
		if (!manager || manager.__ai_folder_paste__) return Boolean(manager);

		manager.__ai_folder_paste__ = true;
		manager.paste = async (target_folder) => {
			const items = manager.files_to_move || [];
			if (!items.length || !manager.old_folder) {
				manager.cut([], null);
				return;
			}

			const result = await frappe.xcall("ai_fr_hg.api.folders.bulk_move", {
				file_names: items.map((item) => item.name),
				target_folder,
				enqueue: items.length > 20 ? 1 : 0,
			});
			manager.cut([], null);

			if (result.status === "Queued") {
				frappe.show_alert({
					message: __("Bulk move queued. Progress will be recorded in the audit trail."),
					indicator: "blue",
				});
				return result;
			}
			if (result.errors?.length) {
				frappe.msgprint({
					title: __("Move completed with errors"),
					message: result.errors.map((entry) => `${entry.file}: ${entry.error}`).join("<br>"),
					indicator: "orange",
				});
			}
			return result;
		};
		return true;
	}

	function install_file_view_menu_override() {
		const FileView = frappe.views?.FileView;
		if (!FileView || FileView.prototype.__ai_folder_menu__) return Boolean(FileView);

		const original_file_menu_items = FileView.prototype.file_menu_items;
		FileView.prototype.file_menu_items = function () {
			const items = original_file_menu_items.call(this).filter((item) => item.label !== __("New Folder"));
			items.splice(1, 0, {
				label: __("New Folder"),
				action: async () => {
					if (await create_folder(this.current_folder)) this.refresh();
				},
			});
			return items;
		};
		FileView.prototype.__ai_folder_menu__ = true;
		return true;
	}

	const core_settings = frappe.listview_settings["File"] || {};
	const core_onload = core_settings.onload;
	frappe.listview_settings["File"] = {
		...core_settings,
		onload(listview) {
			core_onload?.(listview);

			listview.page.add_actions_menu_item(__("Move to Folder…"), async () => {
				const names = selected_or_message(listview);
				if (!names) return;
				const target = await select_destination(listview.current_folder || "Home");
				if (!target) return;
				const result = await frappe.xcall("ai_fr_hg.api.folders.bulk_move", {
					file_names: names,
					target_folder: target,
					enqueue: names.length > 20 ? 1 : 0,
				});
				frappe.show_alert({
					message:
						result.status === "Queued"
							? __("Bulk move queued.")
							: __("{0} item(s) moved.", [result.moved?.length || 0]),
					indicator: result.status === "Queued" ? "blue" : "green",
				});
				listview.refresh();
			});

			listview.page.add_actions_menu_item(__("Copy Files to Folder…"), async () => {
				const names = selected_or_message(listview);
				if (!names) return;
				const target = await select_destination(listview.current_folder || "Home");
				if (!target) return;

				const details = await Promise.all(names.map(item_info));
				const files = details.filter((item) => !item.is_folder);
				if (!files.length) {
					frappe.msgprint(__("Only files can be copied. Folders are moved instead."));
					return;
				}
				const errors = [];
				for (const file of files) {
					try {
						await frappe.xcall("ai_fr_hg.api.folders.copy_file", {
							file_name: file.name,
							target_folder: target,
						});
					} catch (error) {
						errors.push(`${file.file_name}: ${error.message}`);
					}
				}
				if (errors.length) {
					frappe.msgprint({ title: __("Copy completed with errors"), message: errors.join("<br>"), indicator: "orange" });
				} else {
					frappe.show_alert({ message: __("{0} file(s) copied.", [files.length]), indicator: "green" });
				}
				listview.refresh();
			});

			listview.page.add_actions_menu_item(__("Rename…"), async () => {
				const names = selected_or_message(listview, true);
				if (!names) return;
				const item = await item_info(names[0]);
				frappe.prompt(
					[
						{
							fieldname: "new_name",
							fieldtype: "Data",
							label: __("New Name"),
							default: item.file_name,
							reqd: 1,
						},
					],
					async (values) => {
						await frappe.xcall(
							item.is_folder
								? "ai_fr_hg.api.folders.rename_folder"
								: "ai_fr_hg.api.folders.rename_file",
							item.is_folder
								? { folder_name: item.name, new_name: values.new_name }
								: { file_name: item.name, new_name: values.new_name }
						);
						listview.refresh();
					},
					__("Rename"),
					__("Save")
				);
			});

			listview.page.add_actions_menu_item(__("Delete…"), async () => {
				const names = selected_or_message(listview);
				if (!names) return;
				frappe.confirm(
					__(
						"Delete {0} selected item(s)? Non-empty folders are deleted recursively. This cannot be undone.",
						[names.length]
					),
					async () => {
						const details = await Promise.all(names.map(item_info));
						for (const item of details) {
							await frappe.xcall(
								item.is_folder
									? "ai_fr_hg.api.folders.delete_folder"
									: "ai_fr_hg.api.folders.delete_file",
								item.is_folder ? { folder_name: item.name, recursive: 1 } : { file_name: item.name }
							);
						}
						listview.refresh();
					}
				);
			});

			listview.page.add_actions_menu_item(__("Add Folder to Favorites"), async () => {
				const names = selected_or_message(listview, true);
				if (!names) return;
				const item = await item_info(names[0]);
				if (!item.is_folder) {
					frappe.msgprint(__("Only folders can be added to favorites."));
					return;
				}
				await frappe.xcall("ai_fr_hg.api.folders.add_favorite", { folder: item.name });
				frappe.show_alert({ message: __("Folder added to favorites."), indicator: "green" });
			});

			listview.page.add_actions_menu_item(__("Remove Folder from Favorites"), async () => {
				const names = selected_or_message(listview, true);
				if (!names) return;
				const item = await item_info(names[0]);
				if (!item.is_folder) {
					frappe.msgprint(__("Only folders can be removed from favorites."));
					return;
				}
				await frappe.xcall("ai_fr_hg.api.folders.remove_favorite", { folder: item.name });
				frappe.show_alert({ message: __("Folder removed from favorites."), indicator: "green" });
			});
		},
	};

	function install_extensions(remaining_attempts = 20) {
		const pasted = install_native_paste_override();
		const menu = install_file_view_menu_override();
		if ((!pasted || !menu) && remaining_attempts > 0) {
			setTimeout(() => install_extensions(remaining_attempts - 1), 250);
		}
	}

	install_extensions();
})();
