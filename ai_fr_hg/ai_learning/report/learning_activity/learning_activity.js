// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.query_reports["Learning Activity"] = {
	filters: [
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"",
				"Draft",
				"Validated",
				"Conflict",
				"Approved",
				"Rejected",
			],
		},
		{
			fieldname: "candidate_type",
			label: __("Type"),
			fieldtype: "Select",
			options: [
				"",
				"Fact",
				"Preference",
				"Instruction",
				"Feedback",
				"Document",
			],
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "Status") {
			const colors = {
				Draft: "grey",
				Validated: "blue",
				Conflict: "orange",
				Approved: "green",
				Rejected: "red",
			};
			value = `<span class="indicator ${colors[data.Status] || "grey"}">${value}</span>`;
		}
		return value;
	},
};