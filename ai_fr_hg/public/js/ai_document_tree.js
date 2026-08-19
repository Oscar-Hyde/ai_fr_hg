/* Copyright (c) 2026, Ai Fr Hg and contributors */

frappe.provide("frappe.treeview_settings");

(() => {
	const METHOD = "ai_fr_hg.api.document_tree";
	const ROOT = "AI Documents";
	const selected = new Map();
	let view;
	let location_wrapper;
	let root_capabilities = {};

	const data = (node) => (node.is_root ? { ...(node.data || {}), ...root_capabilities } : node.data || {});
	const node_type = (node) => (node.is_root ? "root" : data(node).node_type);
	const folder_for = (node) => {
		if (node.is_root) return "Home";
		return node_type(node) === "folder" ? data(node).value : data(node).folder || "Home";
	};
	const parent_for = (node) => data(node).folder || "Home";

	function escape(value) {
		return frappe.utils.escape_html(String(value || ""));
	}

	function notify_result(result) {
		if (result?.status === "Queued") {
			frappe.show_alert({ message: __("Operation queued: {0}", [result.job_id]), indicator: "blue" }, 8);
		} else {
			frappe.show_alert({ message: __("Tree updated"), indicator: "green" });
		}
	}

	async function call(method, args = {}) {
		const response = await frappe.call({ method: `${METHOD}.${method}`, args, freeze: true });
		return response.message;
	}

	function refresh_tree(result) {
		notify_result(result);
		selected.clear();
		view.make_tree();
		update_bulk_actions();
	}

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

	function open_node(node) {
		if (node_type(node) === "document") {
			frappe.set_route("Form", "AI Document", data(node).document);
			return;
		}
		if (node_type(node) === "folder") {
			// FileView owns folder browsing. Opening a folder as Form/File
			// hits Frappe's preview_file path, which crashes when file_type
			// is missing (folders and new drafts).
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
		const result = await call("create_folder", {
			folder_name: name,
			parent: folder,
			expected_parent_modified: data(node).modified,
		});
		refresh_tree(result);
	}

	async function rename(node) {
		const current = data(node).title || node.title || node.label;
		const name = await prompt_name(__("Rename"), __("New Name"), current);
		const result = await call("rename_node", {
			node: data(node).value,
			new_name: name,
			expected_modified: data(node).modified,
		});
		refresh_tree(result);
	}

	async function move(node) {
		const values = await prompt_destination(__("Move"), parent_for(node));
		const result = await call("move_node", {
			node: data(node).value,
			target_folder: values.target_folder,
			expected_modified: data(node).modified,
		});
		refresh_tree(result);
	}

	async function copy(node) {
		const values = await prompt_destination(__("Copy"), parent_for(node), true);
		const result = await call("copy_node", {
			node: data(node).value,
			target_folder: values.target_folder,
			new_name: values.new_name || null,
			expected_modified: data(node).modified,
		});
		refresh_tree(result);
	}

	async function remove(node) {
		const is_folder = node_type(node) === "folder";
		const message = is_folder
			? __("Delete this folder? Non-empty folders require an explicit recursive deletion and every descendant will be permission checked.")
			: __("Delete this AI Document? Native Frappe attachment and retention policy will be applied.");
		const confirmed = await new Promise((resolve) => frappe.confirm(message, () => resolve(true), () => resolve(false)));
		if (!confirmed) return;
		let recursive = false;
		if (is_folder) {
			recursive = await new Promise((resolve) =>
				frappe.confirm(__("Also delete all folders and AI Documents in this subtree?"), () => resolve(true), () => resolve(false))
			);
		}
		const result = await call("delete_node", {
			node: data(node).value,
			recursive: recursive ? 1 : 0,
			expected_modified: data(node).modified,
		});
		refresh_tree(result);
	}

	function actions_for(node) {
		if (node_type(node) === "page") return [];
		const actions = [];
		if (!node.is_root && data(node).can_read) actions.push({ label: __("Open"), action: () => open_node(node) });
		if ((node.is_root || node_type(node) === "folder") && data(node).can_create_child) {
			actions.push({ label: __("Add"), action: () => add_child(node) });
		}
		if (!node.is_root && data(node).can_write) {
			actions.push({ label: __("Rename"), action: () => rename(node) });
			actions.push({ label: __("Move"), action: () => move(node) });
		}
		if (!node.is_root && data(node).can_copy) actions.push({ label: __("Copy"), action: () => copy(node) });
		if (!node.is_root && data(node).can_delete) actions.push({ label: __("Delete"), action: () => remove(node) });
		return actions;
	}

	function context_menu(node) {
		const actions = actions_for(node);
		if (!actions.length) return;
		const dialog = new frappe.ui.Dialog({
			title: escape(data(node).title || node.title || node.label),
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
		if (!location_wrapper) return;
		const folder = folder_for(node);
		const paths = folder === "Home" ? ["Home"] : folder.split("/").map((_, i, all) => all.slice(0, i + 1).join("/"));
		location_wrapper.empty().append(`<span class="text-muted">${__("Location")}: </span>`);
		paths.forEach((path, index) => {
			const label = index === 0 ? __("Home") : path.split("/").pop();
			const link = $(`<a href="#">${escape(label)}</a>`);
			link.on("click", (event) => {
				event.preventDefault();
				const target = path === "Home" ? view.tree.root_node : view.tree.nodes[path];
				if (target) view.tree.on_node_click(target);
			});
			location_wrapper.append(link);
			if (index < paths.length - 1) location_wrapper.append(" / ");
		});
	}

	function update_bulk_actions() {
		if (!view?.page) return;
		const count = selected.size;
		view.page.set_indicator(count ? __("{0} selected", [count]) : "", count ? "blue" : "gray");
	}

	async function bulk_move() {
		if (!selected.size) return frappe.msgprint(__("Select at least one item."));
		if ([...selected.values()].some((item) => !item.can_write)) {
			return frappe.msgprint(__("Your selection contains an item you cannot move."));
		}
		const values = await prompt_destination(__("Move Selected Items"), "Home");
		const result = await call("bulk_move_nodes", {
			nodes: JSON.stringify([...selected.keys()]),
			target_folder: values.target_folder,
		});
		refresh_tree(result);
	}

	async function bulk_delete() {
		if (!selected.size) return frappe.msgprint(__("Select at least one item."));
		if ([...selected.values()].some((item) => !item.can_delete)) {
			return frappe.msgprint(__("Your selection contains an item you cannot delete."));
		}
		const confirmed = await new Promise((resolve) =>
			frappe.confirm(__("Delete all selected items atomically? Folder descendants will be included."), () => resolve(true), () => resolve(false))
		);
		if (!confirmed) return;
		const result = await call("bulk_delete_nodes", {
			nodes: JSON.stringify([...selected.keys()]),
			recursive: 1,
		});
		refresh_tree(result);
	}

	function collapse_loaded() {
		Object.values(view.tree.nodes).forEach((node) => {
			if (!node.is_root && node.expanded) view.tree.expand_node(node, false);
		});
	}

	function expand_loaded() {
		Object.values(view.tree.nodes).forEach((node) => {
			// Never invoke deep retrieval. Only reveal branches already fetched by
			// the user, which keeps this safe for repositories of arbitrary size.
			if (node.expandable && node.loaded && !node.expanded) view.tree.expand_node(node, false);
		});
	}

	frappe.treeview_settings["AI Document"] = {
		breadcrumb: "AI Knowledge",
		title: __("AI Documents"),
		root_label: ROOT,
		// Initialize the root explicitly in onload. Native root discovery keeps
		// only `value`, which would discard server-derived root capabilities.
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
			if (node.is_root) return `<strong>${escape(__("AI Documents"))}</strong>`;
			const d = data(node);
			const title = escape(d.title || node.title || node.label);
			if (d.node_type === "document") {
				const status = d.status ? ` <span class="indicator-pill gray">${escape(__(d.status))}</span>` : "";
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
			const checkbox = $('<input type="checkbox" class="ai-document-tree-select" aria-label="Select item">');
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
				condition: (node) => !node.is_root && node_type(node) !== "page" && data(node).can_read,
				click: open_node,
			},
			{
				label: __("Add"),
				condition: (node) => (node.is_root || node_type(node) === "folder") && data(node).can_create_child,
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
				condition: (node) => !node.is_root && node_type(node) !== "page" && data(node).can_copy,
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
			view = treeview;
			// Frappe's native root discovery keeps only `value`. Perform that one
			// request here instead, then construct the tree exactly once with the
			// complete server-derived root capability payload. Queue framework
			// refresh/filter requests until that authority response is available.
			let root_ready = false;
			let pending_make_tree = null;
			const native_make_tree = treeview.make_tree.bind(treeview);
			treeview.make_tree = (...args) => {
				if (root_ready) return native_make_tree(...args);
				pending_make_tree = args;
				return undefined;
			};
			treeview.root_label = ROOT;
			treeview.root_value = ROOT;
			frappe.call({
				method: `${METHOD}.get_children`,
				args: { doctype: "AI Document" },
				callback: (response) => {
					root_capabilities = response.message?.[0] || {};
					treeview.root_label = root_capabilities.value || ROOT;
					treeview.root_value = treeview.root_label;
					root_ready = true;
					const pending_args = pending_make_tree || [];
					pending_make_tree = null;
					native_make_tree(...pending_args);
				},
				error: () => {
					// Restore native refresh behavior even when capability discovery
					// fails; server-side child retrieval still enforces authority.
					root_ready = true;
					const pending_args = pending_make_tree || [];
					pending_make_tree = null;
					native_make_tree(...pending_args);
				},
			});
			location_wrapper = $('<div class="ai-document-tree-location text-muted mb-3"></div>');
			treeview.page.main.prepend(location_wrapper);
			treeview.page.add_inner_button(__("Expand Loaded"), expand_loaded, __("Tree"));
			treeview.page.add_inner_button(__("Collapse All"), collapse_loaded, __("Tree"));
			treeview.page.add_inner_button(__("Move Selected"), bulk_move, __("Bulk Actions"));
			treeview.page.add_inner_button(__("Delete Selected"), bulk_delete, __("Bulk Actions"));
			treeview.page.add_inner_button(__("Refresh"), () => treeview.make_tree());
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
