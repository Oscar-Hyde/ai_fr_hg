// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Agent"] = {
	add_fields: [
		"agent_name",
		"enabled",
		"is_default",
		"model",
		"use_tools",
		"use_knowledge",
	],
	filters: [["enabled", "=", 1]],
	get_indicator(doc) {
		if (doc.enabled) {
			return [
				doc.is_default ? __("Default") : __("Enabled"),
				"green",
				"enabled,=,1",
			];
		}
		return [__("Disabled"), "grey", "enabled,=,0"];
	},
};