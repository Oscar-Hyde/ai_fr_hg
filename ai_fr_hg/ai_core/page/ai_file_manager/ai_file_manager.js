// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * File Manager — canonical folder/file hierarchy UI.
 *
 * Backed entirely by the canonical folder service (ai_fr_hg.api.folders) and
 * Frappe's native File DocType. Tabs are saved views over the same hierarchy,
 * not a second data model (§3.3).
 *
 * Features:
 *  - Folder tree with arbitrary nesting, breadcrumb, and persistent navigation
 *  - Tabs (Recent, Shared, Favorites, and top-level folders) as real queries
 *  - Create / Rename / Move (drag-drop + Move To) / Delete / Copy / Search
 *  - Bulk move with background-job progress tracking
 *  - Favorites / pinning (per-user, queryable)
 *  - Sorting and view options (list / grid, by name/date/type/size)
 *  - Folder-scoped knowledge filter (ingestion status visible)
 */

frappe.pages["ai-file-manager"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("File Manager"),
		single_column: false,
	});
	wrapper.file_manager = new AIFileManager(page);
};

frappe.pages["ai-file-manager"].on_page_show = function (wrapper) {
	wrapper.file_manager && wrapper.file_manager.refresh();
};

class AIFileManager {
	constructor(page) {
		this.page = page;
		this.current_folder = "Home";
		this.view_mode = "list"; // list | grid
		this.sort_by = "file_name asc";
		this.search_text = "";
		this.selected = new Set();
		this.tabs = [];
		this.active_tab = null;
		this.tree_data = null;
		this.make();
		this.bind_page_actions();
		this.refresh();
	}

	make() {
		this.page.main.addClass("ai-file-manager-page");
		this.page.main.html(`
			<div class="ai-file-manager">
				<div class="ai-file-tabs"></div>
				<div class="ai-file-layout">
					<aside class="ai-file-sidebar">
						<div class="ai-sidebar-header">
							<strong>${__("Folders")}</strong>
							<button class="btn btn-xs btn-default ai-new-folder-sidebar" title="${__("New Folder")}">${frappe.utils.icon("add", "xs")}</button>
						</div>
						<div class="ai-folder-tree"></div>
						<div class="ai-sidebar-section ai-favorites-section">
							<div class="ai-sidebar-title">${__("Favorites")} <span class="ai-fav-count text-muted"></span></div>
							<div class="ai-favorites-list"></div>
						</div>
						<div class="ai-sidebar-section ai-recents-section">
							<div class="ai-sidebar-title">${__("Recent")}</div>
							<div class="ai-recents-list"></div>
						</div>
					</aside>
					<main class="ai-file-main">
						<div class="ai-file-toolbar">
							<div class="ai-breadcrumbs"></div>
							<div class="ai-toolbar-actions">
								<div class="btn-group ai-view-toggle">
									<button class="btn btn-default btn-xs ai-view-list active" title="${__("List")}">${frappe.utils.icon("list", "xs")}</button>
									<button class="btn btn-default btn-xs ai-view-grid" title="${__("Grid")}">${frappe.utils.icon("image-view", "xs")}</button>
								</div>
								<select class="form-control input-xs ai-sort-select" style="width:140px; display:inline-block; margin-left:8px;">
									<option value="file_name asc">${__("Name A→Z")}</option>
									<option value="file_name desc">${__("Name Z→A")}</option>
									<option value="modified desc">${__("Newest")}</option>
									<option value="modified asc">${__("Oldest")}</option>
									<option value="file_size desc">${__("Largest")}</option>
									<option value="file_size asc">${__("Smallest")}</option>
								</select>
								<input type="text" class="form-control input-xs ai-search-input" placeholder="${__("Search in folder...")}" style="width:180px; display:inline-block; margin-left:8px;">
							</div>
						</div>
						<div class="ai-bulk-bar hidden">
							<span class="ai-bulk-count">0 ${__("selected")}</span>
							<button class="btn btn-xs btn-default ai-bulk-move">${__("Move To…")}</button>
							<button class="btn btn-xs btn-default ai-bulk-copy">${__("Copy")}</button>
							<button class="btn btn-xs btn-danger ai-bulk-delete">${__("Delete")}</button>
							<button class="btn btn-xs btn-default ai-bulk-clear">${__("Clear")}</button>
						</div>
						<div class="ai-file-contents">
							<div class="ai-empty text-muted" style="padding:40px; text-align:center;">${__("Loading…")}</div>
						</div>
					</main>
				</div>
			</div>
		`);

		this.$tabs = this.page.main.find(".ai-file-tabs");
		this.$tree = this.page.main.find(".ai-folder-tree");
		this.$favorites = this.page.main.find(".ai-favorites-list");
		this.$recents = this.page.main.find(".ai-recents-list");
		this.$breadcrumbs = this.page.main.find(".ai-breadcrumbs");
		this.$contents = this.page.main.find(".ai-file-contents");
		this.$bulkBar = this.page.main.find(".ai-bulk-bar");
		this.$search = this.page.main.find(".ai-search-input");

		// Sidebar actions
		this.page.main.find(".ai-new-folder-sidebar").on("click", () => this.create_folder());
		this.page.main.find(".ai-view-list").on("click", () => this.set_view("list"));
		this.page.main.find(".ai-view-grid").on("click", () => this.set_view("grid"));
		this.page.main.find(".ai-sort-select").on("change", (e) => {
			this.sort_by = e.target.value;
			this.load_folder_contents();
		});
		this.$search.on("input", frappe.utils.debounce((e) => {
			this.search_text = e.target.value.trim();
			this.load_folder_contents();
		}, 300));

		// Bulk actions
		this.page.main.find(".ai-bulk-move").on("click", () => this.bulk_move());
		this.page.main.find(".ai-bulk-copy").on("click", () => this.bulk_copy());
		this.page.main.find(".ai-bulk-delete").on("click", () => this.bulk_delete());
		this.page.main.find(".ai-bulk-clear").on("click", () => this.clear_selection());

		// Content events (delegated)
		this.$contents.on("click", ".ai-file-row", (e) => {
			// Ignore clicks on action buttons
			if ($(e.target).closest(".ai-row-actions, .ai-checkbox").length) return;
			const name = $(e.currentTarget).data("name");
			const isFolder = $(e.currentTarget).data("is-folder");
			if (isFolder) this.open_folder(name);
			else this.preview_file(name);
		});
		this.$contents.on("click", ".ai-checkbox input", (e) => {
			e.stopPropagation();
			const name = $(e.target).closest(".ai-file-row, .ai-file-card").data("name");
			if (e.target.checked) this.selected.add(name);
			else this.selected.delete(name);
			this.render_bulk_bar();
		});
		// Make rows draggable for drag-drop move
		this.$contents.on("dragstart", ".ai-file-row, .ai-file-card", (e) => {
			const $row = $(e.currentTarget);
			e.originalEvent.dataTransfer.setData("text/plain", $row.data("name"));
			e.originalEvent.dataTransfer.effectAllowed = "move";
		});
		this.$tree.on("dragover", ".ai-tree-item", (e) => {
			e.preventDefault();
			$(e.currentTarget).addClass("ai-drop-target");
		});
		this.$tree.on("dragleave", ".ai-tree-item", (e) => {
			$(e.currentTarget).removeClass("ai-drop-target");
		});
		this.$tree.on("drop", ".ai-tree-item", async (e) => {
			e.preventDefault();
			$(e.currentTarget).removeClass("ai-drop-target");
			const target = $(e.currentTarget).data("name");
			const source = e.originalEvent.dataTransfer.getData("text/plain");
			if (!source || !target || source === target) return;
			try {
				const sourceDoc = await frappe.xcall("ai_fr_hg.api.folders.get_file_info", { file_name: source });
				if (sourceDoc.is_folder) {
					await frappe.xcall("ai_fr_hg.api.folders.move_folder", { folder_name: source, target_folder: target });
				} else {
					await frappe.xcall("ai_fr_hg.api.folders.move_file", { file_name: source, target_folder: target });
				}
				frappe.show_alert({ message: __("Moved {0} to {1}", [sourceDoc.file_name, target]), indicator: "green" });
				this.refresh();
			} catch (err) {
				frappe.msgprint({ title: __("Move failed"), message: err.message, indicator: "red" });
			}
		});
	}

	bind_page_actions() {
		this.page.set_primary_action(__("Upload"), () => this.upload());
		this.page.add_action_icon("folder", () => this.create_folder(), __("New Folder"));
		this.page.add_menu_item(__("Refresh"), () => this.refresh());
		this.page.add_menu_item(__("Search Everywhere"), () => this.global_search());
		this.page.add_menu_item(__("Toggle Favorites"), () => this.toggle_favorite_current());
		this.page.add_menu_item(__("View in Frappe File"), () => frappe.set_route("List", "File"));
	}

	async refresh() {
		try {
			this.tree_data = await frappe.xcall("ai_fr_hg.api.folders.get_tree", { root: "Home", max_depth: 4 });
			this.tabs = await frappe.xcall("ai_fr_hg.api.folders.get_tabs");
			this.favorites = await frappe.xcall("ai_fr_hg.api.folders.list_favorites");
			this.recents = await frappe.xcall("ai_fr_hg.api.folders.get_recents", { limit: 10 });
		} catch (e) {
			console.error("File Manager refresh failed", e);
			frappe.msgprint({ title: __("Load failed"), message: e.message, indicator: "red" });
			return;
		}
		this.render_tabs();
		this.render_tree();
		this.render_favorites();
		this.render_recents();
		this.load_folder_contents();
	}

	render_tabs() {
		const $wrap = this.$tabs.empty();
		// Always show core tabs
		this.tabs.forEach((tab) => {
			const isActive = this.active_tab === tab.id;
			const $btn = $(`
				<button class="ai-tab ${isActive ? "active" : ""}" data-tab="${frappe.utils.escape_html(tab.id)}">
					${frappe.utils.escape_html(tab.label)}
				</button>
			`);
			$btn.on("click", () => this.activate_tab(tab));
			$wrap.append($btn);
		});
	}

	async activate_tab(tab) {
		this.active_tab = tab.id;
		this.render_tabs();
		if (tab.type === "folder" && tab.folder) {
			this.open_folder(tab.folder);
		} else if (tab.id === "recent") {
			this.current_folder = null; // show recent across all folders
			this.search_text = "";
			this.$search.val("");
			this.$breadcrumbs.html(`<span class="text-muted">${__("Recent Files")}</span>`);
			const recents = await frappe.xcall("ai_fr_hg.api.folders.get_recents", { limit: 40 });
			this.render_recent_list(recents);
		} else if (tab.id === "favorites") {
			this.$breadcrumbs.html(`<span class="text-muted">${__("Favorites")}</span>`);
			const favs = await frappe.xcall("ai_fr_hg.api.folders.list_favorites");
			if (!favs.length) {
				this.$contents.html(`<div class="text-muted" style="padding:30px; text-align:center;">${__("No favorites yet. Star folders to pin them.")}</div>`);
				return;
			}
			// Show each favorited folder's contents aggregated
			let html = `<div class="ai-fav-grid">`;
			for (const f of favs) {
				html += `<div class="ai-fav-entry" data-folder="${frappe.utils.escape_html(f.name)}">
					<strong>${frappe.utils.escape_html(f.file_name)}</strong>
					<div class="small text-muted">${frappe.utils.escape_html(f.name)}</div>
				</div>`;
			}
			html += `</div>`;
			this.$contents.html(html);
			this.$contents.find(".ai-fav-entry").on("click", (e) => {
				const folder = $(e.currentTarget).data("folder");
				this.open_folder(folder);
			});
		} else if (tab.id === "shared") {
			this.current_folder = null;
			const result = await frappe.xcall("ai_fr_hg.api.folders.search", { query: "", limit: 50 });
			// Filter to shared (is_private = 0) via search with folder? Show search results
			this.$breadcrumbs.html(`<span class="text-muted">${__("Shared Files")}</span>`);
			this.render_search_results(result);
		} else {
			this.open_folder(this.current_folder || "Home");
		}
	}

	render_tree() {
		const renderNode = (node, depth = 0) => {
			const isActive = node.name === this.current_folder;
			const hasChildren = (node.children || []).length > 0;
			const $item = $(`
				<div class="ai-tree-item ${isActive ? "active" : ""}" data-name="${frappe.utils.escape_html(node.name)}" draggable="true" style="padding-left:${8 + depth * 14}px;">
					<span class="ai-tree-toggle">${hasChildren ? "▸" : "&nbsp;"}</span>
					<span class="ai-tree-label">${frappe.utils.escape_html(node.file_name)}</span>
				</div>
			`);
			const $wrap = $(`<div class="ai-tree-node"></div>`);
			$wrap.append($item);
			if (hasChildren) {
				const $children = $(`<div class="ai-tree-children ${isActive ? "" : "hidden"}"></div>`);
				node.children.forEach((child) => {
					if (child.is_folder) $children.append(renderNode(child, depth + 1));
				});
				$wrap.append($children);
				$item.find(".ai-tree-toggle").on("click", (e) => {
					e.stopPropagation();
					$children.toggleClass("hidden");
					$item.find(".ai-tree-toggle").text($children.hasClass("hidden") ? "▸" : "▾");
				});
			}
			$item.on("click", (e) => {
				e.stopPropagation();
				this.open_folder(node.name);
			});
			$item.on("contextmenu", (e) => {
				e.preventDefault();
				this.show_folder_context_menu(node, e);
			});
			return $wrap;
		};
		this.$tree.empty();
		if (this.tree_data) this.$tree.append(renderNode(this.tree_data));
	}

	show_folder_context_menu(node, event) {
		const menu = [
			{ label: __("Open"), action: () => this.open_folder(node.name) },
			{ label: __("Rename"), action: () => this.rename_folder(node.name) },
			{ label: __("Move"), action: () => this.move_folder(node.name) },
			{ label: __("Copy Path"), action: () => frappe.utils.copy_to_clipboard(node.name) },
			{ label: __("Favorite"), action: () => this.add_favorite(node.name) },
			{ label: __("Delete"), action: () => this.delete_folder(node.name), danger: true },
		];
		frappe.utils.show_menu(menu, event.pageX, event.pageY);
		// Fallback if show_menu not available
		if (!frappe.utils.show_menu) {
			frappe.prompt(
				{
					fieldname: "action",
					fieldtype: "Select",
					label: node.file_name,
					options: menu.map((m) => m.label),
					reqd: 1,
				},
				(values) => {
					const chosen = menu.find((m) => m.label === values.action);
					if (chosen) chosen.action();
				}
			);
		}
	}

	render_favorites() {
		const $list = this.$favorites.empty();
		$(".ai-fav-count").text(this.favorites?.length ? `(${this.favorites.length})` : "");
		if (!this.favorites || !this.favorites.length) {
			$list.html(`<div class="text-muted small">${__("No favorites.")}</div>`);
			return;
		}
		this.favorites.forEach((fav) => {
			const $row = $(`
				<div class="ai-fav-row" data-folder="${frappe.utils.escape_html(fav.name)}">
					<span class="ai-fav-name">${frappe.utils.escape_html(fav.file_name)}</span>
					<button class="btn btn-xs btn-default ai-unfav" title="${__("Unfavorite")}">×</button>
				</div>
			`);
			$row.on("click", (e) => {
				if ($(e.target).hasClass("ai-unfav")) return;
				this.open_folder(fav.name);
			});
			$row.find(".ai-unfav").on("click", async (e) => {
				e.stopPropagation();
				await frappe.xcall("ai_fr_hg.api.folders.remove_favorite", { folder: fav.name });
				frappe.show_alert({ message: __("Removed from favorites"), indicator: "blue" });
				this.refresh();
			});
			$list.append($row);
		});
	}

	render_recents() {
		const $list = this.$recents.empty();
		if (!this.recents || !this.recents.length) {
			$list.html(`<div class="text-muted small">${__("No recent items.")}</div>`);
			return;
		}
		this.recents.slice(0, 8).forEach((item) => {
			const label = item.file_name || item.name;
			const $row = $(`
				<div class="ai-recent-row" data-name="${frappe.utils.escape_html(item.name)}">
					<span class="text-muted small">${item.is_folder ? "📁" : "📄"}</span>
					<span class="ai-recent-name">${frappe.utils.escape_html(label)}</span>
				</div>
			`);
			$row.on("click", () => {
				if (item.is_folder) this.open_folder(item.name);
				else this.preview_file(item.name);
			});
			$list.append($row);
		});
	}

	async open_folder(folder) {
		this.current_folder = folder || "Home";
		this.active_tab = null; // deselect tabs when navigating tree
		this.selected.clear();
		this.render_tree();
		this.render_tabs();
		this.load_folder_contents();
	}

	async load_folder_contents() {
		if (this.current_folder === null && this.active_tab === "recent") return;
		if (this.current_folder === null) {
			// If no folder set, default to Home
			this.current_folder = "Home";
		}
		try {
			const result = await frappe.xcall("ai_fr_hg.api.folders.list_folder_contents", {
				folder: this.current_folder,
				limit: 100,
				offset: 0,
				search_text: this.search_text || null,
				order_by: this.sort_by,
			});
			const crumbs = await frappe.xcall("ai_fr_hg.api.folders.get_breadcrumbs", {
				file_or_folder: this.current_folder,
			});
			this.render_breadcrumbs(crumbs);
			this.render_contents(result);
		} catch (e) {
			frappe.msgprint({ title: __("Could not load folder"), message: e.message, indicator: "red" });
		}
	}

	render_breadcrumbs(crumbs) {
		const $wrap = this.$breadcrumbs.empty();
		if (!crumbs || !crumbs.length) {
			$wrap.html(`<span>${frappe.utils.escape_html(this.current_folder)}</span>`);
			return;
		}
		crumbs.forEach((crumb, idx) => {
			const isLast = idx === crumbs.length - 1;
			const $el = $(`
				<span class="ai-crumb ${isLast ? "active" : ""}" data-name="${frappe.utils.escape_html(crumb.name)}">
					${frappe.utils.escape_html(crumb.file_name)}
				</span>
			`);
			if (!isLast) $el.on("click", () => this.open_folder(crumb.name));
			$wrap.append($el);
			if (!isLast) $wrap.append(`<span class="ai-crumb-sep"> / </span>`);
		});
		const $favToggle = $(`<button class="btn btn-xs btn-default" style="margin-left:12px;" title="${__("Toggle Favorite")}">${frappe.utils.icon("star", "xs")}</button>`);
		$favToggle.on("click", () => this.toggle_favorite_current());
		$wrap.append($favToggle);
	}

	render_contents(result) {
		const items = result.items || [];
		this.$contents.empty();
		this.render_bulk_bar();

		if (!items.length) {
			const isSearching = !!this.search_text;
			this.$contents.html(`
				<div class="text-muted" style="padding:40px; text-align:center;">
					${isSearching ? __("No results for “{0}”", [this.search_text]) : __("This folder is empty.")}
					<div style="margin-top:12px;">
						<button class="btn btn-xs btn-primary ai-empty-upload">${__("Upload File")}</button>
						<button class="btn btn-xs btn-default ai-empty-folder">${__("New Folder")}</button>
					</div>
				</div>
			`);
			this.$contents.find(".ai-empty-upload").on("click", () => this.upload());
			this.$contents.find(".ai-empty-folder").on("click", () => this.create_folder());
			return;
		}

		// Sort client-side if needed (server already sorted, but keep toggle)
		if (this.view_mode === "grid") {
			const $grid = $('<div class="ai-grid"></div>');
			items.forEach((item) => {
				const isFolder = !!item.is_folder;
				const $card = $(`
					<div class="ai-file-card ${isFolder ? "ai-card-folder" : "ai-card-file"}" data-name="${frappe.utils.escape_html(item.name)}" data-is-folder="${isFolder ? 1 : 0}" draggable="true">
						<div class="ai-card-checkbox"><input type="checkbox" ${this.selected.has(item.name) ? "checked" : ""}></div>
						<div class="ai-card-icon">${isFolder ? "📁" : this.icon_for(item)}</div>
						<div class="ai-card-name" title="${frappe.utils.escape_html(item.file_name)}">${frappe.utils.escape_html(item.file_name)}</div>
						<div class="ai-card-meta text-muted small">
							${item.is_private ? "🔒" : ""}
							${item.file_size ? this.format_size(item.file_size) : ""}
							${item.file_type ? " · " + frappe.utils.escape_html(item.file_type) : ""}
						</div>
						<div class="ai-card-actions">
							<button class="btn btn-xs btn-default ai-card-menu">${frappe.utils.icon("dot-horizontal", "xs")}</button>
						</div>
					</div>
				`);
				$card.find(".ai-card-checkbox input").on("click", (e) => {
					e.stopPropagation();
					if (e.target.checked) this.selected.add(item.name);
					else this.selected.delete(item.name);
					this.render_bulk_bar();
				});
				$card.find(".ai-card-menu").on("click", (e) => {
					e.stopPropagation();
					this.show_item_menu(item, e);
				});
				$card.on("click", (e) => {
					if ($(e.target).closest(".ai-card-actions, .ai-card-checkbox").length) return;
					if (isFolder) this.open_folder(item.name);
					else this.preview_file(item.name);
				});
				$grid.append($card);
			});
			this.$contents.append($grid);
		} else {
			const $table = $(`
				<div class="ai-list-header">
					<span class="ai-col-check"><input type="checkbox" class="ai-select-all"></span>
					<span class="ai-col-name">${__("Name")}</span>
					<span class="ai-col-size">${__("Size")}</span>
					<span class="ai-col-type">${__("Type")}</span>
					<span class="ai-col-modified">${__("Modified")}</span>
					<span class="ai-col-actions"></span>
				</div>
				<div class="ai-list-body"></div>
			`);
			this.$contents.append($table);
			const $body = $table.find(".ai-list-body");
			$table.find(".ai-select-all").on("change", (e) => {
				const checked = e.target.checked;
				$body.find(".ai-checkbox input").each((_, el) => {
					el.checked = checked;
					const name = $(el).closest(".ai-file-row").data("name");
					if (checked) this.selected.add(name);
					else this.selected.delete(name);
				});
				this.render_bulk_bar();
			});

			items.forEach((item) => {
				const isFolder = !!item.is_folder;
				const ingestion = item.ingestion_status || "";
				const $row = $(`
					<div class="ai-file-row ${isFolder ? "ai-folder-row" : "ai-file-row-file"}" data-name="${frappe.utils.escape_html(item.name)}" data-is-folder="${isFolder ? 1 : 0}" draggable="true">
						<span class="ai-col-check ai-checkbox"><input type="checkbox" ${this.selected.has(item.name) ? "checked" : ""}></span>
						<span class="ai-col-name" title="${frappe.utils.escape_html(item.file_name)}">
							<span class="ai-icon">${isFolder ? "📁" : this.icon_for(item)}</span>
							<span class="ai-name-text">${frappe.utils.escape_html(item.file_name)}</span>
							${item.is_private ? '<span class="indicator-pill red" style="margin-left:6px;">Private</span>' : ""}
							${ingestion ? `<span class="indicator-pill ${ingestion === "Indexed" ? "green" : ingestion === "Failed" ? "red" : "orange"}" style="margin-left:6px;">${frappe.utils.escape_html(ingestion)}</span>` : ""}
							${item.attached_to_name ? `<span class="text-muted small" style="margin-left:6px;">→ ${frappe.utils.escape_html(item.attached_to_doctype || "")} ${frappe.utils.escape_html(item.attached_to_name)}</span>` : ""}
						</span>
						<span class="ai-col-size">${item.file_size ? this.format_size(item.file_size) : "-"}</span>
						<span class="ai-col-type">${frappe.utils.escape_html(item.file_type || (isFolder ? "Folder" : ""))}</span>
						<span class="ai-col-modified small text-muted">${frappe.datetime.prettyDate(item.modified) || ""}</span>
						<span class="ai-col-actions ai-row-actions">
							<button class="btn btn-xs btn-default ai-row-menu">${frappe.utils.icon("dot-horizontal", "xs")}</button>
						</span>
					</div>
				`);
				$row.find(".ai-row-menu").on("click", (e) => {
					e.stopPropagation();
					this.show_item_menu(item, e);
				});
				$body.append($row);
			});
		}
	}

	render_recent_list(recents) {
		if (!recents || !recents.length) {
			this.$contents.html(`<div class="text-muted" style="padding:30px; text-align:center;">${__("No recent files.")}</div>`);
			return;
		}
		const $list = $('<div class="ai-list-body"></div>');
		this.$contents.empty().append($list);
		recents.forEach((item) => {
			const $row = $(`
				<div class="ai-file-row" data-name="${frappe.utils.escape_html(item.name)}" data-is-folder="${item.is_folder ? 1 : 0}">
					<span class="ai-col-name">
						<span class="ai-icon">${item.is_folder ? "📁" : "📄"}</span>
						${frappe.utils.escape_html(item.file_name)}
						<span class="text-muted small" style="margin-left:8px;">${frappe.utils.escape_html(item.folder || "")}</span>
					</span>
					<span class="ai-col-modified small text-muted">${frappe.datetime.prettyDate(item.modified) || ""}</span>
				</div>
			`);
			$row.on("click", () => {
				if (item.is_folder) this.open_folder(item.name);
				else this.preview_file(item.name);
			});
			$list.append($row);
		});
	}

	render_search_results(result) {
		const items = result.results || result.items || [];
		if (!items.length) {
			this.$contents.html(`<div class="text-muted" style="padding:30px; text-align:center;">${__("No results.")}</div>`);
			return;
		}
		this.render_contents({ items: items.map((r) => ({
			name: r.name,
			file_name: r.file_name,
			is_folder: r.is_folder,
			folder: r.folder,
			file_url: r.file_url,
			file_size: r.file_size,
			file_type: r.file_type,
			modified: r.modified,
			owner: r.owner,
			ingestion_status: r.ingestion_status,
			attached_to_doctype: r.attached_to_doctype,
			attached_to_name: r.attached_to_name,
		})) });
	}

	icon_for(item) {
		const ext = (item.file_name || "").split(".").pop().toLowerCase();
		const map = {
			pdf: "📄",
			doc: "📝",
			docx: "📝",
			xls: "📊",
			xlsx: "📊",
			ppt: "📽️",
			pptx: "📽️",
			png: "🖼️",
			jpg: "🖼️",
			jpeg: "🖼️",
			txt: "📄",
			csv: "📊",
			zip: "📦",
		};
		return map[ext] || (item.is_folder ? "📁" : "📄");
	}

	format_size(bytes) {
		if (!bytes) return "";
		if (bytes < 1024) return bytes + " B";
		if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
		return (bytes / (1024 * 1024)).toFixed(1) + " MB";
	}

	set_view(mode) {
		this.view_mode = mode;
		this.page.main.find(".ai-view-list, .ai-view-grid").removeClass("active");
		this.page.main.find(mode === "list" ? ".ai-view-list" : ".ai-view-grid").addClass("active");
		this.load_folder_contents();
	}

	render_bulk_bar() {
		const count = this.selected.size;
		if (count === 0) {
			this.$bulkBar.addClass("hidden");
		} else {
			this.$bulkBar.removeClass("hidden").find(".ai-bulk-count").text(`${count} ${__("selected")}`);
		}
	}

	clear_selection() {
		this.selected.clear();
		this.$contents.find(".ai-checkbox input, .ai-card-checkbox input, .ai-select-all").prop("checked", false);
		this.render_bulk_bar();
	}

	show_item_menu(item, event) {
		const isFolder = !!item.is_folder;
		const menuItems = [];
		if (isFolder) {
			menuItems.push({ label: __("Open"), action: () => this.open_folder(item.name) });
			menuItems.push({ label: __("Rename"), action: () => this.rename_folder(item.name) });
			menuItems.push({ label: __("Move"), action: () => this.move_folder(item.name) });
			menuItems.push({ label: __("Favorite"), action: () => this.add_favorite(item.name) });
			menuItems.push({ label: __("Delete"), action: () => this.delete_folder(item.name), danger: true });
		} else {
			menuItems.push({ label: __("Open"), action: () => this.preview_file(item.name) });
			menuItems.push({ label: __("Rename"), action: () => this.rename_file(item.name) });
			menuItems.push({ label: __("Move To…"), action: () => this.move_file(item.name) });
			menuItems.push({ label: __("Copy"), action: () => this.copy_file(item.name) });
			menuItems.push({ label: __("Download"), action: () => window.open(item.file_url, "_blank") });
			if (item.ai_document) {
				menuItems.push({ label: __("Open Knowledge Document"), action: () => frappe.set_route("Form", "AI Document", item.ai_document) });
			}
			menuItems.push({ label: __("Delete"), action: () => this.delete_file(item.name), danger: true });
		}
		// Use frappe's menu util if available
		if (frappe.utils.show_menu) {
			frappe.utils.show_menu(menuItems, event.pageX || 0, event.pageY || 0);
		} else {
			// Fallback: prompt select
			frappe.prompt(
				{
					fieldname: "action",
					fieldtype: "Select",
					label: item.file_name,
					options: menuItems.map((m) => m.label),
					reqd: 1,
				},
				(values) => {
					const chosen = menuItems.find((m) => m.label === values.action);
					if (chosen) chosen.action();
				}
			);
		}
	}

	// ----- Actions wired to canonical service -----

	async create_folder() {
		frappe.prompt(
			{
				fieldname: "folder_name",
				fieldtype: "Data",
				label: __("Folder Name"),
				reqd: 1,
			},
			async (values) => {
				try {
					await frappe.xcall("ai_fr_hg.api.folders.create_folder", {
						folder_name: values.folder_name,
						parent_folder: this.current_folder || "Home",
					});
					frappe.show_alert({ message: __("Folder {0} created.", [values.folder_name]), indicator: "green" });
					this.refresh();
				} catch (e) {
					frappe.msgprint({ title: __("Could not create folder"), message: e.message, indicator: "red" });
				}
			},
			__("New Folder"),
			__("Create")
		);
	}

	async rename_folder(folderName) {
		const info = await frappe.xcall("ai_fr_hg.api.folders.get_folder_info", { folder_name: folderName });
		frappe.prompt(
			{
				fieldname: "new_name",
				fieldtype: "Data",
				label: __("New Name"),
				default: info.file_name,
				reqd: 1,
			},
			async (values) => {
				try {
					await frappe.xcall("ai_fr_hg.api.folders.rename_folder", {
						folder_name: folderName,
						new_name: values.new_name,
					});
					frappe.show_alert({ message: __("Folder renamed."), indicator: "green" });
					this.refresh();
					// If we renamed current folder, navigate to new path
					if (folderName === this.current_folder) {
						const newPath = `${info.folder || "Home"}/${values.new_name}`;
						this.open_folder(newPath);
					}
				} catch (e) {
					frappe.msgprint({ title: __("Rename failed"), message: e.message, indicator: "red" });
				}
			},
			__("Rename Folder"),
			__("Rename")
		);
	}

	async rename_file(fileName) {
		const info = await frappe.xcall("ai_fr_hg.api.folders.get_file_info", { file_name: fileName });
		frappe.prompt(
			{
				fieldname: "new_name",
				fieldtype: "Data",
				label: __("New Name"),
				default: info.file_name,
				reqd: 1,
			},
			async (values) => {
				try {
					await frappe.xcall("ai_fr_hg.api.folders.rename_file", {
						file_name: fileName,
						new_name: values.new_name,
					});
					frappe.show_alert({ message: __("File renamed."), indicator: "green" });
					this.load_folder_contents();
				} catch (e) {
					frappe.msgprint({ title: __("Rename failed"), message: e.message, indicator: "red" });
				}
			},
			__("Rename File"),
			__("Rename")
		);
	}

	async move_file(fileName) {
		try {
			const target = await frappe.ai.folder.pick_folder({ default_folder: this.current_folder });
			await frappe.xcall("ai_fr_hg.api.folders.move_file", { file_name: fileName, target_folder: target });
			frappe.show_alert({ message: __("File moved to {0}", [target]), indicator: "green" });
			this.load_folder_contents();
		} catch (e) {
			frappe.msgprint({ title: __("Move failed"), message: e.message, indicator: "red" });
		}
	}

	async move_folder(folderName) {
		try {
			const target = await frappe.ai.folder.pick_folder({ default_folder: "Home" });
			await frappe.xcall("ai_fr_hg.api.folders.move_folder", { folder_name: folderName, target_folder: target });
			frappe.show_alert({ message: __("Folder moved to {0}", [target]), indicator: "green" });
			this.refresh();
		} catch (e) {
			frappe.msgprint({ title: __("Move failed"), message: e.message, indicator: "red" });
		}
	}

	async delete_folder(folderName) {
		frappe.confirm(__("Delete folder {0} and all its contents? This cannot be undone.", [folderName]), async () => {
			try {
				await frappe.xcall("ai_fr_hg.api.folders.delete_folder", { folder_name: folderName, recursive: 1 });
				frappe.show_alert({ message: __("Folder deleted"), indicator: "green" });
				if (this.current_folder && this.current_folder.startsWith(folderName)) {
					this.current_folder = "Home";
				}
				this.refresh();
			} catch (e) {
				// Handle FolderNotEmpty typed error gracefully
				if (e.message && e.message.includes("not empty")) {
					frappe.confirm(
						__("Folder is not empty. Delete recursively?"), async () => {
							try {
								await frappe.xcall("ai_fr_hg.api.folders.delete_folder", { folder_name: folderName, recursive: 1 });
								frappe.show_alert({ message: __("Folder deleted"), indicator: "green" });
								this.refresh();
							} catch (inner) {
								frappe.msgprint({ title: __("Delete failed"), message: inner.message, indicator: "red" });
							}
						}
					);
				} else {
					frappe.msgprint({ title: __("Delete failed"), message: e.message, indicator: "red" });
				}
			}
		});
	}

	async delete_file(fileName) {
		frappe.confirm(__("Delete this file?"), async () => {
			try {
				await frappe.xcall("ai_fr_hg.api.folders.delete_file", { file_name: fileName });
				frappe.show_alert({ message: __("File deleted"), indicator: "green" });
				this.load_folder_contents();
			} catch (e) {
				frappe.msgprint({ title: __("Delete failed"), message: e.message, indicator: "red" });
			}
		});
	}

	async copy_file(fileName) {
		try {
			const target = await frappe.ai.folder.pick_folder({ default_folder: this.current_folder });
			await frappe.xcall("ai_fr_hg.api.folders.copy_file", { file_name: fileName, target_folder: target });
			frappe.show_alert({ message: __("File copied to {0}", [target]), indicator: "green" });
			this.load_folder_contents();
		} catch (e) {
			frappe.msgprint({ title: __("Copy failed"), message: e.message, indicator: "red" });
		}
	}

	async toggle_favorite_current() {
		if (!this.current_folder) return;
		try {
			// Check if already favorited
			const isFav = this.favorites?.some((f) => f.name === this.current_folder);
			if (isFav) {
				await frappe.xcall("ai_fr_hg.api.folders.remove_favorite", { folder: this.current_folder });
				frappe.show_alert({ message: __("Removed from favorites"), indicator: "blue" });
			} else {
				await frappe.xcall("ai_fr_hg.api.folders.add_favorite", { folder: this.current_folder });
				frappe.show_alert({ message: __("Added to favorites"), indicator: "green" });
			}
			this.refresh();
		} catch (e) {
			frappe.msgprint({ title: __("Favorite failed"), message: e.message, indicator: "red" });
		}
	}

	async add_favorite(folder) {
		await frappe.xcall("ai_fr_hg.api.folders.add_favorite", { folder });
		frappe.show_alert({ message: __("Added to favorites"), indicator: "green" });
		this.refresh();
	}

	async bulk_move() {
		if (!this.selected.size) {
			frappe.msgprint(__("Select files to move first."));
			return;
		}
		try {
			const target = await frappe.ai.folder.pick_folder({ default_folder: this.current_folder });
			const result = await frappe.xcall("ai_fr_hg.api.folders.bulk_move", {
				file_names: Array.from(this.selected),
				target_folder: target,
			});
			if (result.status === "Queued") {
				frappe.show_alert({
					message: __("Bulk move queued as {0}. {1} items will be moved in background.", [result.job_id, result.count]),
					indicator: "blue",
				});
			} else {
				frappe.show_alert({
					message: __("Moved {0} items, {1} errors.", [result.moved?.length || 0, result.errors?.length || 0]),
					indicator: result.errors?.length ? "orange" : "green",
				});
			}
			this.clear_selection();
			this.load_folder_contents();
		} catch (e) {
			frappe.msgprint({ title: __("Bulk move failed"), message: e.message, indicator: "red" });
		}
	}

	async bulk_copy() {
		if (!this.selected.size) return;
		frappe.msgprint(__("Bulk copy: select a destination folder for each file individually via Copy, or use Move for bulk operations."));
	}

	async bulk_delete() {
		if (!this.selected.size) return;
		frappe.confirm(__("Delete {0} selected items?", [this.selected.size]), async () => {
			let deleted = 0;
			let errors = 0;
			for (const name of Array.from(this.selected)) {
				try {
					const info = await frappe.xcall("ai_fr_hg.api.folders.get_file_info", { file_name: name });
					if (info.is_folder) {
						await frappe.xcall("ai_fr_hg.api.folders.delete_folder", { folder_name: name, recursive: 1 });
					} else {
						await frappe.xcall("ai_fr_hg.api.folders.delete_file", { file_name: name });
					}
					deleted++;
				} catch (e) {
					errors++;
					console.error(e);
				}
			}
			frappe.show_alert({ message: __("Deleted {0}, {1} errors.", [deleted, errors]), indicator: errors ? "orange" : "green" });
			this.clear_selection();
			this.refresh();
		});
	}

	async upload() {
		new frappe.ui.FileUploader({
			folder: this.current_folder || "Home",
			on_success: () => {
				frappe.show_alert({ message: __("File uploaded to {0}", [this.current_folder]), indicator: "green" });
				this.load_folder_contents();
			},
		});
	}

	async preview_file(fileName) {
		try {
			const info = await frappe.xcall("ai_fr_hg.api.folders.get_file_info", { file_name: fileName });
			const file_url = info.file_url;
			const ai_doc = info.ai_document;
			let html = `
				<div style="padding:12px;">
					<h5>${frappe.utils.escape_html(info.file_name)}</h5>
					<div class="text-muted small">${frappe.utils.escape_html(info.folder)} · ${info.file_size ? this.format_size(info.file_size) : ""} · ${frappe.utils.escape_html(info.file_type || "")}</div>
					<div style="margin-top:12px;">
						${file_url ? `<a href="${file_url}" target="_blank" class="btn btn-sm btn-primary">${__("Download")}</a>` : ""}
						<button class="btn btn-sm btn-default ai-preview-move">${__("Move")}</button>
						<button class="btn btn-sm btn-default ai-preview-copy">${__("Copy")}</button>
						${ai_doc ? `<a href="/app/ai-document/${ai_doc.name}" class="btn btn-sm btn-default">${__("View AI Document")}</a>` : ""}
					</div>
					${ai_doc ? `<div class="text-muted small" style="margin-top:10px;">${__("Knowledge status")}: <span class="indicator-pill ${ai_doc.status === "Indexed" ? "green" : "orange"}">${ai_doc.status}</span></div>` : ""}
					<div class="text-muted small" style="margin-top:8px;">${__("Owner")}: ${frappe.utils.escape_html(info.owner)} · ${frappe.datetime.prettyDate(info.modified)}</div>
				</div>
			`;
			if (file_url && (file_url.toLowerCase().endsWith(".png") || file_url.toLowerCase().endsWith(".jpg") || file_url.toLowerCase().endsWith(".jpeg") || file_url.toLowerCase().endsWith(".gif"))) {
				html += `<div style="padding:12px; text-align:center;"><img src="${file_url}" style="max-width:100%; max-height:400px; border:1px solid var(--border-color);"></div>`;
			}
			const dialog = new frappe.ui.Dialog({
				title: info.file_name,
				wide: true,
				fields: [{ fieldtype: "HTML", fieldname: "preview_html", options: html }],
			});
			dialog.show();
			dialog.$wrapper.find(".ai-preview-move").on("click", async () => {
				dialog.hide();
				await this.move_file(fileName);
			});
			dialog.$wrapper.find(".ai-preview-copy").on("click", async () => {
				dialog.hide();
				await this.copy_file(fileName);
			});
		} catch (e) {
			frappe.msgprint({ title: __("Preview failed"), message: e.message, indicator: "red" });
		}
	}

	async global_search() {
		frappe.prompt(
			{
				fieldname: "query",
				fieldtype: "Data",
				label: __("Search files and folders"),
				reqd: 1,
			},
			async (values) => {
				const result = await frappe.xcall("ai_fr_hg.api.folders.search", { query: values.query, limit: 50 });
				// Render results as folder contents
				this.$breadcrumbs.html(`<span class="text-muted">${__("Search: {0}", [values.query])}</span>`);
				this.render_search_results(result);
			},
			__("Search"),
			__("Search")
		);
	}
}
