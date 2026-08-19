// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

function normalize_similarity_threshold(value) {
	if (typeof frappe.ai?.normalize_similarity_threshold === "function") {
		return frappe.ai.normalize_similarity_threshold(value);
	}
	if (value === "" || value == null) return null;
	const score = Number(value);
	if (!Number.isFinite(score)) return null;
	if (score > 1 && score <= 100) return Number((score / 100).toFixed(6));
	if (score < 0 || score > 1) return null;
	return score;
}

frappe.ui.form.on("AI Platform Settings", {
	setup(frm) {
		frm.set_query("default_chat_model", () => ({
			filters: { enabled: 1, model_type: ["in", ["Chat", "Vision"]] },
		}));
		frm.set_query("default_embedding_model", () => ({
			filters: { enabled: 1, model_type: "Embedding" },
		}));
		frm.set_query("default_vision_model", () => ({
			filters: { enabled: 1, model_type: "Vision" },
		}));
	},

	refresh(frm) {
		frm.add_custom_button(__("Test All Providers"), async () => {
			frappe.dom.freeze(__("Testing providers..."));
			try {
				const result = await frappe.xcall("ai_fr_hg.api.admin.test_all_providers");
				frappe.dom.unfreeze();
				frappe.msgprint({
					title: __("Provider Status"),
					message: result
						.map(
							(row) =>
								`<p><b>${frappe.utils.escape_html(row.provider)}</b>: ${row.status}
								${row.error ? ` <span class="text-danger">${frappe.utils.escape_html(row.error)}</span>` : ""}</p>`
						)
						.join(""),
				});
			} catch (error) {
				frappe.dom.unfreeze();
			}
		}).addClass("btn-primary");

		frm.add_custom_button(__("Operations Dashboard"), () => frappe.set_route("ai-operations"));
		frm.add_custom_button(__("Model Manager"), () => frappe.set_route("ai-model-manager"));

		frm.trigger("show_readiness");
	},

	async show_readiness(frm) {
		const status = await frappe.xcall("ai_fr_hg.api.admin.get_system_status");
		frm.dashboard.clear_headline();

		if (status.ready) {
			frm.dashboard.set_headline(
				`<span class="text-success">${__(
					"Platform is fully configured and ready."
				)}</span>`
			);
			return;
		}

		const pending = status.checks.filter((check) => !check.status);
		frm.dashboard.set_headline(
			`<b>${__("Setup incomplete")}</b><ul style="margin:4px 0 0">` +
				pending
					.map(
						(check) =>
							`<li>${frappe.utils.escape_html(
								check.label
							)} - <span class="text-muted">${frappe.utils.escape_html(
								check.hint
							)}</span></li>`
					)
					.join("") +
				"</ul>"
		);
	},

	offline_mode(frm) {
		if (!frm.doc.offline_mode) {
			frappe.msgprint({
				title: __("Local Only Mode Disabled"),
				indicator: "orange",
				message: __(
					"Provider endpoints outside the local network will now be allowed. Prompts and document contents could leave this machine."
				),
			});
		}
	},

	similarity_threshold(frm) {
		const next = normalize_similarity_threshold(frm.doc.similarity_threshold);
		if (next !== null && next !== parseFloat(frm.doc.similarity_threshold)) {
			frm.set_value("similarity_threshold", next);
		}
	},

	validate(frm) {
		const threshold = normalize_similarity_threshold(frm.doc.similarity_threshold);
		if (
			frm.doc.similarity_threshold !== "" &&
			frm.doc.similarity_threshold != null &&
			threshold === null
		) {
			frappe.throw(
				__("Similarity Threshold must be between 0 and 1, or 1–100 as a percentage.")
			);
		}
		if (threshold !== null) {
			frm.doc.similarity_threshold = threshold;
		}
	},
});
