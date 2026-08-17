// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Memory"] = {
	add_fields: [
		"content",
		"memory_type",
		"scope",
		"scope_value",
		"status",
		"usage_count",
		"helpful_count",
		"not_helpful_count",
	],
	filters: [["status", "=", "Active"]],
	get_indicator(doc) {
		if (doc.status === "Archived") {
			return [__("Archived"), "grey"];
		}
		return [__(doc.memory_type), "green"];
	},
	primary_action: {
		label: __("View Learning Dashboard"),
		action() {
			frappe.set_route("ai-learning");
		},
	},
};