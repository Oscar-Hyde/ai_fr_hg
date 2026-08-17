// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Knowledge Candidate"] = {
	add_fields: [
		"title",
		"candidate_type",
		"status",
		"user",
		"testing_status",
		"conflict_count",
		"target_scope",
	],
	filters: [
		["status", "in", ["Draft", "Validated", "Conflict"]],
	],
	get_indicator(doc) {
		const colors = {
			Draft: "grey",
			Validated: "blue",
			Conflict: "orange",
			Approved: "green",
			Rejected: "red",
		};
		return [__(doc.status), colors[doc.status] || "grey"];
	},
	button: {
		show(doc) {
			return ["Draft", "Validated", "Conflict"].includes(doc.status);
		},
		get_label() {
			return __("Review");
		},
		get_description(doc) {
			return __("Review {0}", [doc.title || doc.name]);
		},
		action(doc) {
			frappe.set_route("Form", "AI Knowledge Candidate", doc.name);
		},
	},
	primary_action: {
		label: __("Teach AI"),
		action() {
			frappe.prompt(
				{
					fieldname: "content",
					fieldtype: "Long Text",
					label: __("What would you like to teach?"),
					reqd: 1,
				},
				async (values) => {
					const result = await frappe.xcall(
						"ai_fr_hg.api.learning.teach",
						{ content: values.content }
					);
					frappe.show_alert({
						message: __("Candidate created: {0}", [result.candidate]),
						indicator: "green",
					});
					frappe.set_route("Form", "AI Knowledge Candidate", result.candidate);
				},
				__("Teach the AI"),
				__("Teach")
			);
		},
	},
};