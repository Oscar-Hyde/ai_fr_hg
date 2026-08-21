// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

const STEP_CONFIG_FIELDS = {
	Classify: [
		{
			fieldtype: "Small Text",
			fieldname: "categories",
			reqd: 1,
			label: __("Categories"),
			description: __("One category per line."),
		},
		{ fieldtype: "Small Text", fieldname: "instructions", label: __("Instructions") },
	],
	Summarize: [
		{ fieldtype: "Int", fieldname: "max_words", label: __("Max words"), default: 200 },
		{ fieldtype: "Small Text", fieldname: "instructions", label: __("Instructions") },
	],
	Chunk: [
		{ fieldtype: "Int", fieldname: "chunk_size", label: __("Chunk size"), default: 1200 },
		{ fieldtype: "Int", fieldname: "chunk_overlap", label: __("Overlap"), default: 150 },
	],
	Compare: [
		{ fieldtype: "Data", fieldname: "document_a", label: __("Context key A"), default: "document_a" },
		{ fieldtype: "Data", fieldname: "document_b", label: __("Context key B"), default: "document_b" },
	],
	Translate: [
		{
			fieldtype: "Select",
			fieldname: "target_language",
			label: __("Target language"),
			options: "ar\nen\nhe",
			reqd: 1,
		},
		{
			fieldtype: "Select",
			fieldname: "tone",
			label: __("Register"),
			options: "Neutral\nFormal\nInformal\nTechnical\nLegal",
			default: "Neutral",
		},
		{ fieldtype: "Select", fieldname: "return", label: __("Return"), options: "text\nobject", default: "text" },
	],
	Tool: [{ fieldtype: "Code", fieldname: "arguments", label: __("Arguments"), options: "JSON", default: "{}" }],
};

function parseConfig(raw) {
	if (!raw) return {};
	try {
		const value = JSON.parse(raw);
		return value && typeof value === "object" ? value : {};
	} catch (error) {
		return {};
	}
}

function openStepConfig(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const fields = STEP_CONFIG_FIELDS[row.step_type];
	if (!fields) {
		frappe.msgprint(__("This step type has no extra configuration. Use the row fields."));
		return;
	}
	const current = parseConfig(row.config);
	const dialog = new frappe.ui.Dialog({
		title: __("Configure {0}", [row.step_name || row.step_type]),
		fields: fields.map((field) => {
			const copy = { ...field };
			if (field.fieldname === "categories" && Array.isArray(current.categories)) {
				copy.default = current.categories.join("\n");
			} else if (current[field.fieldname] !== undefined) {
				copy.default =
					field.fieldtype === "Code" && typeof current[field.fieldname] !== "string"
						? JSON.stringify(current[field.fieldname], null, 2)
						: current[field.fieldname];
			}
			return copy;
		}),
		primary_action_label: __("Save"),
		primary_action(values) {
			const config = { ...current };
			if (row.step_type === "Classify") {
				config.categories = (values.categories || "")
					.split(/\n|,/)
					.map((item) => item.trim())
					.filter(Boolean);
				config.instructions = values.instructions || "";
			} else if (row.step_type === "Tool") {
				try {
					config.arguments = values.arguments ? JSON.parse(values.arguments) : {};
				} catch (error) {
					frappe.throw(__("Arguments must be valid JSON."));
				}
			} else {
				Object.assign(config, values);
			}
			frappe.model.set_value(cdt, cdn, "config", JSON.stringify(config, null, 2));
			dialog.hide();
		},
	});
	dialog.show();
}

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

		if (frm.doc.next_run_on) {
			frm.dashboard.add_indicator(__("Next run {0}", [frm.doc.next_run_on]), "blue");
		}
		if (frm.doc.run_count) {
			const rate = Math.round((frm.doc.success_count / frm.doc.run_count) * 100);
			frm.dashboard.add_indicator(
				__("{0}% success ({1} runs)", [rate, frm.doc.run_count]),
				rate >= 90 ? "green" : rate >= 60 ? "orange" : "red"
			);
		}

		const steps = (frm.doc.steps || []).map(
			(step) =>
				`<div class="mb-1"><b>${frappe.utils.escape_html(step.step_name)}</b>
				<span class="text-muted"> · ${frappe.utils.escape_html(step.step_type)}
				${step.input_field ? " ← " + frappe.utils.escape_html(step.input_field) : ""}
				${step.output_field ? " → " + frappe.utils.escape_html(step.output_field) : ""}</span></div>`
		);
		if (steps.length) {
			frm.dashboard.add_section(`<h6>${__("Step graph")}</h6>${steps.join("")}`);
		}
		if (frm.fields_dict.steps && frm.fields_dict.steps.grid) {
			frm.fields_dict.steps.grid.add_custom_button(__("Configure selected step"), () => {
				const selected = frm.fields_dict.steps.grid.get_selected_children()[0];
				if (!selected) {
					frappe.msgprint(__("Select a step row first."));
					return;
				}
				openStepConfig(frm, selected.doctype, selected.name);
			});
		}
	},
});

frappe.ui.form.on("AI Pipeline Step", {
	step_type(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const templates = {
			Classify: '{\n  "categories": ["Invoice", "Contract", "Report"]\n}',
			Summarize: '{\n  "max_words": 200,\n  "instructions": ""\n}',
			Chunk: '{\n  "chunk_size": 1200,\n  "chunk_overlap": 150\n}',
			Compare: '{\n  "document_a": "document_a",\n  "document_b": "document_b"\n}',
			Translate: '{\n  "target_language": "ar",\n  "tone": "Neutral",\n  "return": "text"\n}',
			Tool: '{\n  "arguments": {}\n}',
		};
		if (templates[row.step_type] && !row.config) {
			frappe.model.set_value(cdt, cdn, "config", templates[row.step_type]);
		}
	},
	configure_step(frm, cdt, cdn) {
		openStepConfig(frm, cdt, cdn);
	},
});
