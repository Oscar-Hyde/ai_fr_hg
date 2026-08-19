// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Skill"] = {
	add_fields: ["skill_name", "skill_type", "scope", "enabled", "usage_count", "version"],
	filters: [["enabled", "=", 1]],
	get_indicator(doc) {
		if (doc.enabled) {
			return [__("Enabled"), "green", "enabled,=,1"];
		}
		return [__("Disabled"), "grey", "enabled,=,0"];
	},
};
