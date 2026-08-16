// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Model"] = {
	add_fields: ["status", "enabled", "model_type", "is_default", "total_requests"],

	get_indicator(doc) {
		if (!doc.enabled) return [__("Disabled"), "gray", "enabled,=,0"];
		return [__(doc.status), frappe.ai.status_color(doc.status), "status,=," + doc.status];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Model Manager"), () =>
			frappe.set_route("ai-model-manager")
		);
	},
};
