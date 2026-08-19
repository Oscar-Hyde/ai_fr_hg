// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Folder selection for Frappe's native FileUploader.
 *
 * This module deliberately extends the stock uploader in place.  It does not
 * create a second upload surface or a separate file browser: Frappe's File
 * view remains responsible for browsing, search, sorting and navigation.
 *
 * Desk return resilience: the patch is idempotent, defers when the Desk is
 * still booting, and never throws during Desk init so returning to Desk
 * cannot brick the application.
 */

(() => {
	if (typeof frappe === "undefined") return;
	if (typeof frappe.provide !== "function") return;

	frappe.provide("frappe.ai");
	frappe.ai = frappe.ai || {};
	const folder = (frappe.ai.folder = frappe.ai.folder || {});
	const FALLBACK_FOLDER = "Home/Attachments";

	function attachment_context(options = {}) {
		return {
			doctype: options.doctype || null,
			docname: options.docname || null,
			fieldname: options.fieldname || null,
		};
	}

	function error_message(error) {
		try {
			return error?.message || error?._server_messages || __("The folder operation failed.");
		} catch (_e) {
			return "The folder operation failed.";
		}
	}

	Object.assign(folder, {
		async get_tree(root = "Home", max_depth = 6) {
			return frappe.xcall("ai_fr_hg.api.folders.get_tree", {
				root,
				max_depth,
				include_files: 0,
			});
		},

		async get_default_folder(doctype = null, docname = null) {
			try {
				const result = await frappe.xcall("ai_fr_hg.api.folders.get_default_folder", {
					doctype,
					docname,
				});
				return result.folder || FALLBACK_FOLDER;
			} catch (error) {
				// Returning to Desk should never block on a folder default
				// lookup failure; fall back and let the upload proceed.
				console.warn("AI folder: get_default_folder failed, using fallback", error);
				return FALLBACK_FOLDER;
			}
		},

		async get_breadcrumbs(file_or_folder) {
			return frappe.xcall("ai_fr_hg.api.folders.get_breadcrumbs", { file_or_folder });
		},

		async get_file_info(file_name) {
			return frappe.xcall("ai_fr_hg.api.folders.get_file_info", { file_name });
		},

		flatten_tree(node, depth = 0, rows = []) {
			if (!node || !node.is_folder) return rows;
			rows.push({
				value: node.name,
				label: `${"— ".repeat(depth)}${node.file_name || node.name}`,
			});
			(node.children || []).forEach((child) => {
				try {
					this.flatten_tree(child, depth + 1, rows);
				} catch (_e) {
					// Skip malformed subtree nodes rather than breaking Desk.
				}
			});
			return rows;
		},

		/**
		 * Use Frappe's standard Dialog and Link control for native list/form bulk
		 * actions.  The uploader itself receives the richer, inline tree select.
		 */
		prompt_for_folder({ title, default_folder } = {}) {
			return new Promise((resolve) => {
				let settled = false;
				const done = (value) => {
					if (settled) return;
					settled = true;
					resolve(value || null);
				};
				try {
					const dialog = new frappe.ui.Dialog({
						title: title || __("Select Destination Folder"),
						fields: [
							{
								fieldname: "folder",
								fieldtype: "Link",
								label: __("Folder"),
								options: "File",
								default: default_folder || "Home",
								reqd: 1,
								get_query() {
									return { filters: { is_folder: 1 } };
								},
							},
						],
						primary_action_label: __("Select"),
						primary_action(values) {
							dialog.hide();
							done(values.folder);
						},
					});
					dialog.$wrapper.on("hidden.bs.modal", () => done(null));
					dialog.show();
				} catch (error) {
					console.warn("AI folder: prompt_for_folder failed", error);
					done(null);
				}
			});
		},

		async confirm_upload(file_doc, destination, context) {
			const args = {
				folder: destination,
				attached_to_doctype: context.doctype,
				attached_to_name: context.docname,
				attached_to_field: context.fieldname,
			};
			if (file_doc?.name) {
				return frappe.xcall("ai_fr_hg.api.folders.set_file_folder", {
					file_name: file_doc.name,
					...args,
				});
			}
			if (file_doc?.file_url) {
				return frappe.xcall("ai_fr_hg.api.folders.upload_file_with_folder", {
					file_url: file_doc.file_url,
					...args,
				});
			}
			return null;
		},
	});

	function make_folder_aware_file_uploader(NativeFileUploader) {
		return class FolderAwareFileUploader extends NativeFileUploader {
		constructor(options = {}) {
			const original_on_success = options.on_success;
			const context = attachment_context(options);
			const state = {
				context,
				selected_folder: options.folder || FALLBACK_FOLDER,
				explicit_folder: Boolean(options.folder),
				initialization_error: null,
				selector: null,
			};

			// The native uploader sends this folder in its initial /upload_file
			// request.  The confirmation below is idempotent server-side and makes
			// the canonical folder service the authoritative final check.
			super({
				...options,
				folder: state.selected_folder,
				on_success: async (file_doc, response) => {
					try {
						await folder.confirm_upload(file_doc, state.selected_folder, context);
					} catch (error) {
						frappe.msgprint({
							title: __("Folder assignment failed"),
							message: error_message(error),
							indicator: "red",
						});
						return;
					}
					try {
						return original_on_success?.(file_doc, response);
					} catch (_e) {
						return null;
					}
				},
			});

			this._ai_folder_state = state;
			this._ai_folder_ready = this._initialize_folder_selector().catch((error) => {
				state.initialization_error = error;
				console.warn("AI folder: folder selector init failed", error);
				return null;
			});
		}

		async _initialize_folder_selector() {
			const state = this._ai_folder_state;
			try {
				if (!state.explicit_folder) {
					state.selected_folder = await folder.get_default_folder(
						state.context.doctype,
						state.context.docname
					);
				}
				this._set_native_destination(state.selected_folder);

				if (this.dialog) {
					await this._inject_folder_selector();
				}
			} catch (error) {
				// Non-fatal: keep the fallback folder and allow the upload
				// to proceed rather than breaking Desk on return.
				console.warn("AI folder: _initialize_folder_selector error", error);
				state.initialization_error = null;
				this._set_native_destination(state.selected_folder || FALLBACK_FOLDER);
			}
		}

		_set_native_destination(destination) {
			const state = this._ai_folder_state;
			state.selected_folder = destination;

			// FileUploader mounts a Vue component.  Its public proxy exposes the
			// component instance at `$`; updating the reactive props means the
			// native uploader serialises the selected folder in the initial request.
			try {
				const props = this.uploader?.$?.props;
				if (props) {
					props.folder = destination;
				}
			} catch (_e) {
				// Vue may not be mounted yet during Desk boot; ignore.
			}
		}

		async _inject_folder_selector() {
			const state = this._ai_folder_state;
			let $body;
			try {
				$body = this.dialog.$body?.length ? this.dialog.$body : $(this.wrapper);
			} catch (_e) {
				return;
			}
			if (!$body || !$body.length || state.selector) return;

			let tree;
			try {
				tree = await folder.get_tree("Home", 6);
			} catch (error) {
				console.warn("AI folder: get_tree failed, skipping selector injection", error);
				return;
			}
			let options;
			try {
				options = folder.flatten_tree(tree);
			} catch (_e) {
				options = [{ value: FALLBACK_FOLDER, label: FALLBACK_FOLDER }];
			}
			const $section = $("<div class='form-section'></div>");
			const $breadcrumbs = $("<div class='text-muted small'></div>");
			try {
				$body.prepend($section);
			} catch (_e) {
				return;
			}

			let destination;
			try {
				destination = frappe.ui.form.make_control({
					parent: $section,
					render_input: true,
					df: {
						fieldname: "ai_folder_destination",
						fieldtype: "Select",
						label: __("Destination Folder"),
						options,
						reqd: 1,
						description: __("Choose where every selected upload in this batch will be stored."),
					},
				});
				destination.set_value(state.selected_folder);
			} catch (error) {
				console.warn("AI folder: make_control failed", error);
				return;
			}

			const $new_folder = $("<div class='form-section'></div>").appendTo($section);
			const new_folder_name = frappe.ui.form.make_control({
				parent: $new_folder,
				render_input: true,
				df: {
					fieldname: "ai_new_folder_name",
					fieldtype: "Data",
					label: __("New Folder"),
					placeholder: __("Create inside the selected folder"),
				},
			});
			frappe.ui.form.make_control({
				parent: $new_folder,
				render_input: true,
				df: {
					fieldname: "ai_create_folder",
					fieldtype: "Button",
					label: __("Create Folder"),
					click: async () => {
						const name = (new_folder_name.get_value() || "").trim();
						if (!name) return;
						try {
							const created = await frappe.xcall("ai_fr_hg.api.folders.create_folder", {
								folder_name: name,
								parent_folder: state.selected_folder,
							});
							let refreshed_tree;
							try {
								refreshed_tree = await folder.get_tree("Home", 6);
								destination.df.options = folder.flatten_tree(refreshed_tree);
								destination.set_options();
							} catch (_err) {
								// Keep existing options if refresh fails.
							}
							new_folder_name.set_value("");
							this._set_native_destination(created.name);
							destination.set_value(created.name);
							await render_breadcrumbs(created.name);
							frappe.show_alert({
								message: __("Folder {0} created.", [created.file_name]),
								indicator: "green",
							});
						} catch (error) {
							frappe.msgprint({
								title: __("Could not create folder"),
								message: error_message(error),
								indicator: "red",
							});
						}
					},
				},
			});
			$section.append($breadcrumbs);

			const render_breadcrumbs = async (destination_folder) => {
				try {
					const crumbs = await folder.get_breadcrumbs(destination_folder);
					$breadcrumbs.text(crumbs.map((crumb) => crumb.file_name).join(" › "));
				} catch (_e) {
					$breadcrumbs.text("");
				}
			};
			try {
				destination.$input.on("change", async () => {
					this._set_native_destination(destination.get_value());
					try {
						await render_breadcrumbs(destination.get_value());
					} catch (error) {
						$breadcrumbs.text("");
					}
				});
				await render_breadcrumbs(state.selected_folder);
			} catch (_e) {
				// ignore
			}
			state.selector = destination;
		}

		async upload_files() {
			try {
				await this._ai_folder_ready;
			} catch (_e) {
				// _ai_folder_ready already captures and stores the error.
			}
			const state = this._ai_folder_state;
			if (state.initialization_error) {
				frappe.msgprint({
					title: __("Folder selector unavailable"),
					message: error_message(state.initialization_error),
					indicator: "red",
				});
				throw state.initialization_error;
			}

			// FileUploader can also be embedded without a dialog.  In that rare
			// native context, use a standard Frappe Dialog before sending rather
			// than silently filing the upload somewhere else.
			if (!this.dialog && !state.selector) {
				const selected = await folder.prompt_for_folder({
					default_folder: state.selected_folder,
				});
				if (!selected) return Promise.reject();
				this._set_native_destination(selected);
			}

			this._set_native_destination(state.selected_folder);
			try {
				return await super.upload_files();
			} catch (error) {
				console.warn("AI folder: upload_files failed", error);
				throw error;
			}
		}
		};
	}

	function patch_file_uploader(remaining_attempts = 20) {
		try {
			const NativeFileUploader = frappe.ui?.FileUploader;
			if (!NativeFileUploader) {
				if (remaining_attempts > 0) {
					setTimeout(() => patch_file_uploader(remaining_attempts - 1), 250);
				} else {
					console.warn("AI folder: FileUploader not found, folder selector disabled");
				}
				return;
			}
			if (NativeFileUploader.__ai_folder_selector__) return;

			const FolderAwareFileUploader = make_folder_aware_file_uploader(NativeFileUploader);
			FolderAwareFileUploader.__ai_folder_selector__ = true;
			FolderAwareFileUploader.__ai_native_file_uploader__ = NativeFileUploader;
			frappe.ui.FileUploader = FolderAwareFileUploader;
		} catch (error) {
			console.warn("AI folder: patch_file_uploader failed", error);
		}
	}

	folder.install_uploader_extension = patch_file_uploader;
	try {
		patch_file_uploader();
	} catch (_e) {
		// Never let the folder patch break Desk boot.
	}

	// Also attempt to re-patch when Desk is reloaded via SPA navigation.
	// Frappe's router fires `route_change` on returning to Desk; the
	// FileUploader class may have been restored to its native version by a
	// hot reload in development.
	if (typeof frappe.router !== "undefined" && frappe.router.on) {
		try {
			frappe.router.on("change", () => {
				if (!frappe.ui?.FileUploader?.__ai_folder_selector__) {
					patch_file_uploader(5);
				}
			});
		} catch (_e) {
			// ignore
		}
	}
})();
