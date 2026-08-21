// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Prompt Template", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Render through the server's `preview` method rather than
		// re-implementing template rendering here. The previous version
		// concatenated the raw fields in the browser, so it showed the
		// unrendered source: variables were never substituted and any
		// server-side rendering rule (defaults, missing-variable handling)
		// was invisible. The operator was previewing something the model
		// would never receive.
		frm.add_custom_button(__("Preview Prompt"), async () => {
			let rendered;
			try {
				rendered = await frm.call("preview");
			} catch (error) {
				return; // Server already surfaced the message.
			}

			const result = rendered?.message || rendered || {};
			const section = (label, value) =>
				value
					? `<b>${__(label)}:</b><br>${frappe.utils.escape_html(value)}<br><br>`
					: "";

			const body = [
				section("System Prompt", result.system_prompt),
				section("User Prompt", result.user_prompt),
				section("Model", result.model),
				section("Output Format", result.output_format),
			].join("");

			frappe.msgprint(`<div class="small">${body}</div>`, __("Template Preview"));
		});
	},
});
