// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Model", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.status, frappe.ai.status_color(frm.doc.status));

		frm.add_custom_button(__("Test Model"), async () => {
			frappe.dom.freeze(__("Running a probe prompt..."));
			try {
				const result = await frm.call("test_model");
				frappe.dom.unfreeze();
				const data = result.message;
				frappe.msgprint({
					title: __("Model Test"),
					indicator: "green",
					message: data.response
						? `<p><b>${__("Response")}:</b> ${frappe.utils.escape_html(
								data.response
						  )}</p>
						   <p class="text-muted">${data.duration_ms} ms · ${data.total_tokens} ${__("tokens")} · ${
								data.tokens_per_second
						  } ${__("tok/s")}</p>`
						: __("Returned {0}-dimensional embeddings.", [data.dimensions]),
				});
				frm.reload_doc();
			} catch (error) {
				frappe.dom.unfreeze();
			}
		}).addClass("btn-primary");

		frm.add_custom_button(__("Refresh Metadata"), async () => {
			await frm.call("refresh_metadata");
			frm.reload_doc();
			frappe.show_alert({ message: __("Metadata refreshed"), indicator: "green" });
		});

		frm.add_custom_button(
			__("Execution Logs"),
			() => {
				frappe.set_route("List", "AI Execution Log", { model: frm.doc.name });
			},
			__("View")
		);

		if (frm.doc.total_requests) {
			frm.dashboard.add_indicator(
				__("{0} requests", [frappe.ai.compact(frm.doc.total_requests)]),
				"blue"
			);
			frm.dashboard.add_indicator(
				__("{0} tokens", [frappe.ai.compact(frm.doc.total_tokens)]),
				"purple"
			);
			frm.dashboard.add_indicator(
				__("{0} ms avg", [Math.round(frm.doc.average_latency_ms || 0)]),
				frm.doc.average_latency_ms > 10000 ? "orange" : "green"
			);
		}
	},

	model_type(frm) {
		frm.toggle_reqd("embedding_dimensions", false);
		if (frm.doc.model_type === "Vision") frm.set_value("supports_vision", 1);
	},
});
