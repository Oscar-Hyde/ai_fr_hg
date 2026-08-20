// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.query_reports["Skill Summary"] = {
	filters: [
		{
			fieldname: "skill_type",
			label: __("Skill Type"),
			fieldtype: "Select",
			options: ["", "Procedural", "Formatting", "Workflow"],
		},
		{
			fieldname: "enabled",
			label: __("Enabled"),
			fieldtype: "Select",
			options: ["", 1, 0],
		},
		{
			fieldname: "scope",
			label: __("Scope"),
			fieldtype: "Select",
			options: ["", "Global", "User", "Role", "Agent"],
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "Enabled") {
			value = data.Enabled
				? `<span class="indicator green">${__("Yes")}</span>`
				: `<span class="indicator grey">${__("No")}</span>`;
		}
		return value;
	},
};
