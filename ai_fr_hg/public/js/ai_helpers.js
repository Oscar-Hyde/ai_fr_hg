// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Shared client-side helpers, exposed as `frappe.ai`.
 */

frappe.provide("frappe.ai");

Object.assign(frappe.ai, {
	/** Colour for a status indicator pill. */
	status_color(status) {
		return (
			{
				Online: "green",
				Available: "green",
				Indexed: "green",
				Success: "green",
				Completed: "green",
				Degraded: "orange",
				Queued: "orange",
				Running: "blue",
				Extracting: "blue",
				Chunking: "blue",
				Embedding: "blue",
				Stale: "orange",
				"Pending Approval": "orange",
				Offline: "red",
				Missing: "red",
				Failed: "red",
				Error: "red",
				Rejected: "red",
				Unknown: "gray",
				Draft: "gray",
				Archived: "gray",
			}[status] || "gray"
		);
	},

	/** Compact number formatting, e.g. 12400 -> 12.4K */
	compact(value) {
		const number = Number(value || 0);
		if (number >= 1e9) return (number / 1e9).toFixed(1) + "B";
		if (number >= 1e6) return (number / 1e6).toFixed(1) + "M";
		if (number >= 1e3) return (number / 1e3).toFixed(1) + "K";
		return String(number);
	},

	/** Ask the platform a one-off question and show the answer in a dialog. */
	async ask(question, options = {}) {
		frappe.show_alert({ message: __("Thinking..."), indicator: "blue" });
		try {
			const response = await frappe.xcall("ai_fr_hg.api.knowledge.ask", {
				question,
				knowledge_bases: options.knowledge_bases || null,
				agent: options.agent || null,
				documents: options.documents || null,
			});

			const citations = (response.citations || [])
				.map(
					(cite, index) =>
						`<li><a href="/app/ai-document/${cite.document}">[${
							index + 1
						}] ${frappe.utils.escape_html(cite.document_title)}</a></li>`
				)
				.join("");

			frappe.msgprint({
				title: __("AI Answer"),
				wide: true,
				message: `
					<div>${frappe.markdown(response.answer || "")}</div>
					${citations ? `<hr><p class="text-muted small">${__("Sources")}</p><ul>${citations}</ul>` : ""}
				`,
			});
			return response;
		} catch (error) {
			frappe.msgprint({ title: __("AI Error"), indicator: "red", message: error.message });
		}
	},

	/**
	 * Add an "Ask AI" button to any form. Call from a custom script:
	 *
	 *   frappe.ui.form.on("Sales Order", {
	 *       refresh: (frm) => frappe.ai.add_form_button(frm),
	 *   });
	 */
	add_form_button(frm, label) {
		frm.add_custom_button(label || __("Ask AI"), () => {
			frappe.prompt(
				{
					fieldtype: "Small Text",
					fieldname: "question",
					label: __("Question"),
					reqd: 1,
					default: __("Summarise this {0}.", [__(frm.doctype)]),
				},
				(values) => {
					frappe.ai.ask(`${values.question}\n\nDocument: ${frm.doctype} ${frm.docname}`);
				},
				__("Ask AI"),
				__("Ask")
			);
		});
	},
});
