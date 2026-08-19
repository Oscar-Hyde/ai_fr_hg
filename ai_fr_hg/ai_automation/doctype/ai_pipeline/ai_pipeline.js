// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Pipeline", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Run Now"), () => {
			frappe.prompt(
				{
					fieldtype: "Code",
					fieldname: "input_data",
					label: __("Input"),
					options: "JSON",
					default: '{\n  "content": ""\n}',
					description: __("Keys here become the pipeline's starting context."),
				},
				async (values) => {
					const result = await frm.call("run_now", { input_data: values.input_data });
					frappe.show_alert({
						message: __("Run {0} started.", [result.message.run]),
						indicator: "blue",
					});
					frappe.set_route("Form", "AI Pipeline Run", result.message.run);
				},
				__("Run Pipeline"),
				__("Run")
			);
		}).addClass("btn-primary");

		frm.add_custom_button(
			__("Runs"),
			() => {
				frappe.set_route("List", "AI Pipeline Run", { pipeline: frm.doc.name });
			},
			__("View")
		);

		if (frm.doc.run_count) {
			const rate = Math.round((frm.doc.success_count / frm.doc.run_count) * 100);
			frm.dashboard.add_indicator(
				__("{0}% success ({1} runs)", [rate, frm.doc.run_count]),
				rate >= 90 ? "green" : rate >= 60 ? "orange" : "red"
			);
		}
	},
});

frappe.ui.form.on("AI Pipeline Step", {
	step_type(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		// Seed a helpful configuration skeleton per step type.
		const templates = {
			Classify: '{\n  "categories": ["Invoice", "Contract", "Report"]\n}',
			Summarize: '{\n  "max_words": 200,\n  "instructions": ""\n}',
			Chunk: '{\n  "chunk_size": 1200,\n  "chunk_overlap": 150\n}',
			Compare: '{\n  "document_a": "document_a",\n  "document_b": "document_b"\n}',
			Translate:
				'{\n  "target_language": "ar",\n  "tone": "Neutral",\n  "return": "text"\n}',
			Tool: '{\n  "arguments": {}\n}',
		};
		if (templates[row.step_type] && !row.config) {
			frappe.model.set_value(cdt, cdn, "config", templates[row.step_type]);
		}
	},
});
