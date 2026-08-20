// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Translation list. Surfaces the review state at a glance: a translation
 * that needs a human is worth more than one that merely finished.
 */
const LANGUAGE_LABELS = { ar: __("Arabic"), en: __("English"), he: __("Hebrew") };

frappe.listview_settings["AI Translation"] = {
	add_fields: [
		"status",
		"source_language",
		"target_language",
		"quality_score",
		"flagged_segments",
		"segment_count",
	],

	get_indicator(doc) {
		if (doc.status === "Needs Review") {
			return [
				__("Needs Review ({0})", [doc.flagged_segments || 0]),
				"orange",
				"status,=,Needs Review",
			];
		}
		const color = frappe.ai?.status_color ? frappe.ai.status_color(doc.status) : "blue";
		return [__(doc.status), color, "status,=," + doc.status];
	},

	formatters: {
		target_language(value) {
			return LANGUAGE_LABELS[value] || value;
		},
		quality_score(value) {
			if (!value) return "";
			const color = value >= 90 ? "green" : value >= 70 ? "blue" : "orange";
			return `<span class="indicator-pill ${color}">${Math.round(value)}%</span>`;
		},
	},

	onload(listview) {
		listview.page.add_inner_button(__("Glossaries"), () =>
			frappe.set_route("List", "AI Translation Glossary")
		);
		listview.page.add_inner_button(__("Needs Review"), () =>
			listview.filter_area.add([["AI Translation", "status", "=", "Needs Review", false]])
		);
	},
};
