// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Message", {
	refresh(frm) {
		if (frm.is_new()) return;

		const role_colors = {
			User: "blue",
			Assistant: "green",
			System: "grey",
			Tool: "orange",
		};
		frm.page.set_indicator(__(frm.doc.role), role_colors[frm.doc.role] || "grey");

		if (frm.doc.conversation) {
			frm.add_custom_button(__("View Conversation"), () =>
				frappe.set_route("Form", "AI Conversation", frm.doc.conversation)
			);
		}

		if (frm.doc.learned_context) {
			try {
				const ctx = JSON.parse(frm.doc.learned_context);
				const lines = [];
				if (ctx.memories?.length) {
					lines.push(`<b>${__("Recalled Memories")}:</b> ${ctx.memories.join(", ")}`);
				}
				if (ctx.skills?.length) {
					lines.push(`<b>${__("Applied Skills")}:</b> ${ctx.skills.join(", ")}`);
				}
				if (lines.length) {
					frm.dashboard.set_headline(lines.join("<br>"));
				}
			} catch (_e) {
				// learned_context is not valid JSON; skip.
			}
		}

		if (frm.doc.feedback) {
			frm.set_df_property("feedback", "hidden", 0);
			frm.set_df_property("feedback_reason", "hidden", 0);
		}

		frm.disable_save();
	},
});
