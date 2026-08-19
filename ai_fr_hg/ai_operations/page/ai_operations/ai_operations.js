// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Operations - real-time platform health, usage and readiness.
 */

frappe.pages["ai-operations"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI Operations"),
		single_column: true,
	});
	wrapper.dashboard = new AIOperations(page);
};

frappe.pages["ai-operations"].on_page_show = function (wrapper) {
	wrapper.dashboard && wrapper.dashboard.refresh();
};

class AIOperations {
	constructor(page) {
		this.page = page;
		this.make();
		this.refresh();
		// Keep the operations view live without hammering the server.
		this.timer = setInterval(() => this.refresh(true), 30000);
	}

	make() {
		this.page.main.addClass("ai-ops-page");
		this.page.main.html(`
			<div class="ai-ops">
				<div class="ai-ops-readiness"></div>
				<div class="ai-ops-metrics"></div>
				<div class="ai-ops-grid">
					<div class="ai-ops-panel ai-ops-providers">
						<div class="ai-ops-panel-header">
							<strong>${__("Providers")}</strong>
							<button class="btn btn-xs btn-default ai-test-providers">${__("Test All")}</button>
						</div>
						<div class="ai-ops-panel-body"></div>
					</div>
					<div class="ai-ops-panel ai-ops-models">
						<div class="ai-ops-panel-header">
							<strong>${__("Top Models")}</strong>
							<button class="btn btn-xs btn-default ai-open-models">${__("Manage")}</button>
						</div>
						<div class="ai-ops-panel-body"></div>
					</div>
					<div class="ai-ops-panel ai-ops-approvals">
						<div class="ai-ops-panel-header">
							<strong>${__("Pending Approvals")}</strong>
						</div>
						<div class="ai-ops-panel-body"></div>
					</div>
					<div class="ai-ops-panel ai-ops-errors">
						<div class="ai-ops-panel-header">
							<strong>${__("Recent Failures")}</strong>
							<button class="btn btn-xs btn-default ai-open-logs">${__("All Logs")}</button>
						</div>
						<div class="ai-ops-panel-body"></div>
					</div>
					<div class="ai-ops-panel ai-ops-queues">
						<div class="ai-ops-panel-header"><strong>${__("Background Queues")}</strong></div>
						<div class="ai-ops-panel-body"></div>
					</div>
					<div class="ai-ops-panel ai-ops-users">
						<div class="ai-ops-panel-header"><strong>${__("Top Users (7 days)")}</strong></div>
						<div class="ai-ops-panel-body"></div>
					</div>
				</div>
			</div>
		`);

		this.page.set_primary_action(__("Refresh"), () => this.refresh());
		this.page.add_menu_item(__("Platform Settings"), () =>
			frappe.set_route("Form", "AI Platform Settings")
		);
		this.page.add_menu_item(__("Model Manager"), () => frappe.set_route("ai-model-manager"));
		this.page.add_menu_item(__("Purge Old Logs"), () => this.purge_logs());

		this.page.main.find(".ai-test-providers").on("click", () => this.test_providers());
		this.page.main
			.find(".ai-open-models")
			.on("click", () => frappe.set_route("ai-model-manager"));
		this.page.main
			.find(".ai-open-logs")
			.on("click", () => frappe.set_route("List", "AI Execution Log", { status: "Failed" }));
	}

	async refresh(silent) {
		try {
			const [data, status] = await Promise.all([
				frappe.xcall("ai_fr_hg.api.admin.get_dashboard"),
				frappe.xcall("ai_fr_hg.api.admin.get_system_status"),
			]);
			this.data = data;
			this.render_readiness(status);
			this.render_metrics(data);
			this.render_providers(data.providers_detail);
			this.render_models(data.models_detail);
			this.render_approvals(data.pending_approvals);
			this.render_errors(data.recent_errors);
			this.render_queues(data.active_jobs);
			this.render_users(data.top_users);
		} catch (error) {
			if (!silent) frappe.msgprint(__("Could not load the operations dashboard."));
		}
	}

	render_readiness(status) {
		const $wrap = this.page.main.find(".ai-ops-readiness").empty();
		if (status.ready) {
			$wrap.html(`
				<div class="ai-banner ai-banner-success">
					${frappe.utils.icon("solid-success", "sm")}
					<span>${__("Platform is ready.")}</span>
					${status.offline_mode ? `<span class="ai-pill">${__("Strict Local Only")}</span>` : ""}
				</div>
			`);
			return;
		}

		const pending = status.checks.filter((check) => !check.status);
		$wrap.html(`
			<div class="ai-banner ai-banner-warning">
				<div>
					${frappe.utils.icon("solid-warning", "sm")}
					<strong>${__("Setup incomplete")}</strong>
					<span class="text-muted">${__("{0} step(s) remaining", [pending.length])}</span>
				</div>
				<ul class="ai-checklist">
					${pending
						.map(
							(check) => `
						<li>
							<span class="ai-check-label">${frappe.utils.escape_html(check.label)}</span>
							<span class="text-muted small">${frappe.utils.escape_html(check.hint)}</span>
						</li>`
						)
						.join("")}
				</ul>
			</div>
		`);
	}

	render_metrics(data) {
		const cards = [
			{
				label: __("Providers Online"),
				value: `${data.providers.online} / ${data.providers.total}`,
				indicator: data.providers.online ? "green" : "red",
			},
			{
				label: __("Models Available"),
				value: `${data.models.available} / ${data.models.total}`,
				indicator: data.models.available ? "blue" : "orange",
			},
			{
				label: __("Documents Indexed"),
				value: `${data.knowledge.indexed} / ${data.knowledge.documents}`,
				indicator: data.knowledge.failed ? "orange" : "green",
			},
			{ label: __("Chunks"), value: this.compact(data.knowledge.chunks), indicator: "grey" },
			{
				label: __("Requests (24h)"),
				value: this.compact(data.activity_24h.requests),
				indicator: "purple",
			},
			{
				label: __("Tokens (24h)"),
				value: this.compact(data.activity_24h.tokens),
				indicator: "purple",
			},
			{
				label: __("Avg Latency"),
				value: `${Math.round(data.activity_24h.average_latency_ms)} ms`,
				indicator: data.activity_24h.average_latency_ms > 10000 ? "orange" : "green",
			},
			{
				label: __("Failures (24h)"),
				value: data.activity_24h.failures,
				indicator: data.activity_24h.failures ? "red" : "green",
			},
		];

		this.page.main.find(".ai-ops-metrics").html(
			cards
				.map(
					(card) => `
			<div class="ai-metric">
				<div class="ai-metric-value indicator-pill-${card.indicator}">${card.value}</div>
				<div class="ai-metric-label">${card.label}</div>
			</div>`
				)
				.join("")
		);
	}

	render_providers(providers) {
		const $body = this.page.main.find(".ai-ops-providers .ai-ops-panel-body");
		if (!providers || !providers.length) {
			$body.html(this.empty(__("No providers configured."), "AI Provider"));
			return;
		}

		$body.html(`
			<table class="table table-sm ai-table">
				<tbody>
					${providers
						.map(
							(provider) => `
						<tr>
							<td>
								<a href="/app/ai-provider/${encodeURIComponent(provider.name)}">
									${frappe.utils.escape_html(provider.name)}
								</a>
								<div class="text-muted small">${frappe.utils.escape_html(provider.base_url)}</div>
							</td>
							<td class="text-right">
								<span class="indicator-pill ${this.status_color(provider.status)}">
									${provider.status}
								</span>
								<div class="text-muted small">
									${provider.latency_ms ? provider.latency_ms + " ms" : ""}
									${
										provider.available_model_count
											? " · " +
											  provider.available_model_count +
											  " " +
											  __("models")
											: ""
									}
								</div>
							</td>
						</tr>`
						)
						.join("")}
				</tbody>
			</table>
		`);
	}

	render_models(models) {
		const $body = this.page.main.find(".ai-ops-models .ai-ops-panel-body");
		if (!models || !models.length) {
			$body.html(this.empty(__("No models registered."), "AI Model"));
			return;
		}

		$body.html(`
			<table class="table table-sm ai-table">
				<thead>
					<tr>
						<th>${__("Model")}</th>
						<th class="text-right">${__("Requests")}</th>
						<th class="text-right">${__("Avg")}</th>
					</tr>
				</thead>
				<tbody>
					${models
						.slice(0, 8)
						.map(
							(model) => `
						<tr>
							<td>
								<a href="/app/ai-model/${encodeURIComponent(model.name)}">
									${frappe.utils.escape_html(model.model_label)}
								</a>
								<span class="indicator-pill ${this.status_color(model.status)} ai-mini-pill">
									${model.model_type}
								</span>
							</td>
							<td class="text-right">${this.compact(model.total_requests)}</td>
							<td class="text-right">${Math.round(model.average_latency_ms || 0)} ms</td>
						</tr>`
						)
						.join("")}
				</tbody>
			</table>
		`);
	}

	render_approvals(approvals) {
		const $body = this.page.main.find(".ai-ops-approvals .ai-ops-panel-body");
		if (!approvals || !approvals.length) {
			$body.html(
				`<div class="ai-ops-empty text-muted">${__("Nothing awaiting approval.")}</div>`
			);
			return;
		}

		$body.empty();
		approvals.forEach((approval) => {
			const $row = $(`
				<div class="ai-approval">
					<div>
						<code>${frappe.utils.escape_html(approval.tool)}</code>
						<div class="text-muted small">
							${frappe.utils.escape_html(approval.user)} ·
							${frappe.ai.relative_time(approval.creation)}
						</div>
						<div class="ai-approval-args small">${frappe.utils.escape_html(
							(approval.arguments || "").slice(0, 200)
						)}</div>
					</div>
					<div class="ai-approval-actions">
						<button class="btn btn-xs btn-primary ai-approve">${__("Approve")}</button>
						<button class="btn btn-xs btn-default ai-reject">${__("Reject")}</button>
					</div>
				</div>
			`);

			$row.find(".ai-approve").on("click", async () => {
				await frappe.xcall("ai_fr_hg.ai.tools.approve_invocation", {
					invocation: approval.name,
				});
				frappe.show_alert({ message: __("Approved"), indicator: "green" });
				this.refresh();
			});
			$row.find(".ai-reject").on("click", async () => {
				await frappe.xcall("ai_fr_hg.ai.tools.reject_invocation", {
					invocation: approval.name,
				});
				this.refresh();
			});
			$body.append($row);
		});
	}

	render_errors(errors) {
		const $body = this.page.main.find(".ai-ops-errors .ai-ops-panel-body");
		if (!errors || !errors.length) {
			$body.html(`<div class="ai-ops-empty text-muted">${__("No recent failures.")}</div>`);
			return;
		}

		$body.html(
			errors
				.map(
					(error) => `
			<div class="ai-error-row">
				<div>
					<a href="/app/ai-execution-log/${error.name}">${error.operation}</a>
					<span class="text-muted small">${frappe.datetime.comment_when(error.creation)}</span>
				</div>
				<div class="text-muted small ai-error-message">
					${frappe.utils.escape_html((error.error_message || "").slice(0, 160))}
				</div>
			</div>`
				)
				.join("")
		);
	}

	render_queues(queues) {
		const $body = this.page.main.find(".ai-ops-queues .ai-ops-panel-body");
		const names = Object.keys(queues || {});
		if (!names.length) {
			$body.html(
				`<div class="ai-ops-empty text-muted">${__("Queue data unavailable.")}</div>`
			);
			return;
		}

		$body.html(
			names
				.map(
					(name) => `
			<div class="ai-queue-row">
				<span>${name}</span>
				<span class="indicator-pill ${queues[name] > 20 ? "orange" : "green"}">
					${queues[name]} ${__("jobs")}
				</span>
			</div>`
				)
				.join("")
		);
	}

	render_users(users) {
		const $body = this.page.main.find(".ai-ops-users .ai-ops-panel-body");
		if (!users || !users.length) {
			$body.html(
				`<div class="ai-ops-empty text-muted">${__("No usage recorded yet.")}</div>`
			);
			return;
		}

		$body.html(`
			<table class="table table-sm ai-table">
				<tbody>
					${users
						.map(
							(user) => `
						<tr>
							<td>${frappe.utils.escape_html(user.user)}</td>
							<td class="text-right">${this.compact(user.requests)} ${__("req")}</td>
							<td class="text-right text-muted">${this.compact(user.tokens)} ${__("tok")}</td>
						</tr>`
						)
						.join("")}
				</tbody>
			</table>
		`);
	}

	async test_providers() {
		frappe.show_alert({ message: __("Testing providers..."), indicator: "blue" });
		const results = await frappe.xcall("ai_fr_hg.api.admin.test_all_providers");
		const online = results.filter((result) => result.status === "Online").length;
		frappe.show_alert({
			message: __("{0} of {1} providers are online.", [online, results.length]),
			indicator: online ? "green" : "red",
		});
		this.refresh();
	}

	purge_logs() {
		frappe.prompt(
			[
				{
					fieldtype: "Select",
					fieldname: "doctype",
					label: __("Log Type"),
					options: [
						"AI Execution Log",
						"AI Service Health Log",
						"AI Audit Log",
						"AI Search Query",
					],
					reqd: 1,
				},
				{
					fieldtype: "Int",
					fieldname: "days",
					label: __("Delete records older than (days)"),
					default: 30,
					reqd: 1,
				},
			],
			async (values) => {
				const result = await frappe.xcall("ai_fr_hg.api.admin.purge_logs", values);
				frappe.msgprint(__("Deleted {0} records.", [result.deleted]));
				this.refresh();
			},
			__("Purge Logs"),
			__("Purge")
		);
	}

	status_color(status) {
		return (
			{
				Online: "green",
				Available: "green",
				Degraded: "orange",
				Unknown: "gray",
				Offline: "red",
				Missing: "red",
				Error: "red",
			}[status] || "gray"
		);
	}

	compact(value) {
		const number = Number(value || 0);
		if (number >= 1e9) return (number / 1e9).toFixed(1) + "B";
		if (number >= 1e6) return (number / 1e6).toFixed(1) + "M";
		if (number >= 1e3) return (number / 1e3).toFixed(1) + "K";
		return String(number);
	}

	empty(message, doctype) {
		return `
			<div class="ai-ops-empty text-muted">
				${frappe.utils.escape_html(message)}
				<a href="/app/${frappe.router.slug(doctype)}/new">${__("Create one")}</a>
			</div>`;
	}
}
