// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Provider", {
	refresh(frm) {
		frm.trigger("render_status");

		if (frm.is_new()) return;

		frm.add_custom_button(__("Test Connection"), async () => {
			frappe.dom.freeze(__("Contacting {0}...", [frm.doc.name]));
			try {
				const result = await frm.call("test_connection");
				frappe.dom.unfreeze();
				frappe.msgprint({
					title: __("Connection Test"),
					indicator: result.message.status === "Online" ? "green" : "red",
					message:
						result.message.status === "Online"
							? __("Online. {0} model(s) available, {1} ms latency.", [
									result.message.available_models,
									result.message.latency_ms,
							  ])
							: __("{0}: {1}", [result.message.status, result.message.error || ""]),
				});
				frm.reload_doc();
			} catch (error) {
				frappe.dom.unfreeze();
			}
		}).addClass("btn-primary");

		frm.add_custom_button(__("Discover Models"), async () => {
			frappe.dom.freeze(__("Reading model list..."));
			try {
				const result = await frm.call("discover_models");
				frappe.dom.unfreeze();
				const data = result.message;
				frappe.msgprint({
					title: __("Model Discovery"),
					indicator: "green",
					message: `
						<p>${__("Found {0} model(s).", [data.discovered])}</p>
						${data.created.length ? `<p><b>${__("Registered")}:</b> ${data.created.join(", ")}</p>` : ""}
						${
							data.updated.length
								? `<p class="text-muted"><b>${__(
										"Updated"
								  )}:</b> ${data.updated.join(", ")}</p>`
								: ""
						}
						${
							data.missing.length
								? `<p class="text-muted"><b>${__(
										"Missing"
								  )}:</b> ${data.missing.join(", ")}</p>`
								: ""
						}
					`,
				});
				frm.reload_doc();
			} catch (error) {
				frappe.dom.unfreeze();
			}
		});

		frm.add_custom_button(
			__("View Models"),
			() => {
				frappe.set_route("List", "AI Model", { provider: frm.doc.name });
			},
			__("View")
		);

		frm.add_custom_button(
			__("Health History"),
			() => {
				frappe.set_route("List", "AI Service Health Log", { provider: frm.doc.name });
			},
			__("View")
		);
	},

	render_status(frm) {
		frm.dashboard.clear_headline();
		if (frm.is_new()) return;

		const color = frappe.ai.status_color(frm.doc.status);
		frm.page.set_indicator(frm.doc.status, color);

		if (frm.doc.status === "Offline" && frm.doc.last_error) {
			frm.dashboard.set_headline(
				`<span class="text-danger">${frappe.utils.escape_html(frm.doc.last_error)}</span>`
			);
		} else if (frm.doc.status === "Online") {
			frm.dashboard.set_headline(
				__("{0} model(s) available · {1} ms", [
					frm.doc.available_model_count || 0,
					frm.doc.latency_ms || 0,
				])
			);
		}
	},

	provider_type(frm) {
		// Offer the conventional local port for the selected runtime.
		const defaults = {
			Ollama: "http://localhost:11434",
			"Llama.cpp": "http://localhost:8080",
			vLLM: "http://localhost:8000",
			"LM Studio": "http://localhost:1234/v1",
			"Text Generation WebUI": "http://localhost:5000",
			"OpenAI Compatible": "http://localhost:8000/v1",
		};
		if (defaults[frm.doc.provider_type] && !frm.doc.base_url) {
			frm.set_value("base_url", defaults[frm.doc.provider_type]);
		}
	},
});
