// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Resource Marketplace - discover, download, install, activate and manage
 * translation packages and AI templates directly from Desk.
 */

frappe.pages["resource-marketplace"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI Resource Marketplace"),
		single_column: true,
	});
	wrapper.marketplace = new AIResourceMarketplace(page);
};

frappe.pages["resource-marketplace"].on_page_show = function (wrapper) {
	wrapper.marketplace && wrapper.marketplace.refresh();
};

const RESOURCE_TYPE_LABELS = {
	"Translation Package": __("Translation"),
	"Translation Memory Pack": __("Translation Memory"),
	"AI Model": __("AI Model"),
	"AI Prompt Template": __("Prompt Template"),
	"AI Workflow Template": __("Workflow"),
	"Agent Capability": __("Agent"),
	"Language Pack": __("Language"),
	"Knowledge Resource": __("Knowledge"),
	"AI Extension": __("Extension"),
};

const DOWNLOAD_ACTIVE_STATUSES = new Set([
	"Queued",
	"Preparing",
	"Downloading",
	"Waiting Dependencies",
	"Verifying",
	"Installing",
	"Registering",
	"Activating",
	"Ready",
	"Paused",
	"Retrying",
]);

function bytesToText(value) {
	const bytes = Number(value || 0);
	if (!bytes) return "0 B";
	const units = ["B", "KB", "MB", "GB"];
	let i = 0;
	let v = bytes;
	while (v >= 1024 && i < units.length - 1) {
		v = v / 1024;
		i += 1;
	}
	return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function etaToText(seconds) {
	const s = Number(seconds || 0);
	if (s <= 0) return "—";
	if (s >= 60) {
		const mins = Math.floor(s / 60);
		const secs = s % 60;
		return `${mins}m ${secs}s`;
	}
	return `${Math.ceil(s)}s`;
}

function statusClass(status) {
	const map = {
		Available: "success",
		Installed: "info",
		"Update Available": "warning",
		Incompatible: "secondary",
		Deprecated: "secondary",
		"Security Restricted": "danger",
		Downloading: "primary",
		Queued: "primary",
		Preparing: "primary",
		"Waiting Dependencies": "primary",
		Verifying: "info",
		Installing: "info",
		Registering: "info",
		Activating: "info",
		Ready: "success",
		Completed: "success",
		Paused: "warning",
		Retrying: "warning",
		Failed: "danger",
		Cancelled: "secondary",
		Removed: "secondary",
	};
	return map[status] || "secondary";
}

function rpcError(error, fallback) {
	if (!error) return fallback;
	if (typeof error === "string") return error;
	return (
		error.message ||
		error._server_messages ||
		error.exc ||
		(error.responseJSON && error.responseJSON.exception) ||
		fallback
	);
}

function busy(instance) {
	instance._busy = true;
	frappe.show_alert({ message: __("Working…"), indicator: "blue" });
}

function done(instance) {
	instance._busy = false;
}

class AIResourceMarketplace {
	constructor(page) {
		this.page = page;
		this.activeTab = "discover";
		this.category = "";
		this.type = "";
		this.search = "";
		this.canManage = false;
		this.refreshTimer = null;
		this.pollTimer = null;
		this.make();
		this.refresh();
	}

	make() {
		this.page.main.addClass("ai-resource-marketplace");
		this.page.main.html(`
			<div class="rm-app">
				<div class="row align-items-center mb-3">
					<div class="col">
						<h4>${__("Resource Marketplace")}</h4>
						<p class="text-muted small mb-0">${__("Download translation packages, AI prompt templates, workflows, agent skills and more.")}</p>
					</div>
					<div class="col-auto">
						<span class="badge mr-2" data-view="installed-count"></span>
						<span class="badge badge-warning mr-2" data-view="updates-count"></span>
						<span class="badge badge-primary" data-view="downloads-count"></span>
					</div>
				</div>
				<div class="nav nav-tabs mb-3 rm-tabs">
					<a class="nav-link active" data-tab="discover" href="#">${__("Discover")}</a>
					<a class="nav-link" data-tab="downloads" href="#">${__("Downloads")}</a>
					<a class="nav-link" data-tab="installed" href="#">${__("Installed")}</a>
					<a class="nav-link" data-tab="updates" href="#">${__("Updates")}</a>
				</div>
				<div class="rm-view"></div>
			</div>
		`);

		this.el = {
			view: this.page.main.find(".rm-view"),
			downloadedCount: this.page.main.find('[data-view="installed-count"]'),
			updateCount: this.page.main.find('[data-view="updates-count"]'),
			activeDownloadCount: this.page.main.find('[data-view="downloads-count"]'),
		};

		this.page.main.find(".rm-tabs .nav-link").on("click", (event) => {
			event.preventDefault();
			this.activeTab = $(event.currentTarget).data("tab");
			this.page.main.find(".rm-tabs .nav-link").removeClass("active");
			$(event.currentTarget).addClass("active");
			this.render();
		});

		this.canManage =
			frappe.user.has_role("AI Manager") || frappe.user.has_role("System Manager") || frappe.user.has_role("Administrator");
	}

	refresh() {
		busy(this);
		frappe.call({
			method: "ai_fr_hg.api.resources.marketplace",
			callback: (response) => {
				done(this);
				this.data = response.message || {};
				this.renderBadges();
				this.render();
			},
			error: (error) => {
				done(this);
				frappe.show_alert({ message: rpcError(error, __("Failed to load marketplace")), indicator: "red" });
			},
		});
		this.startPolling();
	}

	startPolling() {
		if (this.pollTimer) clearInterval(this.pollTimer);
		this.pollTimer = setInterval(() => {
			if (!document.body.contains(this.page.main[0])) {
				clearInterval(this.pollTimer);
				this.pollTimer = null;
				return;
			}
			frappe.call({ method: "ai_fr_hg.api.resources.downloads", callback: (response) => {
				if (this.activeTab === "downloads") {
					this.activeDownloads = response.message || [];
					this.renderDownloads(true);
				}
				frappe.call({ method: "ai_fr_hg.api.resources.marketplace", callback: (response) => {
					this.data = response.message || this.data;
					this.renderBadges();
					if (this.activeTab !== "downloads") this.render();
				}});
			}});
		}, 2500);
	}

	renderBadges() {
		const data = this.data || { summary: {} };
		const summary = data.summary || {};
		this.el.downloadedCount.text(`${(__("Installed"))}: ${summary.installed_count || 0}`);
		this.el.downloadedCount.attr("class", "badge badge-success mr-2");
		this.el.updateCount.text(`${(__("Updates"))}: ${summary.updates_available || 0}`);
		this.el.activeDownloadCount.text(`${(__("Active Downloads"))}: ${summary.active_downloads || 0}`);
	}

	render() {
		this.canManage = this.canManage; // stable
		const views = {
			discover: () => this.renderDiscover(),
			downloads: () => this.renderDownloads(),
			installed: () => this.renderInstalled(),
			updates: () => this.renderUpdates(),
		};
		(views[this.activeTab] || views.discover)();
	}

	renderDiscover() {
		const data = this.data || {};
		const catalog = data.catalog || [];
		const recommendations = data.recommendations || [];
		const downloadSources = data.download_sources || [];
		const hasSources = downloadSources.length > 0;

		this.el.view.html(`
			<div class="row mb-3">
				<div class="col-md-8">
					<div class="rm-filters d-flex flex-wrap align-items-center">
						<input class="form-control mr-2 rm-search" placeholder="${__("Search resources…")}" />
						<select class="form-control mr-2 rm-type" style="width: 190px;">
							<option value="">${__("All Types")}</option>
							${Object.keys(RESOURCE_TYPE_LABELS).map((key) => `<option value="${key}">${RESOURCE_TYPE_LABELS[key]}</option>`).join("")}
						</select>
						<select class="form-control mr-2 rm-category" style="width: 190px;">
							<option value="">${__("All Categories")}</option>
							${this.categories(catalog).map((c) => `<option value="${c}">${c}</option>`).join("")}
						</select>
						${hasSources ? `
							<select class="form-control rm-source-filter" style="width: 220px;">
								<option value="">${__("All Download Sources")}</option>
								${downloadSources.map((s) => `<option value="${s.repository_name}">${s.repository_name} (${s.source_count || 0})</option>`).join("")}
							</select>` : ""}
					</div>
				</div>
				<div class="col-md-4 text-right">
					${this.canManage ? `<button class="btn btn-sm btn-default rm-sync">${__("Sync Catalog")}</button>` : ""}
				</div>
			</div>
			${hasSources ? this.sourcesPanel(downloadSources) : ""}
			<div class="row">
				<div class="col-12">
					<div class="rm-resource-grid"></div>
				</div>
			</div>
			${recommendations.length ? `
				<div class="mt-4">
					<h5>${__("Recommended for you")}</h5>
					<div class="rm-recommendations"></div>
				</div>` : ""}
		`);

		this.el.view.find(".rm-search").val(this.search);
		this.el.view.find(".rm-type").val(this.type);
		this.el.view.find(".rm-category").val(this.category);
		this.el.view.find(".rm-search").on("input", (event) => {
			this.search = $(event.currentTarget).val();
			this.renderResourceGrid(filterResources(catalog, this.search, this.type, this.category));
		});
		this.el.view.find(".rm-type").on("change", (event) => {
			this.type = $(event.currentTarget).val();
			this.renderResourceGrid(filterResources(catalog, this.search, this.type, this.category));
		});
		this.el.view.find(".rm-category").on("change", (event) => {
			this.category = $(event.currentTarget).val();
			this.renderResourceGrid(filterResources(catalog, this.search, this.type, this.category));
		});
		this.el.view.find(".rm-source-filter").on("change", (event) => {
			const sourceRepository = $(event.currentTarget).val();
			this.renderResourceGrid(
				filterResources(catalog, this.search, this.type, this.category).filter(
					(row) => !sourceRepository || String(row.repository || "").includes(sourceRepository) || String(row.source_url || "").includes(sourceRepository)
				)
			);
		});
		this.el.view.find(".rm-sync").on("click", () => this.syncCatalog());
		this.el.view.find(".rm-recommendations").html(
			recommendations.map((r) => this.resourceCard(r, { compact: true })).join("")
		);
		this.bindResourceCards();
		this.renderResourceGrid(filterResources(catalog, this.search, this.type, this.category));
	}

	categories(catalog) {
		const seen = [];
		(catalog || []).forEach((item) => {
			if (item.category && !seen.includes(item.category)) seen.push(item.category);
		});
		return seen.sort();
	}

	sourcesPanel(sources) {
		return `
			<div class="card mb-3 rm-sources-panel">
				<div class="card-body">
					<h6>${__("Download Sources")}</h6>
					<p class="small text-muted mb-2">${__("All sources from which translation packages and AI templates can be fetched.")}</p>
					<div class="row">
						${sources.map((source) => `
							<div class="col-md-4 mb-2">
								<div class="border rounded p-2 h-100 rm-source-card" data-repository="${source.repository_name}">
									<div class="d-flex justify-content-between">
										<strong>${source.repository_name}</strong>
										<span class="badge badge-${source.is_builtin ? "info" : "light"}">${source.repository_type}</span>
									</div>
									<div class="small text-muted mt-1">
										${source.offline_supported ? `<span class="badge badge-light">${__("Offline")}</span>` : ""}
										${source.requires_authorization ? `<span class="badge badge-light">${__("Authorized")}</span>` : ""}
										${__("Sources")}: ${source.source_count || 0}
									</div>
									<div class="small text-muted mt-1">${source.description || source.source_url || "—"}</div>
									${source.source_url ? `<div class="small text-truncate text-muted">${source.source_url}</div>` : ""}
								</div>
							</div>
						`).join("")}
					</div>
				</div>
			</div>
		`;
	}

	renderResourceGrid(rows) {
		const grid = this.el.view.find(".rm-resource-grid");
		if (!rows.length) {
			grid.html(`<div class="text-center text-muted py-5">${__("No resources match your filters.")}</div>`);
			return;
		}
		grid.html(`<div class="row">${rows.map((r) => this.resourceCard(r)).join("")}</div>`);
		this.bindResourceCards();
	}

	resourceCard(resource, options = {}) {
		const compact = options.compact;
		const status = resource.status || "Available";
		const actions = this.resourceAction(resource);
		const sources = resource.sources || [];
		const defaultSource = sources.find((s) => s.is_default) || sources[0];
		const meta = [
			resource.version ? __("v{0}").format(resource.version) : "",
			resource.package_size_mb ? `${resource.package_size_mb} MB` : "",
			resource.publisher || "",
			(sources.length || resource.source_count) ? __("{0} source(s)").format(sources.length || resource.source_count) : "",
		].filter(Boolean).join(" · ");

		return `
			<div class="col-md-${compact ? "6" : "4"} mb-3">
				<div class="card h-100 rm-card" data-resource="${resource.name || resource.resource_code}" role="button">
					<div class="card-body">
						<div class="d-flex justify-content-between align-items-start">
							<div>
								<div class="text-uppercase small text-muted">${__("Type")}: ${RESOURCE_TYPE_LABELS[resource.resource_type] || resource.resource_type}</div>
								<h5 class="mb-1">${resource.resource_name}</h5>
							</div>
							<span class="badge badge-${statusClass(status)}">${__(status)}</span>
						</div>
						<p class="text-muted small mb-1">${resource.description || ""}</p>
						<div class="small text-muted">${meta}</div>
						${defaultSource ? `<div class="small text-muted mt-1">${__("Primary source")}: ${defaultSource.source_name} <span class="badge badge-light">${defaultSource.source_type}</span>${defaultSource.offline_supported ? ` <span class="badge badge-light">${__("Offline")}</span>` : ""}</div>` : ""}
						${actions ? `<div class="mt-2">${actions}</div>` : ""}
						${resource.reason ? `<div class="small text-muted mt-2"><i>${resource.reason}</i></div>` : ""}
					</div>
				</div>
			</div>
		`;
	}

	resourceAction(resource) {
		const status = resource.status;
		if (!this.canManage && status !== "Downloading") return "";
		if (status === "Installed") {
			return `<button class="btn btn-sm btn-default rm-open" data-action="open" data-resource="${resource.resource_code}">${__("Details")}</button>`;
		}
		if (status === "Update Available") {
			return `<button class="btn btn-sm btn-primary rm-update" data-action="update" data-resource="${resource.resource_code}">${__("Update")}</button>`;
		}
		if (status === "Downloading") {
			return `<button class="btn btn-sm btn-default rm-open" data-action="open" data-resource="${resource.resource_code}">${__("View Download")}</button>`;
		}
		if (status === "Incompatible" || status === "Security Restricted" || status === "Deprecated") {
			return `<button class="btn btn-sm btn-default rm-open" data-action="open" data-resource="${resource.resource_code}">${__("Details")}</button>`;
		}
		return `<button class="btn btn-sm btn-primary rm-download" data-action="download" data-resource="${resource.resource_code}">${__("Download")}</button>`;
	}

	bindResourceCards() {
		this.el.view.find(".rm-card").off("click").on("click", (event) => {
			const resource = $(event.currentTarget).data("resource");
			if ($(event.target).closest("button").length) return;
			this.openResource(resource);
		});
		this.el.view.find("[data-action='download']").off("click").on("click", async (event) => {
			event.stopPropagation();
			await this.startDownload($(event.currentTarget).data("resource"));
		});
		this.el.view.find("[data-action='open']").off("click").on("click", (event) => {
			event.stopPropagation();
			this.openResource($(event.currentTarget).data("resource"));
		});
		this.el.view.find("[data-action='update']").off("click").on("click", async (event) => {
			event.stopPropagation();
			await this.updateResource($(event.currentTarget).data("resource"));
		});
	}

	renderDownloads(keep = false) {
		const data = this.data || {};
		const downloads = (this.activeDownloads || data.downloads || []).filter((d) => DOWNLOAD_ACTIVE_STATUSES.has(d.status) && !d.is_cancelled);

		const html = `
			<div class="row">
				<div class="col-md-8">
					<h5>${__("Active Downloads")}</h5>
					${downloads.length ? `<div class="rm-downloads">${downloads.map((d) => this.downloadRow(d)).join("")}</div>` :
						`<div class="text-muted py-4">${__("No active downloads. Completed downloads move to Installed automatically.")}</div>`}
				</div>
				<div class="col-md-4">
					<h5>${__("History")}</h5>
					<button class="btn btn-sm btn-default rm-history">${__("View Download History")}</button>
				</div>
			</div>
		`;
		if (!keep) this.el.view.html(html);
		else this.el.view.find(".rm-downloads").html(downloads.map((d) => this.downloadRow(d)).join(""));

		if (!keep) {
			this.el.view.find(".rm-history").on("click", () => this.showHistory());
		}
		this.bindDownloadActions();
	}

	downloadRow(d) {
		const status = d.status;
		const progress = Number(d.progress || 0);
		const classForState = statusClass(status);
		return `
			<div class="card mb-3 rm-download-card" data-download="${d.name}">
				<div class="card-body">
					<div class="d-flex justify-content-between">
						<div>
							<div class="font-weight-bold">${d.resource_name}</div>
							<div class="small text-muted">${d.version || ""} ${d.is_dependency ? `<span class="badge badge-secondary">${__("Dependency")}</span>` : ""}</div>
						</div>
						<span class="badge badge-${classForState}">${__(status)}</span>
					</div>
					<div class="mt-2">
						<div class="small text-muted mb-1">${d.stage || status}${d.stage_message ? ` — ${d.stage_message}` : ""}</div>
						<div class="progress" style="height: 10px;">
							<div class="progress-bar" style="width: ${progress}%"></div>
						</div>
						<div class="row small text-muted mt-1">
							<div class="col">${__("Progress")}: ${progress}%</div>
							<div class="col">${__("Size")}: ${bytesToText(d.downloaded_bytes)} / ${bytesToText(d.total_bytes)}</div>
							<div class="col">${__("Speed")}: ${d.transfer_speed_kbps ? `${d.transfer_speed_kbps} KB/s` : "—"}</div>
							<div class="col">${__("ETA")}: ${etaToText(d.eta_seconds)}</div>
							<div class="col">${__("Network")}: ${d.network_status || "—"}</div>
							<div class="col">${__("Quality")}: ${d.connection_quality || "—"}</div>
						</div>
					</div>
					${d.verify_message || d.install_message ? `<div class="small text-muted mt-1">${d.verify_message || ""} ${d.install_message || ""}</div>` : ""}
					${d.error_message ? `<div class="small text-danger mt-1">${d.error_message}</div>` : ""}
					${this.canManage ? this.downloadActions(d) : ""}
				</div>
			</div>
		`;
	}

	downloadActions(d) {
		const status = d.status;
		if (status === "Paused" || status === "Failed" || status === "Retrying") {
			return `<div class="mt-2">
				<button class="btn btn-sm btn-primary rm-resume" data-action="resume" data-download="${d.name}">${__("Resume")}</button>
				${status === "Failed" || status === "Retrying" ? `<button class="btn btn-sm btn-default rm-retry" data-action="retry" data-download="${d.name}">${__("Retry")}</button>` : ""}
				<button class="btn btn-sm btn-default rm-cancel" data-action="cancel" data-download="${d.name}">${__("Cancel")}</button>
			</div>`;
		}
		if (status === "Cancelled") return "";
		return `<div class="mt-2">
			<button class="btn btn-sm btn-default rm-pause" data-action="pause" data-download="${d.name}">${__("Pause")}</button>
			<button class="btn btn-sm btn-default rm-cancel" data-action="cancel" data-download="${d.name}">${__("Cancel")}</button>
		</div>`;
	}

	bindDownloadActions() {
		const esc = this.el.view;
		esc.find("[data-action='pause']").off("click").on("click", (event) => this.pauseDownload($(event.currentTarget).data("download")));
		esc.find("[data-action='resume']").off("click").on("click", (event) => this.resumeDownload($(event.currentTarget).data("download")));
		esc.find("[data-action='retry']").off("click").on("click", (event) => this.retryDownload($(event.currentTarget).data("download")));
		esc.find("[data-action='cancel']").off("click").on("click", (event) => this.cancelDownload($(event.currentTarget).data("download")));
	}

	renderInstalled() {
		const data = this.data || {};
		const installed = data.installed || [];
		this.el.view.html(`
			<div class="row">
				<div class="col-12">
					<h5>${__("Installed Resources")}</h5>
					${installed.length ? installed.map((r) => this.installedRow(r)).join("") : `<div class="text-muted py-4">${__("Nothing installed yet from the marketplace.")}</div>`}
				</div>
			</div>
		`);
		this.el.view.find("[data-action='details']").on("click", (event) => this.openResource($(event.currentTarget).data("resource")));
		this.el.view.find("[data-action='update']").on("click", (event) => this.updateResource($(event.currentTarget).data("resource")));
		this.el.view.find("[data-action='rollback']").on("click", (event) => this.rollback($(event.currentTarget).data("install")));
		this.el.view.find("[data-action='remove']").on("click", (event) => this.removeResource($(event.currentTarget).data("install")));
	}

	installedRow(r) {
		const canManage = this.canManage;
		return `
			<div class="card mb-2">
				<div class="card-body">
					<div class="d-flex justify-content-between">
						<div>
							<div class="font-weight-bold">${r.resource_name}</div>
							<div class="small text-muted">${RESOURCE_TYPE_LABELS[r.resource_type] || r.resource_type} · v${r.version} · ${__("Used {0} times").format(r.use_count || 0)}</div>
							<div class="small text-muted">${__("Health")}: ${r.health_status || "Unknown"} · ${__("Last used")}: ${r.last_used || "—"}</div>
						</div>
						<div>
							${r.status === "Update Available" && canManage ? `<button class="btn btn-sm btn-primary mr-1" data-action="update" data-resource="${r.resource_code}">${__("Update")}</button>` : ""}
							<button class="btn btn-sm btn-default mr-1" data-action="details" data-resource="${r.resource_code}">${__("Details")}</button>
							${canManage ? `<button class="btn btn-sm btn-default mr-1" data-action="rollback" data-install="${r.name}">${__("Rollback")}</button>` : ""}
							${canManage ? `<button class="btn btn-sm btn-danger mr-1" data-action="remove" data-install="${r.name}">${__("Remove")}</button>` : ""}
						</div>
					</div>
				</div>
			</div>
		`;
	}

	renderUpdates() {
		const data = this.data || {};
		const updates = data.updates || [];
		this.el.view.html(`
			<div class="row">
				<div class="col-12">
					<h5>${__("Available Updates")}</h5>
					${updates.length ? updates.map((r) => this.updateRow(r)).join("") : `<div class="text-muted py-4">${__("All installed resources are up to date.")}</div>`}
				</div>
			</div>
		`);
		this.el.view.find("[data-action='update']").on("click", (event) => this.updateResource($(event.currentTarget).data("resource")));
	}

	updateRow(r) {
		return `
			<div class="card mb-2">
				<div class="card-body d-flex justify-content-between">
					<div>
						<div class="font-weight-bold">${r.resource_name}</div>
						<div class="small text-muted">${__("Installed")}: v${r.version} → ${__("update available")}</div>
					</div>
					${this.canManage ? `<button class="btn btn-sm btn-primary" data-action="update" data-resource="${r.resource_code}">${__("Update Now")}</button>` : ""}
				</div>
			</div>
		`;
	}

	openResource(resourceCode) {
		frappe.call({
			method: "ai_fr_hg.api.resources.resource_detail",
			args: { name: resourceCode },
			callback: (response) => this.showResourceDetail(response.message),
			error: (error) => frappe.show_alert({ message: rpcError(error, __("Failed to load resource")), indicator: "red" }),
		});
	}

	showResourceDetail(resource) {
		const canDownload = this.canManage && resource.status === "Available";
		const status = resource.status || "Available";
		const versions = resource.versions || [];
		const dependencies = resource.dependencies || [];
		const compatibility = resource.compatibility || { checks: [] };
		const sources = resource.sources || [];
		const defaultSource = sources.find((s) => s.is_default) || sources[0];
		const selectedSource = this.lastSelectedSource || (defaultSource && defaultSource.source_name) || "";

		const dialog = new frappe.ui.Dialog({
			title: resource.resource_name,
			size: "large",
			fields: [
				{
					fieldtype: "Select",
					fieldname: "source",
					label: __("Download Source"),
					options: sources.map((s) => ({
						value: s.source_name,
						label: `${s.source_name} — ${s.source_type}${s.offline_supported ? " (Offline)" : ""}${s.requires_authorization ? " (Authorization)" : ""}`,
					})),
					default: selectedSource,
					hidden: !sources.length,
				},
			],
			primary_action_label: this.canManage && status === "Available" ? __("Download") : null,
			primary_action: async (values) => {
				await this.startDownload(resource.name || resource.resource_code, values && values.source);
				dialog.hide();
			},
		});

		const langRows = resource.supported_languages || [];
		const providerRows = resource.supported_providers || [];
		const html = `
			<div class="rm-detail">
				<div class="d-flex justify-content-between mb-2">
					<div>
						<span class="badge badge-${statusClass(status)}">${__(status)}</span>
						<span class="badge badge-light">${RESOURCE_TYPE_LABELS[resource.resource_type] || resource.resource_type}</span>
						<span class="badge badge-light">v${resource.version}</span>
					</div>
					<div class="small text-muted">${resource.publisher || ""} · ${resource.license || "MIT"}</div>
				</div>
				<p>${resource.description || ""}</p>
				<div class="row">
					<div class="col-md-6">
						<h6>${__("Package")}</h6>
						<ul class="list-unstyled small">
							<li>${__("Size")}: ${resource.package_size_mb || "—"} MB</li>
							<li>${__("Repository")}: ${resource.repository || "—"}</li>
							<li>${__("Last updated")}: ${resource.last_updated || "—"}</li>
							<li>${__("SHA-256")}: ${(resource.sha256 || "—").slice(0, 20)}…</li>
							<li>${__("Signature verified")}: ${resource.signature_verified ? "Yes" : "No"}</li>
						</ul>
						<h6>${__("Compatibility")}</h6>
						<ul class="list-unstyled small">
							${(compatibility.checks || []).map((c) => `<li><span class="badge badge-${c.ok ? "success" : "danger"}">${c.ok ? "OK" : "No"}</span> ${c.check} — ${c.detail}</li>`).join("")}
						</ul>
					</div>
					<div class="col-md-6">
						<h6>${__("Languages")}</h6>
						<div>${langRows.map((l) => `<span class="badge badge-light mr-1">${l.language_name || l.language_code}</span>`).join("") || "—"}</div>
						<h6>${__("Providers")}</h6>
						<div>${providerRows.map((p) => `<span class="badge badge-light mr-1">${p.provider_name}</span>`).join("") || "—"}</div>
						<h6>${__("Dependencies")}</h6>
						<div>${dependencies.map((d) => `<div class="small">${d.resource_code} ${d.version_constraint ? `(${d.version_constraint})` : ""}</div>`).join("") || "None"}</div>
					</div>
				</div>
				${sources.length ? `
					<h6 class="mt-3">${__("Download Sources")}</h6>
					<div class="table-responsive">
						<table class="table table-sm small rm-source-table">
							<thead><tr><th>${__("Source")}</th><th>${__("Type")}</th><th>${__("Repository")}</th><th>${__("URL / Location")}</th><th>${__("Offline")}</th><th>${__("Integrity")}</th></tr></thead>
							<tbody>
								${sources.map((s) => `
									<tr>
										<td>${s.source_name} ${s.is_default ? `<span class="badge badge-success">${__("Default")}</span>` : ""}</td>
										<td>${s.source_type}</td>
										<td>${s.repository || "—"}</td>
										<td class="text-truncate" style="max-width: 220px;">${s.source_url || "—"}</td>
										<td>${s.offline_supported ? __("Yes") : __("No")}</td>
										<td>${s.checksum ? `<span class="badge badge-success">${__("Signed")}</span>` : `<span class="badge badge-light">${__("Verify on download")}</span>`}</td>
									</tr>
								`).join("")}
							</tbody>
						</table>
					</div>` : ""}
				${resource.release_notes ? `<h6 class="mt-2">${__("Release Notes")}</h6><pre class="small">${escapeHtml(resource.release_notes)}</pre>` : ""}
				${versions.length ? `<h6 class="mt-3">${__("Versions")}</h6><ul class="small">${versions.map((v) => `<li>v${v.version} ${v.is_installed ? `<span class="badge badge-success">${__("Installed")}</span>` : ""} ${v.installed_on ? ` · ${v.installed_on}` : ""}</li>`).join("")}</ul>` : ""}
				${this.canManage && status === "Installed" ? `<button class="btn btn-sm btn-primary rm-install-update">${__("Update if available")}</button>` : ""}
			</div>
		`;
		dialog.body.html(html);
		dialog.body.find(".rm-install-update").on("click", async () => {
			await this.updateResource(resource.resource_code);
			dialog.hide();
		});
		dialog.show();
	}

	async startDownload(resourceCode, source) {
		if (!this.canManage) {
			frappe.show_alert({ message: __("AI Manager or System Manager role required."), indicator: "red" });
			return;
		}
		this.lastSelectedSource = source || "";
		busy(this);
		try {
			const args = { name: resourceCode };
			if (source) args.source = source;
			const response = await frappe.call({ method: "ai_fr_hg.api.resources.start_download", args });
			done(this);
			frappe.show_alert({ message: __("Download started for {0}").format(resourceCode), indicator: "blue" });
			this.activeTab = "downloads";
			this.page.main.find(".rm-tabs .nav-link").removeClass("active");
			this.page.main.find(`.rm-tabs [data-tab="downloads"]`).addClass("active");
			this.refresh();
		} catch (error) {
			done(this);
			frappe.show_alert({ message: rpcError(error, __("Failed to start download")), indicator: "red" });
		}
	}

	async pauseDownload(name) {
		try {
			await frappe.call({ method: "ai_fr_hg.api.resources.pause_download", args: { name } });
			await this.refresh();
		} catch (error) {
			this.showError(error);
		}
	}

	async resumeDownload(name) {
		try {
			await frappe.call({ method: "ai_fr_hg.api.resources.resume_download_api", args: { name } });
			await this.refresh();
		} catch (error) {
			this.showError(error);
		}
	}

	async retryDownload(name) {
		try {
			await frappe.call({ method: "ai_fr_hg.api.resources.retry_download_api", args: { name } });
			await this.refresh();
		} catch (error) {
			this.showError(error);
		}
	}

	async cancelDownload(name) {
		try {
			await frappe.call({ method: "ai_fr_hg.api.resources.cancel_download", args: { name } });
			await this.refresh();
		} catch (error) {
			this.showError(error);
		}
	}

	async updateResource(resourceCode) {
		try {
			await frappe.call({ method: "ai_fr_hg.api.resources.update_resource_api", args: { name: resourceCode } });
			frappe.show_alert({ message: __("Update queued") }, 3);
			this.activeTab = "downloads";
			this.page.main.find(".rm-tabs .nav-link").removeClass("active");
			this.page.main.find(`.rm-tabs [data-tab="downloads"]`).addClass("active");
			this.refresh();
		} catch (error) {
			this.showError(error);
		}
	}

	async rollback(installName) {
		try {
			await frappe.call({ method: "ai_fr_hg.api.resources.rollback_api", args: { install_name: installName } });
			frappe.show_alert({ message: __("Rollback queued") }, 3);
		} catch (error) {
			this.showError(error);
		}
	}

	async removeResource(installName) {
		frappe.confirm(__("Remove this installed resource? Its target records will be disabled, but no documents are deleted."), async () => {
			try {
				await frappe.call({ method: "ai_fr_hg.api.resources.remove_api", args: { install_name: installName } });
				frappe.show_alert({ message: __("Resource removed") }, 3);
				this.refresh();
			} catch (error) {
				this.showError(error);
			}
		});
	}

	async syncCatalog() {
		busy(this);
		try {
			await frappe.call({ method: "ai_fr_hg.api.resources.sync_catalog" });
			done(this);
			frappe.show_alert({ message: __("Catalog refreshed") }, 3);
			this.refresh();
		} catch (error) {
			done(this);
			this.showError(error);
		}
	}

	async showHistory() {
		frappe.call({
			method: "ai_fr_hg.api.resources.download_history",
			callback: (response) => {
				const rows = response.message || [];
				const dialog = new frappe.ui.Dialog({ title: __("Download History"), size: "large", fields: [] });
				dialog.body.html(`
					<table class="table table-sm">
						<thead><tr><th>${__("Resource")}</th><th>${__("Version")}</th><th>${__("Status")}</th><th>${__("Progress")}</th><th>${__("Started")}</th><th>${__("Error")}</th></tr></thead>
						<tbody>${rows.map((r) => `
							<tr>
								<td>${r.resource_name}</td><td>${r.version}</td><td><span class="badge badge-${statusClass(r.status)}">${__(r.status)}</span></td>
								<td>${r.progress}%</td><td>${r.creation || ""}</td><td>${r.error_message || ""}</td>
							</tr>`).join("")}</tbody>
					</table>
				`);
				dialog.show();
			},
			error: (error) => this.showError(error),
		});
	}

	showError(error) {
		frappe.show_alert({ message: rpcError(error, __("Failed")), indicator: "red" });
	}
}

function filterResources(catalog, search, type, category) {
	const text = (search || "").toLowerCase();
	return (catalog || []).filter((row) => {
		if (type && row.resource_type !== type) return false;
		if (category && row.category !== category) return false;
		if (!text) return true;
		return [row.resource_name, row.resource_code, row.description, row.publisher, row.category]
			.filter(Boolean)
			.some((value) => String(value).toLowerCase().includes(text));
	});
}

function escapeHtml(value) {
	return String(value || "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;");
}
