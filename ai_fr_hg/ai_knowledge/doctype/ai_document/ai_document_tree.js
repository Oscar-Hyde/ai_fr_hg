// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Document Tree View — owned by the AI Document DocType.
 *
 * Frappe v17 Tree View (`frappe.treeview_settings`) renders and expands
 * nodes. Mutations, permission checks, locking, and audit stay in
 * `ai_fr_hg.ai.document_tree` and are reached only through the thin
 * `ai_fr_hg.api.document_tree` facade. This file does not invent a second
 * tree store or permission model.
 */

frappe.provide("frappe.treeview_settings");

(() => {
	const METHOD = "ai_fr_hg.api.document_tree";
	const ROOT = "AI Documents";
	const selected = new Map();
	let view;
	let location_wrapper;
	let root_capabilities = {};

	// ------------------------------------------------------------------
	// Node payload (server-authored; Tree View only keeps `value` on root)
	// ------------------------------------------------------------------

	const data = (node) =>
		node.is_root ? { ...(node.data || {}), ...root_capabilities } : node.data || {};
	const node_type = (node) => (node.is_root ? "root" : data(node).node_type);
	const folder_for = (node) => {
		if (node.is_root) return "Home";
		return node_type(node) === "folder" ? data(node).value : data(node).folder || "Home";
	};
	const parent_for = (node) => data(node).folder || "Home";

	// ------------------------------------------------------------------
	// Native Desk dialogs
	// ------------------------------------------------------------------

	function prompt_name(title, label, value = "") {
		return new Promise((resolve) => {
			frappe.prompt(
				[{ fieldtype: "Data", fieldname: "name", label, reqd: 1, default: value }],
				(values) => resolve(values.name),
				title,
				__("Continue")
			);
		});
	}

	function prompt_destination(title, default_folder = "Home", include_name = false) {
		return new Promise((resolve) => {
			const fields = [
				{
					fieldtype: "Link",
					fieldname: "target_folder",
					options: "File",
					label: __("Destination Folder"),
					default: default_folder || "Home",
					reqd: 1,
					get_query: () => ({ filters: { is_folder: 1 } }),
				},
			];
			if (include_name) {
				fields.push({
					fieldtype: "Data",
					fieldname: "new_name",
					label: __("New Name"),
					description: __("Leave empty to generate a collision-safe copy name."),
				});
			}
			frappe.prompt(fields, resolve, title, __("Continue"));
		});
	}

	function confirm_action(message) {
		return new Promise((resolve) =>
			frappe.confirm(
				message,
				() => resolve(true),
				() => resolve(false)
			)
		);
	}

	// ------------------------------------------------------------------
	// Thin RPC — business logic stays on the document_tree service
	// ------------------------------------------------------------------

	function call(method, args = {}) {
		return frappe.xcall(`${METHOD}.${method}`, args);
	}

	function notify_result(result) {
		if (result?.status === "Queued") {
			frappe.show_alert(
				{ message: __("Operation queued: {0}", [result.job_id]), indicator: "blue" },
				8
			);
		} else {
			frappe.show_alert({ message: __("Tree updated"), indicator: "green" });
		}
	}

	function refresh_tree(result) {
		try {
			notify_result(result);
		} catch (_e) {
			// ignore
		}
		selected.clear();
		try {
			view && view.make_tree && view.make_tree();
		} catch (error) {
			console.warn("AI tree: refresh failed", error);
		}
		try {
			update_bulk_actions();
		} catch (_e) {
			// ignore
		}
	}

	// ------------------------------------------------------------------
	// Actions (Desk orchestration only)
	// ------------------------------------------------------------------

	function open_node(node) {
		if (node_type(node) === "document") {
			frappe.set_route("Form", "AI Document", data(node).document);
			return;
		}
		if (node_type(node) === "folder") {
			// FileView owns folder browsing. Form/File preview crashes when
			// file_type is missing on folders and new drafts.
			frappe.set_route("List", "File", folder_for(node));
		}
	}

	async function add_child(node) {
		const folder = folder_for(node);
		const kinds = [];
		if (data(node).can_create_folder) kinds.push(__("Folder"));
		if (data(node).can_create_document) kinds.push(__("AI Document"));
		if (!kinds.length) return frappe.msgprint(__("You cannot create items in this folder."));
		const values = await new Promise((resolve) => {
			frappe.prompt(
				[
					{
						fieldtype: "Select",
						fieldname: "kind",
						label: __("Create"),
						options: kinds,
						default: kinds[0],
						reqd: 1,
					},
				],
				resolve,
				__("Add to {0}", [folder]),
				__("Continue")
			);
		});
		if (values.kind === __("AI Document")) {
			frappe.route_options = { folder, source_folder: folder };
			frappe.new_doc("AI Document");
			return;
		}
		const name = await prompt_name(__("New Folder"), __("Folder Name"));
		refresh_tree(
			await call("create_folder", {
				folder_name: name,
				parent: folder,
				expected_parent_modified: data(node).modified,
			})
		);
	}

	async function rename(node) {
		const current = data(node).title || node.title || node.label;
		const name = await prompt_name(__("Rename"), __("New Name"), current);
		refresh_tree(
			await call("rename_node", {
				node: data(node).value,
				new_name: name,
				expected_modified: data(node).modified,
			})
		);
	}

	async function move(node) {
		const values = await prompt_destination(__("Move"), parent_for(node));
		refresh_tree(
			await call("move_node", {
				node: data(node).value,
				target_folder: values.target_folder,
				expected_modified: data(node).modified,
			})
		);
	}

	async function copy(node) {
		const values = await prompt_destination(__("Copy"), parent_for(node), true);
		refresh_tree(
			await call("copy_node", {
				node: data(node).value,
				target_folder: values.target_folder,
				new_name: values.new_name || null,
				expected_modified: data(node).modified,
			})
		);
	}

	async function remove(node) {
		const is_folder = node_type(node) === "folder";
		const message = is_folder
			? __(
					"Delete this folder? Non-empty folders require an explicit recursive deletion and every descendant will be permission checked."
			  )
			: __(
					"Delete this AI Document? Native Frappe attachment and retention policy will be applied."
			  );
		if (!(await confirm_action(message))) return;
		let recursive = false;
		if (is_folder) {
			recursive = await confirm_action(
				__("Also delete all folders and AI Documents in this subtree?")
			);
		}
		refresh_tree(
			await call("delete_node", {
				node: data(node).value,
				recursive: recursive ? 1 : 0,
				expected_modified: data(node).modified,
			})
		);
	}

	function actions_for(node) {
		if (node_type(node) === "page") return [];
		const actions = [];
		if (!node.is_root && data(node).can_read)
			actions.push({ label: __("Open"), action: () => open_node(node) });
		if ((node.is_root || node_type(node) === "folder") && data(node).can_create_child) {
			actions.push({ label: __("Add"), action: () => add_child(node) });
		}
		if (!node.is_root && data(node).can_write) {
			actions.push({ label: __("Rename"), action: () => rename(node) });
			actions.push({ label: __("Move"), action: () => move(node) });
		}
		if (!node.is_root && data(node).can_copy)
			actions.push({ label: __("Copy"), action: () => copy(node) });
		if (!node.is_root && data(node).can_delete)
			actions.push({ label: __("Delete"), action: () => remove(node) });
		return actions;
	}

	function context_menu(node) {
		const actions = actions_for(node);
		if (!actions.length) return;
		const dialog = new frappe.ui.Dialog({
			title: frappe.utils.escape_html(
				String(data(node).title || node.title || node.label || "")
			),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "action",
					label: __("Action"),
					options: actions.map((item) => item.label),
					reqd: 1,
				},
			],
			primary_action_label: __("Continue"),
			primary_action(values) {
				dialog.hide();
				actions.find((item) => item.label === values.action)?.action();
			},
		});
		dialog.show();
	}

	function update_location(node) {
		if (!location_wrapper || !location_wrapper.length) return;
		let folder;
		try {
			folder = folder_for(node);
		} catch (_e) {
			return;
		}
		const paths =
			folder === "Home"
				? ["Home"]
				: folder.split("/").map((_, i, all) => all.slice(0, i + 1).join("/"));
		try {
			location_wrapper.empty().append(`<span class="text-muted">${__("Location")}: </span>`);
		} catch (_e) {
			return;
		}
		paths.forEach((path, index) => {
			let label;
			try {
				label = index === 0 ? __("Home") : path.split("/").pop();
			} catch (_e) {
				label = path;
			}
			let link;
			try {
				link = $(`<a href="#">${frappe.utils.escape_html(String(label || ""))}</a>`);
			} catch (_e) {
				return;
			}
			link.on("click", (event) => {
				event.preventDefault();
				let target;
				try {
					target = path === "Home" ? view.tree.root_node : view.tree.nodes[path];
				} catch (_e) {
					target = null;
				}
				if (target) {
					try {
						view.tree.on_node_click(target);
					} catch (_e) {
						// ignore
					}
				}
			});
			location_wrapper.append(link);
			if (index < paths.length - 1) location_wrapper.append(" / ");
		});
	}

	function update_bulk_actions() {
		if (!view?.page) return;
		let count;
		try {
			count = selected.size;
		} catch (_e) {
			count = 0;
		}
		try {
			view.page.set_indicator(
				count ? __("{0} selected", [count]) : "",
				count ? "blue" : "gray"
			);
		} catch (_e) {
			// page may be destroyed when returning to Desk
		}
	}

	async function bulk_move() {
		if (!selected.size) return frappe.msgprint(__("Select at least one item."));
		if ([...selected.values()].some((item) => !item.can_write)) {
			return frappe.msgprint(__("Your selection contains an item you cannot move."));
		}
		const values = await prompt_destination(__("Move Selected Items"), "Home");
		refresh_tree(
			await call("bulk_move_nodes", {
				nodes: JSON.stringify([...selected.keys()]),
				target_folder: values.target_folder,
			})
		);
	}

	async function bulk_delete() {
		if (!selected.size) return frappe.msgprint(__("Select at least one item."));
		if ([...selected.values()].some((item) => !item.can_delete)) {
			return frappe.msgprint(__("Your selection contains an item you cannot delete."));
		}
		if (
			!(await confirm_action(
				__("Delete all selected items atomically? Folder descendants will be included.")
			))
		) {
			return;
		}
		refresh_tree(
			await call("bulk_delete_nodes", {
				nodes: JSON.stringify([...selected.keys()]),
				recursive: 1,
			})
		);
	}

	function collapse_loaded() {
		try {
			Object.values(view.tree.nodes).forEach((node) => {
				if (!node.is_root && node.expanded) view.tree.expand_node(node, false);
			});
		} catch (_e) {
			// tree may not be ready when Desk is re-entered quickly
		}
	}

	function expand_loaded() {
		try {
			Object.values(view.tree.nodes).forEach((node) => {
				// Never invoke deep retrieval. Only reveal branches already fetched.
				if (node.expandable && node.loaded && !node.expanded)
					view.tree.expand_node(node, false);
			});
		} catch (_e) {
			// ignore
		}
	}

	// ------------------------------------------------------------------
	// Frappe v17 Tree View contract
	// ------------------------------------------------------------------

	frappe.treeview_settings["AI Document"] = {
		breadcrumb: "AI Knowledge",
		title: __("AI Documents"),
		root_label: ROOT,
		// Native root discovery keeps only `value`. Load capabilities in onload.
		get_tree_root: false,
		get_tree_nodes: `${METHOD}.get_children`,
		show_expand_all: false,
		disable_add_node: true,
		do_not_setup_menu: false,
		filters: [
			{
				fieldtype: "Link",
				fieldname: "knowledge_base",
				options: "AI Knowledge Base",
				label: __("Knowledge Base"),
			},
			{
				fieldtype: "Data",
				fieldname: "search",
				label: __("Search folders and documents"),
			},
		],
		get_label(node) {
			if (node.is_root)
				return `<strong>${frappe.utils.escape_html(__("AI Documents"))}</strong>`;
			const d = data(node);
			const title = frappe.utils.escape_html(
				String(d.title || node.title || node.label || "")
			);
			if (d.node_type === "document") {
				const status = d.status
					? ` <span class="indicator-pill gray">${frappe.utils.escape_html(
							__(d.status)
					  )}</span>`
					: "";
				return `<span class="ai-document-tree-document">${title}${status}</span>`;
			}
			return title;
		},
		click(node) {
			update_location(node);
			if (node_type(node) === "document") open_node(node);
		},
		onrender(node) {
			if (node.is_root || !["folder", "document"].includes(node_type(node))) return;
			const checkbox = $(
				'<input type="checkbox" class="ai-document-tree-select" aria-label="Select item">'
			);
			checkbox.prop("checked", selected.has(data(node).value));
			checkbox.on("click", (event) => {
				event.stopPropagation();
				if (event.currentTarget.checked) selected.set(data(node).value, data(node));
				else selected.delete(data(node).value);
				update_bulk_actions();
			});
			node.$tree_link.prepend(checkbox);
			node.$tree_link.on("contextmenu", (event) => {
				event.preventDefault();
				context_menu(node);
			});
		},
		toolbar: [
			{
				label: __("Open"),
				condition: (node) =>
					!node.is_root && node_type(node) !== "page" && data(node).can_read,
				click: open_node,
			},
			{
				label: __("Add"),
				condition: (node) =>
					(node.is_root || node_type(node) === "folder") && data(node).can_create_child,
				click: add_child,
			},
			{
				label: __("Rename"),
				condition: (node) => !node.is_root && data(node).can_write,
				click: rename,
				btnClass: "hidden-xs",
			},
			{
				label: __("Move"),
				condition: (node) => !node.is_root && data(node).can_write,
				click: move,
				btnClass: "hidden-xs",
			},
			{
				label: __("Copy"),
				condition: (node) =>
					!node.is_root && node_type(node) !== "page" && data(node).can_copy,
				click: copy,
				btnClass: "hidden-xs",
			},
			{
				label: __("Delete"),
				condition: (node) => !node.is_root && data(node).can_delete,
				click: remove,
				btnClass: "hidden-xs",
			},
		],
		onload(treeview) {
			// Clean up any stale Desk return state: previous tree instances
			// leave DOM nodes and stale selected sets that would otherwise
			// leak and corrupt bulk actions after returning from Desk.
			try {
				if (location_wrapper && location_wrapper.length) location_wrapper.remove();
			} catch (_e) {
				// ignore
			}
			selected.clear();
			view = treeview;
			root_capabilities = {};
			// Frappe's native root discovery keeps only `value`. Request the
			// complete capability payload once, then construct the tree.
			let root_ready = false;
			let pending_make_tree = null;
			let make_tree_bound = false;
			let native_make_tree;
			try {
				native_make_tree = treeview.make_tree.bind(treeview);
				make_tree_bound = true;
			} catch (_e) {
				native_make_tree = (..._args) => {};
			}
			treeview.make_tree = (...args) => {
				if (root_ready && make_tree_bound) return native_make_tree(...args);
				pending_make_tree = args;
				return undefined;
			};
			treeview.root_label = ROOT;
			treeview.root_value = ROOT;
			frappe.call({
				method: `${METHOD}.get_children`,
				args: { doctype: "AI Document" },
				callback: (response) => {
					try {
						root_capabilities = response.message?.[0] || {};
						treeview.root_label = root_capabilities.value || ROOT;
						treeview.root_value = treeview.root_label;
					} catch (_e) {
						root_capabilities = {};
					}
					root_ready = true;
					const pending_args = pending_make_tree || [];
					pending_make_tree = null;
					try {
						native_make_tree(...pending_args);
					} catch (error) {
						console.warn("AI tree: make_tree after root failed", error);
					}
				},
				error: () => {
					root_ready = true;
					const pending_args = pending_make_tree || [];
					pending_make_tree = null;
					try {
						native_make_tree(...pending_args);
					} catch (error) {
						console.warn("AI tree: make_tree error fallback failed", error);
					}
				},
			});
			location_wrapper = $('<div class="ai-document-tree-location text-muted mb-3"></div>');
			try {
				treeview.page.main.prepend(location_wrapper);
			} catch (_e) {
				// page may not be ready during fast SPA navigation back from Desk
			}
			// Avoid duplicate buttons when Desk is revisited and onload fires again.
			const addOnce = (label, fn, group) => {
				try {
					const existing = (treeview.page.inner_toolbar || []).find
						? treeview.page.inner_toolbar.find((b) => b.label === label)
						: null;
					if (existing) return;
				} catch (_e) {
					// ignore
				}
				try {
					treeview.page.add_inner_button(label, fn, group);
				} catch (_e) {
					// ignore if toolbar not ready
				}
			};
			addOnce(__("Expand Loaded"), expand_loaded, __("Tree"));
			addOnce(__("Collapse All"), collapse_loaded, __("Tree"));
			addOnce(__("Move Selected"), bulk_move, __("Bulk Actions"));
			addOnce(__("Delete Selected"), bulk_delete, __("Bulk Actions"));
			addOnce(
				__("Refresh"),
				() => {
					try {
						treeview.make_tree();
					} catch (_e) {
						// ignore
					}
				},
				null
			);
			update_location({ is_root: true, data: { value: ROOT } });
		},
		menu_items: [
			{
				label: __("View List"),
				action: () => frappe.set_route("List", "AI Document", "List"),
			},
		],
	};
})();
