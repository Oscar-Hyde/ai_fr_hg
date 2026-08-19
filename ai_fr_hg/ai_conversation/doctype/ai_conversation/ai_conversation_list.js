// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Conversation"] = {
	add_fields: ["title", "agent", "status", "user", "message_count", "creation"],
	filters: [["status", "=", "Active"]],
	get_indicator(doc) {
		const colors = {
			Active: "green",
			Archived: "grey",
		};
		return [__(doc.status), colors[doc.status] || "grey"];
	},
	primary_action: {
		label: __("New Conversation"),
		action() {
			frappe.set_route("ai-assistant");
		},
	},
};
