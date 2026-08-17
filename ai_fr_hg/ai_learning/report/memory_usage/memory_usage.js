// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.query_reports["Memory Usage"] = {
	filters: [
		{
			fieldname: "memory_type",
			label: __("Memory Type"),
			fieldtype: "Select",
			options: [
				"",
				"Fact",
				"Preference",
				"Instruction",
				"Feedback",
			],
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Active", "Archived"],
		},
		{
			fieldname: "scope",
			label: __("Scope"),
			fieldtype: "Select",
			options: ["", "Global", "User", "Role", "Agent"],
		},
		{
			fieldname: "min_usage",
			label: __("Minimum Usage Count"),
			fieldtype: "Int",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "Status") {
			value = `<span class="indicator ${data.Status === "Active" ? "green" : "grey"}">${value}</span>`;
		}
		return value;
	},
};