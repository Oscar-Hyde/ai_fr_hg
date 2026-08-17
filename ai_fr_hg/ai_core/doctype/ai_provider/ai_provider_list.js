// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Provider"] = {
	add_fields: ["status", "enabled", "provider_type", "latency_ms"],

	get_indicator(doc) {
		if (!doc.enabled) return [__("Disabled"), "gray", "enabled,=,0"];
		return [__(doc.status), frappe.ai.status_color(doc.status), "status,=," + doc.status];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Test All Providers"), async () => {
			frappe.dom.freeze(__("Testing..."));
			try {
				const results = await frappe.xcall("ai_fr_hg.api.admin.test_all_providers");
				frappe.dom.unfreeze();
				frappe.msgprint({
					title: __("Provider Status"),
					message: results
						.map((row) => `<p><b>${row.provider}</b>: ${row.status}</p>`)
						.join(""),
				});
				listview.refresh();
			} catch (error) {
				frappe.dom.unfreeze();
			}
		});

		listview.page.add_inner_button(__("Operations Dashboard"), () =>
			frappe.set_route("ai-operations")
		);
	},
};
