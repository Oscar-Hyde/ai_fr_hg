// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Agent", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Start Conversation"), async () => {
			const result = await frm.call("start_conversation");
			frappe.set_route("ai-assistant");
			frappe.show_alert({
				message: __("Conversation {0} created.", [result.message]),
				indicator: "green",
			});
		}).addClass("btn-primary");

		frm.add_custom_button(__("Quick Test"), () => {
			frappe.prompt(
				{
					fieldtype: "Small Text",
					fieldname: "prompt",
					label: __("Prompt"),
					reqd: 1,
					default: __("Introduce yourself in one sentence."),
				},
				async (values) => {
					frappe.dom.freeze(__("Running..."));
					try {
						const result = await frm.call("test_agent", { prompt: values.prompt });
						frappe.dom.unfreeze();
						const data = result.message;
						frappe.msgprint({
							title: __("Agent Response"),
							wide: true,
							message: `
								<div>${frappe.markdown(data.answer || "")}</div>
								<hr>
								<p class="text-muted small">
									${data.model} · ${data.total_tokens} ${__("tokens")} ·
									${(data.duration_ms / 1000).toFixed(1)}s ·
									${(data.citations || []).length} ${__("sources")}
								</p>`,
						});
					} catch (error) {
						frappe.dom.unfreeze();
					}
				},
				__("Test Agent"),
				__("Run")
			);
		});

		frm.add_custom_button(
			__("Conversations"),
			() => {
				frappe.set_route("List", "AI Conversation", { agent: frm.doc.name });
			},
			__("View")
		);

		if (frm.doc.conversation_count) {
			frm.dashboard.add_indicator(
				__("{0} conversations", [frm.doc.conversation_count]),
				"blue"
			);
			frm.dashboard.add_indicator(__("{0} messages", [frm.doc.message_count]), "grey");
		}
	},

	use_knowledge(frm) {
		if (!frm.doc.use_knowledge) frm.set_value("strict_grounding", 0);
	},
});
