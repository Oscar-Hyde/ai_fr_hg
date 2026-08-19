// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Model Manager - install, test and govern local models per provider.
 */

frappe.pages["ai-model-manager"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI Model Manager"),
		single_column: true,
	});
	wrapper.manager = new AIModelManager(page);
};

frappe.pages["ai-model-manager"].on_page_show = function (wrapper) {
	wrapper.manager && wrapper.manager.refresh();
};

/** Curated starting points for a fresh Ollama install. */
const SUGGESTED_MODELS = [
	{ name: "qwen2.5:0.5b", type: "Chat", note: __("Fits machines with ~10 GB RAM, ~400 MB") },
	{ name: "phi3:mini", type: "Chat", note: __("Small and capable, ~2.3 GB") },
	{
		name: "qwen2.5:7b",
		type: "Chat",
		note: __("Needs ~11 GB free RAM — skip if Test says out of memory"),
	},
	{
		name: "llama3.1:8b",
		type: "Chat",
		note: __("Needs ~11 GB free RAM — too large for a 10 GB machine"),
	},
	{ name: "mistral:7b", type: "Chat", note: __("Needs ~10 GB free RAM") },
	{ name: "nomic-embed-text", type: "Embedding", note: __("Recommended embeddings, ~274 MB") },
	{
		name: "mxbai-embed-large",
		type: "Embedding",
		note: __("Higher quality embeddings, ~670 MB"),
	},
	{ name: "llava:7b", type: "Vision", note: __("Needs ~16 GB free RAM") },
];

function rpc_error_message(error, fallback) {
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

class AIModelManager {
	constructor(page) {
		this.page = page;
		this.make();
		this.refresh();
	}

	make() {
		this.page.main.addClass("ai-ops-page");
		this.page.main.html(`
			<div class="ai-models">
				<div class="ai-models-toolbar">
					<div class="ai-provider-filter"></div>
					<div class="ai-models-actions">
						<button class="btn btn-sm btn-default ai-discover">${__("Discover Models")}</button>
						<button class="btn btn-sm btn-primary ai-install">${__("Install Model")}</button>
					</div>
				</div>
				<div class="ai-models-body"></div>
				<div class="ai-models-suggested">
					<h5>${__("Suggested Models")}</h5>
					<p class="text-muted small">
						${__("Install these from the terminal with `ollama pull <name>`, or use Install Model above.")}
					</p>
					<div class="ai-suggested-grid"></div>
				</div>
			</div>
		`);

		this.page.set_primary_action(__("Refresh"), () => this.refresh());
		this.page.add_menu_item(__("Operations Dashboard"), () =>
			frappe.set_route("ai-operations")
		);
		this.page.add_menu_item(__("Providers"), () => frappe.set_route("List", "AI Provider"));

		this.page.main.find(".ai-discover").on("click", () => this.discover());
		this.page.main.find(".ai-install").on("click", () => this.install());

		this.render_suggested();
	}

	async refresh() {
		const [providers, models] = await Promise.all([
			frappe.db.get_list("AI Provider", {
				fields: ["name", "provider_type", "status", "base_url", "available_model_count"],
				filters: { enabled: 1 },
				limit: 100,
			}),
			frappe.db.get_list("AI Model", {
				fields: [
					"name",
					"model_label",
					"model_name",
					"provider",
					"model_type",
					"status",
					"enabled",
					"is_default",
					"parameter_size",
					"quantization",
					"size_bytes",
					"context_window",
					"total_requests",
					"average_latency_ms",
					"embedding_dimensions",
				],
				limit: 500,
				order_by: "provider asc, model_type asc, model_label asc",
			}),
		]);

		this.providers = providers;
		this.models = models;
		this.render_filter();
		this.render_models();
	}

	render_filter() {
		if (this.filter_control) return;
		const me = this;
		this.filter_control = frappe.ui.form.make_control({
			parent: this.page.main.find(".ai-provider-filter"),
			df: {
				fieldtype: "Select",
				fieldname: "provider",
				label: __("Provider"),
				options: [{ label: __("All providers"), value: "" }].concat(
					this.providers.map((provider) => ({
						label: provider.name,
						value: provider.name,
					}))
				),
				change() {
					me.active_provider = this.get_value();
					me.render_models();
				},
			},
			render_input: true,
		});
	}

	render_models() {
		const $body = this.page.main.find(".ai-models-body").empty();
		let models = this.models || [];
		if (this.active_provider) {
			models = models.filter((model) => model.provider === this.active_provider);
		}

		if (!models.length) {
			$body.html(`
				<div class="ai-ops-empty text-muted">
					${__("No models registered yet. Start your runtime and click Discover Models.")}
				</div>
			`);
			return;
		}

		const grouped = {};
		models.forEach((model) => {
			(grouped[model.model_type] = grouped[model.model_type] || []).push(model);
		});

		Object.keys(grouped)
			.sort()
			.forEach((type) => {
				const $section = $(`
					<div class="ai-model-group">
						<h5>${type} <span class="text-muted small">(${grouped[type].length})</span></h5>
						<div class="ai-model-cards"></div>
					</div>
				`);
				const $cards = $section.find(".ai-model-cards");
				grouped[type].forEach((model) => $cards.append(this.model_card(model)));
				$body.append($section);
			});
	}

	model_card(model) {
		const me = this;
		const size = model.size_bytes
			? (model.size_bytes / 1024 / 1024 / 1024).toFixed(1) + " GB"
			: "";

		const $card = $(`
			<div class="ai-model-card ${model.enabled ? "" : "is-disabled"}">
				<div class="ai-model-card-head">
					<div>
						<a href="/app/ai-model/${encodeURIComponent(model.name)}" class="ai-model-name">
							${frappe.utils.escape_html(model.model_label)}
						</a>
						${model.is_default ? `<span class="ai-pill">${__("Default")}</span>` : ""}
					</div>
					<span class="indicator-pill ${this.status_color(model.status)}">${model.status}</span>
				</div>
				<div class="text-muted small ai-model-runtime">
					<code>${frappe.utils.escape_html(model.model_name)}</code> · ${frappe.utils.escape_html(
			model.provider
		)}
				</div>
				<div class="ai-model-specs">
					${model.parameter_size ? `<span>${model.parameter_size}</span>` : ""}
					${model.quantization ? `<span>${model.quantization}</span>` : ""}
					${size ? `<span>${size}</span>` : ""}
					${model.context_window ? `<span>${this.compact(model.context_window)} ctx</span>` : ""}
					${model.embedding_dimensions ? `<span>${model.embedding_dimensions} dim</span>` : ""}
				</div>
				<div class="ai-model-stats text-muted small">
					${this.compact(model.total_requests || 0)} ${__("requests")}
					${model.average_latency_ms ? ` · ${Math.round(model.average_latency_ms)} ms ${__("avg")}` : ""}
				</div>
				<div class="ai-model-actions">
					<button class="btn btn-xs btn-default ai-model-test">${__("Test")}</button>
					<button class="btn btn-xs btn-default ai-model-default">${__("Set Default")}</button>
					<button class="btn btn-xs btn-default ai-model-toggle">
						${model.enabled ? __("Disable") : __("Enable")}
					</button>
				</div>
			</div>
		`);

		$card.find(".ai-model-test").on("click", async function () {
			const $button = $(this).prop("disabled", true).text(__("Testing..."));
			try {
				const result = await frappe.xcall("ai_fr_hg.api.admin.test_model", {
					model: model.name,
				});
				const failed = result && result.status && result.status !== "OK";
				frappe.msgprint({
					title: __("Model Test: {0}", [model.model_label]),
					indicator: failed ? "orange" : "green",
					message: failed
						? `<p>${frappe.utils
								.escape_html(
									result.response ||
										result.error ||
										__("The model could not start. Try qwen2.5:0.5b.")
								)
								.replace(/\n/g, "<br>")}</p>`
						: result.response
						? `<p><b>${__("Response")}:</b> ${frappe.utils.escape_html(
								result.response
						  )}</p>
						   <p class="text-muted">${result.duration_ms} ms · ${result.total_tokens} ${__("tokens")} ·
						   ${result.tokens_per_second} ${__("tok/s")}</p>`
						: __("Embeddings returned {0} dimensions.", [result.dimensions]),
				});
			} catch (error) {
				frappe.msgprint({
					title: __("Model Test Failed"),
					indicator: "red",
					message: rpc_error_message(
						error,
						__("The model could not start. Try qwen2.5:0.5b on this machine.")
					),
				});
			} finally {
				$button.prop("disabled", false).text(__("Test"));
			}
		});

		$card.find(".ai-model-default").on("click", async () => {
			await frappe.db.set_value("AI Model", model.name, "is_default", 1);
			frappe.show_alert({ message: __("Default updated"), indicator: "green" });
			this.refresh();
		});

		$card.find(".ai-model-toggle").on("click", async () => {
			await frappe.db.set_value("AI Model", model.name, "enabled", model.enabled ? 0 : 1);
			this.refresh();
		});

		return $card;
	}

	render_suggested() {
		const $grid = this.page.main.find(".ai-suggested-grid").empty();
		SUGGESTED_MODELS.forEach((suggestion) => {
			const $card = $(`
				<div class="ai-suggested-card">
					<div>
						<code>${suggestion.name}</code>
						<span class="ai-pill">${suggestion.type}</span>
					</div>
					<div class="text-muted small">${suggestion.note}</div>
					<button class="btn btn-xs btn-default ai-pull-suggested">${__("Install")}</button>
				</div>
			`);
			$card.find(".ai-pull-suggested").on("click", () => this.install(suggestion.name));
			$grid.append($card);
		});
	}

	async discover() {
		const providers = (this.providers || []).map((provider) => provider.name);
		if (!providers.length) {
			frappe.msgprint(__("Create an AI Provider first."));
			return;
		}

		frappe.prompt(
			[
				{
					fieldtype: "Select",
					fieldname: "provider",
					label: __("Provider"),
					options: providers,
					default: this.active_provider || providers[0],
					reqd: 1,
				},
			],
			async (values) => {
				frappe.show_alert({ message: __("Discovering models..."), indicator: "blue" });
				try {
					const result = await frappe.xcall(
						"ai_fr_hg.api.admin.discover_models",
						values
					);
					frappe.msgprint({
						title: __("Discovery Complete"),
						indicator: "green",
						message: `
							<p>${__("Found {0} model(s) on {1}.", [result.discovered, values.provider])}</p>
							${result.created.length ? `<p><b>${__("Registered")}:</b> ${result.created.join(", ")}</p>` : ""}
							${
								result.missing.length
									? `<p class="text-muted"><b>${__(
											"No longer present"
									  )}:</b> ${result.missing.join(", ")}</p>`
									: ""
							}
						`,
					});
					this.refresh();
				} catch (error) {
					frappe.msgprint({
						title: __("Discovery Failed"),
						indicator: "red",
						message: rpc_error_message(error, __("Discovery failed.")),
					});
				}
			},
			__("Discover Models"),
			__("Discover")
		);
	}

	install(prefill) {
		const providers = (this.providers || [])
			.filter((provider) => provider.provider_type === "Ollama")
			.map((provider) => provider.name);

		if (!providers.length) {
			frappe.msgprint(
				__(
					"Only Ollama providers support installing models from the Desk. Use your runtime's own tooling."
				)
			);
			return;
		}

		frappe.prompt(
			[
				{
					fieldtype: "Select",
					fieldname: "provider",
					label: __("Provider"),
					options: providers,
					default: providers[0],
					reqd: 1,
				},
				{
					fieldtype: "Data",
					fieldname: "model_name",
					label: __("Model Name"),
					default: prefill || "",
					reqd: 1,
					description: __("For example llama3.1:8b or nomic-embed-text"),
				},
			],
			async (values) => {
				try {
					await frappe.xcall("ai_fr_hg.api.admin.pull_model", values);
					frappe.show_alert({
						message: __("Downloading {0}. This can take several minutes.", [
							values.model_name,
						]),
						indicator: "blue",
					});

					// Prompt callback can fire more than once; always replace the
					// listener instead of stacking another on each install.
					frappe.realtime.off("ai_model_pulled");
					frappe.realtime.on("ai_model_pulled", (data) => {
						if (data.model !== values.model_name || data.provider !== values.provider)
							return;
						frappe.realtime.off("ai_model_pulled");
						frappe.show_alert({
							message:
								data.status === "Success"
									? __("{0} installed.", [data.model])
									: __("Install failed: {0}", [
											data.error || __("Unknown error"),
									  ]),
							indicator: data.status === "Success" ? "green" : "red",
						});
						this.refresh();
					});
				} catch (error) {
					frappe.msgprint({
						title: __("Install Failed"),
						indicator: "red",
						message: rpc_error_message(
							error,
							__("Could not queue model installation.")
						),
					});
				}
			},
			__("Install Model"),
			__("Install")
		);
	}

	status_color(status) {
		return (
			{ Available: "green", Unknown: "gray", Missing: "red", Error: "red" }[status] || "gray"
		);
	}

	compact(value) {
		const number = Number(value || 0);
		if (number >= 1e6) return (number / 1e6).toFixed(1) + "M";
		if (number >= 1e3) return (number / 1e3).toFixed(0) + "K";
		return String(number);
	}
}
