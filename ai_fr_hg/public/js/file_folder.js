// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Canonical folder & file organization client helpers.
 *
 * Extends Frappe's native attachment/foler mechanism without duplicating it:
 *  - Every upload goes through the single folder selector backed by the
 *    canonical server service (ai_fr_hg.ai.folders).
 *  - Folder choice is persisted server-side (File.folder), not merely client state.
 *  - Permission, collision and circular-nesting errors are surfaced typed from the server.
 *
 * This module patches ``frappe.ui.FileUploader`` to inject a folder selector
 * so that *every* attachment point in the Desk lets the user choose its
 * destination folder (§4). It also exposes utilities for the dedicated
 * file manager page (ai_file_manager) and breadcrumb/tree views.
 */

frappe.provide("frappe.ai.folder");

Object.assign(frappe.ai.folder, {
	/** Cached folder tree promise */
	_tree: null,
	default_folder: null,

	/** Fetch the folder tree from the server (permission-filtered). */
	async get_tree(root = "Home", max_depth = 4) {
		return await frappe.xcall("ai_fr_hg.api.folders.get_tree", {
			root,
			max_depth,
			include_files: 0,
		});
	},

	async get_default_folder(doctype = null, docname = null) {
		const result = await frappe.xcall("ai_fr_hg.api.folders.get_default_folder", {
			doctype,
			docname,
		});
		return result.folder || "Home/Attachments";
	},

	async get_folder_info(folder) {
		return await frappe.xcall("ai_fr_hg.api.folders.get_folder_info", { folder_name: folder });
	},

	async list_folder_contents(folder, opts = {}) {
		return await frappe.xcall("ai_fr_hg.api.folders.list_folder_contents", {
			folder,
			include_files: opts.include_files ?? 1,
			include_folders: opts.include_folders ?? 1,
			limit: opts.limit ?? 50,
			offset: opts.offset ?? 0,
			search_text: opts.search_text || null,
		});
	},

	async get_breadcrumbs(file_or_folder) {
		return await frappe.xcall("ai_fr_hg.api.folders.get_breadcrumbs", {
			file_or_folder,
		});
	},

	async search(query, folder = null) {
		return await frappe.xcall("ai_fr_hg.api.folders.search", {
			query,
			folder,
		});
	},

	/** Render a flat list of folder options from a tree for a <select>. */
	flatten_tree(node, depth = 0, out = []) {
		if (!node) return out;
		out.push({
			value: node.name,
			label: `${"— ".repeat(depth)} ${node.file_name || node.name}`.trim(),
			depth,
		});
		(node.children || []).forEach((child) => {
			if (child.is_folder) this.flatten_tree(child, depth + 1, out);
		});
		return out;
	},

	/** Show a folder picker dialog that resolves to the chosen folder. */
	async pick_folder(opts = {}) {
		const current = opts.default_folder || (await this.get_default_folder(opts.doctype, opts.docname));
		const tree = await this.get_tree("Home", 6);
		const options = this.flatten_tree(tree);

		return new Promise((resolve) => {
			const dialog = new frappe.ui.Dialog({
				title: __("Select Destination Folder"),
				fields: [
					{
						fieldname: "folder",
						fieldtype: "Select",
						label: __("Destination Folder"),
						options: options.map((o) => ({ label: o.label, value: o.value })),
						reqd: 1,
						default: current,
						description: __("Browse the hierarchy or search. Create a new folder inline if needed."),
					},
					{
						fieldname: "new_folder_name",
						fieldtype: "Data",
						label: __("Or Create New Folder Here"),
						placeholder: __("New folder name (optional)"),
					},
					{
						fieldname: "search",
						fieldtype: "Data",
						label: __("Search Folders"),
						placeholder: __("Type to filter folders..."),
					},
				],
				primary_action_label: __("Select"),
			});

			// Live filter of the select options when user types in search field
			dialog.fields_dict.search.df.onchange = () => {
				const term = (dialog.get_value("search") || "").toLowerCase();
				const select = dialog.fields_dict.folder;
				const filtered = term
					? options.filter((o) => o.label.toLowerCase().includes(term))
					: options;
				select.df.options = filtered.map((o) => ({ label: o.label, value: o.value }));
				// Re-render the select control
				if (select.refresh) select.refresh();
			};

			dialog.set_primary_action(async (values) => {
				let chosen = values.folder;
				const newName = (values.new_folder_name || "").trim();
				if (newName) {
					try {
						const result = await frappe.xcall("ai_fr_hg.api.folders.create_folder", {
							folder_name: newName,
							parent_folder: chosen,
						});
						chosen = result.name;
						frappe.show_alert({ message: __("Folder {0} created.", [chosen]), indicator: "green" });
					} catch (e) {
						frappe.msgprint({ title: __("Could not create folder"), message: e.message, indicator: "red" });
						return;
					}
				}
				dialog.hide();
				resolve(chosen);
			});
			dialog.show();
		});
	},

	/** Show inline folder creation inside an existing Uploader dialog. */
	async inject_folder_selector(uploader) {
		if (!uploader || uploader._folder_selector_injected) return;
		uploader._folder_selector_injected = true;

		// Resolve default folder based on attached document context
		const doctype = uploader.args?.doctype || uploader.dialog?.__doctype || null;
		const docname = uploader.args?.docname || uploader.dialog?.__docname || null;
		const default_folder = await this.get_default_folder(doctype, docname);
		uploader._selected_folder = uploader.args?.folder || default_folder;

		const tree = await this.get_tree("Home", 6);
		const options = this.flatten_tree(tree);

		// Find a place to inject the control: FileUploader dialog has a body; append after file input
		const $wrapper = uploader.dialog?.$wrapper || uploader.dialog?.$body?.parent();
		if (!$wrapper || !$wrapper.length) return;

		const $control = $(`
			<div class="ai-folder-selector" style="margin: 12px 0; padding: 12px; border: 1px solid var(--border-color); border-radius: var(--border-radius); background: var(--control-bg);">
				<label class="control-label" style="font-weight: 600;">${__("Destination Folder")}</label>
				<div class="d-flex gap-2" style="gap:8px;">
					<select class="form-control ai-folder-select" style="flex:1;">
						${options.map((o) => `<option value="${frappe.utils.escape_html(o.value)}" ${o.value === uploader._selected_folder ? "selected" : ""}>${frappe.utils.escape_html(o.label)}</option>`).join("")}
					</select>
					<button class="btn btn-default btn-sm ai-folder-new">${__("New Folder")}</button>
				</div>
				<div class="ai-folder-breadcrumbs small text-muted" style="margin-top:6px;"></div>
				<div class="ai-folder-actions small" style="margin-top:8px; display:none;">
					<input type="text" class="form-control input-sm ai-new-folder-input" placeholder="${__("New folder name")}" style="max-width:220px; display:inline-block;">
					<button class="btn btn-xs btn-primary ai-create-folder">${__("Create")}</button>
					<button class="btn btn-xs btn-default ai-cancel-create">${__("Cancel")}</button>
				</div>
			</div>
		`);

		// Insert before the upload button row
		const $body = uploader.dialog.$body || $wrapper.find(".modal-body");
		if ($body && $body.length) {
			$body.prepend($control);
		} else {
			$wrapper.prepend($control);
		}

		const renderBreadcrumbs = async (folder) => {
			try {
				const crumbs = await this.get_breadcrumbs(folder);
				$control.find(".ai-folder-breadcrumbs").html(
					crumbs.map((c) => frappe.utils.escape_html(c.file_name)).join(' <span class="text-muted">›</span> ')
				);
			} catch (_) {}
		};
		renderBreadcrumbs(uploader._selected_folder);

		$control.find(".ai-folder-select").on("change", function () {
			uploader._selected_folder = $(this).val();
			renderBreadcrumbs(uploader._selected_folder);
		});

		$control.find(".ai-folder-new").on("click", () => {
			$control.find(".ai-folder-actions").show();
			$control.find(".ai-new-folder-input").focus();
		});
		$control.find(".ai-cancel-create").on("click", () => {
			$control.find(".ai-folder-actions").hide();
			$control.find(".ai-new-folder-input").val("");
		});
		$control.find(".ai-create-folder").on("click", async () => {
			const newName = $control.find(".ai-new-folder-input").val().trim();
			if (!newName) return;
			try {
				const result = await frappe.xcall("ai_fr_hg.api.folders.create_folder", {
					folder_name: newName,
					parent_folder: uploader._selected_folder,
				});
				frappe.show_alert({ message: __("Folder {0} created.", [result.name]), indicator: "green" });
				// Refresh tree and select the new folder
				const newTree = await frappe.ai.folder.get_tree("Home", 6);
				const newOptions = frappe.ai.folder.flatten_tree(newTree);
				const $select = $control.find(".ai-folder-select").empty();
				newOptions.forEach((o) => {
					$select.append(
						$("<option>").val(o.value).text(o.label).prop("selected", o.value === result.name)
					);
				});
				uploader._selected_folder = result.name;
				$control.find(".ai-folder-actions").hide();
				$control.find(".ai-new-folder-input").val("");
				renderBreadcrumbs(uploader._selected_folder);
			} catch (e) {
				frappe.msgprint({ title: __("Could not create folder"), message: e.message, indicator: "red" });
			}
		});

		// Monkey-patch the success path to re-file the uploaded File into the chosen folder server-side
		const original_on_success = uploader.args?.on_success || uploader.on_success;
		const wrapped_on_success = async (file_doc) => {
			// Frappe's FileUploader calls on_success with the File doc JSON
			let file_url = file_doc?.file_url || file_doc?.fileUrl;
			let file_name = file_doc?.name;
			if (!file_url && file_name) {
				// Try to get file_url from doc if not in callback payload
				try {
					const info = await frappe.xcall("ai_fr_hg.api.folders.get_file_info", { file_name });
					file_url = info.file_url;
				} catch (_) {}
			}
			if (file_url && uploader._selected_folder) {
				try {
					await frappe.xcall("ai_fr_hg.api.folders.upload_file_with_folder", {
						file_url,
						folder: uploader._selected_folder,
						attached_to_doctype: uploader.args?.doctype || null,
						attached_to_name: uploader.args?.docname || null,
					});
				} catch (e) {
					frappe.show_alert({ message: e.message, indicator: "red" });
				}
			}
			if (typeof original_on_success === "function") {
				return original_on_success(file_doc);
			}
		};

		// Attach wrapper to both possible hook points
		if (uploader.args) uploader.args.on_success = wrapped_on_success;
		uploader.on_success = wrapped_on_success;

		// Also wrap the internal upload method if exists to carry folder flag
		if (uploader.upload) {
			const origUpload = uploader.upload.bind(uploader);
			uploader.upload = function (...args) {
				// Pass folder via flags so server sees it even if re-file wrapper missed
				if (uploader.args) uploader.args.folder = uploader._selected_folder;
				return origUpload(...args);
			};
		}
	},

	/** Initialize global patch once Desk is ready. */
	init() {
		if (this._init_done) return;
		this._init_done = true;

		const tryPatch = () => {
			if (!window.frappe || !frappe.ui || !frappe.ui.FileUploader) {
				setTimeout(tryPatch, 600);
				return;
			}
			const Original = frappe.ui.FileUploader;
			const self = this;

			// Wrap construction
			frappe.ui.FileUploader = function (opts) {
				// Resolve default folder eagerly via server to ensure server truth
				const instance = new Original(opts);
				// Inject selector after dialog is built (next tick)
				setTimeout(() => self.inject_folder_selector(instance), 250);
				return instance;
			};
			// Preserve prototype and static helpers
			frappe.ui.FileUploader.prototype = Original.prototype;
			Object.assign(frappe.ui.FileUploader, Original);

			console.log("[ai_fr_hg] FileUploader patched with folder selector");
		};
		tryPatch();
	},
});

// Auto-init on desk load
if (typeof frappe !== "undefined") {
	frappe.after_ajax ? frappe.after_ajax(() => frappe.ai.folder.init()) : setTimeout(() => frappe.ai.folder.init(), 1000);
}
