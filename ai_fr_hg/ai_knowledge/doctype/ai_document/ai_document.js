// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Document form. Tree View lives in `ai_document_tree.js` on this DocType;
 * list actions live in `ai_document_list.js`. Folder mutations go through
 * `ai_fr_hg.api.document_tree` so the form never writes provenance itself.
 *
 * App bundles can be refreshed independently from a DocType script.  Do not
 * let that normal asset-loading window turn a native form/list action into an
 * uncaught JavaScript error.
 */
async function prompt_for_folder(options) {
	const picker = frappe.ai?.folder?.prompt_for_folder;
	if (picker) return picker(options);
	frappe.msgprint(__("The folder selector is still loading. Reload Desk and try again."));
	return null;
}

/**
 * Translation is a first-class document action: pick a target language (Arabic,
 * English or Hebrew), an optional glossary and register, and the platform
 * translates the extracted text on a background worker into a reviewable
 * `AI Translation` record.
 */
const TRANSLATION_LANGUAGES = [
	{ value: "ar", label: __("Arabic") },
	{ value: "en", label: __("English") },
	{ value: "he", label: __("Hebrew") },
];

async function open_translation_dialog(frm) {
	let languages = TRANSLATION_LANGUAGES;
	try {
		const info = await frappe.xcall("ai_fr_hg.api.translation.get_languages");
		if (!info.enabled) {
			frappe.msgprint(__("Translation is disabled in AI Platform Settings."));
			return;
		}
		if (info.languages?.length) {
			languages = info.languages.map((item) => ({
				value: item.code,
				label: `${item.name} (${item.endonym})`,
			}));
		}
	} catch (error) {
		// Fall back to the built-in list; the endpoint is only used for labels.
	}

	const detected = (frm.doc.language || "").split(",")[0];
	const dialog = new frappe.ui.Dialog({
		title: __("Translate {0}", [frm.doc.title]),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "target_language",
				label: __("Translate Into"),
				options: languages,
				reqd: 1,
				default: languages.find((item) => item.value !== detected)?.value,
			},
			{
				fieldtype: "Select",
				fieldname: "source_language",
				label: __("Source Language"),
				options: [{ value: "", label: __("Detect automatically") }, ...languages],
				default: "",
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Select",
				fieldname: "tone",
				label: __("Register"),
				options: ["Neutral", "Formal", "Informal", "Technical", "Legal"],
				default: "Neutral",
			},
			{
				fieldtype: "Link",
				fieldname: "glossary",
				label: __("Glossary"),
				options: "AI Translation Glossary",
				get_query: () => ({ filters: { enabled: 1 } }),
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Data",
				fieldname: "domain",
				label: __("Domain"),
				description: __("Optional, for example: construction contracts."),
			},
			{
				fieldtype: "Check",
				fieldname: "index_output",
				label: __("Index the translation as its own document"),
				default: 0,
			},
		],
		primary_action_label: __("Translate"),
		primary_action: async (values) => {
			dialog.hide();
			try {
				const result = await frm.call("translate", { ...values, background: true });
				frappe.show_alert({
					message: __("Translation queued"),
					indicator: "blue",
				});
				if (result.message?.translation) {
					frappe.set_route("Form", "AI Translation", result.message.translation);
				}
			} catch (error) {
				// frappe already surfaced the server message.
			}
		},
	});
	dialog.show();
}

frappe.ui.form.on("AI Document", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.status, frappe.ai.status_color(frm.doc.status));

		// The source folder is a native Link field and a standard list filter.
		// Deep navigation and breadcrumbs are provided by Frappe's FileView.
		if (frm.doc.folder) {
			frm.dashboard.add_indicator(__("Folder: {0}", [frm.doc.folder]), "blue");
		}

		if (["Draft", "Failed", "Queued"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Process Now"), async () => {
				await frm.call("process");
				frappe.show_alert({ message: __("Queued for processing"), indicator: "blue" });
				frm.reload_doc();
			}).addClass("btn-primary");
		}

		// Folder organization actions use the same server-authoritative facade as
		// Tree View; the form never resolves Files or writes provenance itself.
		frm.add_custom_button(__("Move to Folder…"), async () => {
			const target = await prompt_for_folder({
				default_folder: frm.doc.folder || "Home",
				title: __("Select Destination Folder"),
			});
			if (!target) return;
			try {
				await frappe.xcall("ai_fr_hg.api.document_tree.move_node", {
					node: `document::${frm.doc.name}`,
					target_folder: target,
					expected_modified: frm.doc.modified,
				});
				frappe.show_alert({ message: __("Moved to {0}", [target]), indicator: "green" });
				frm.reload_doc();
			} catch (e) {
				frappe.msgprint({ title: __("Move failed"), message: e.message, indicator: "red" });
			}
		}, __("Folder"));

		frm.add_custom_button(__("Open Document Tree"), () => {
			frappe.set_route("Tree", "AI Document");
		}, __("Folder"));

		frm.add_custom_button(__("Copy Document To…"), async () => {
			const target = await prompt_for_folder({
				default_folder: frm.doc.folder || "Home",
				title: __("Select Destination Folder"),
			});
			if (!target) return;
			try {
				const copy = await frappe.xcall("ai_fr_hg.api.document_tree.copy_node", {
					node: `document::${frm.doc.name}`,
					target_folder: target,
					expected_modified: frm.doc.modified,
				});
				frappe.show_alert({ message: __("Document copied to {0}", [target]), indicator: "green" });
				frappe.set_route("Form", "AI Document", copy.name);
			} catch (e) {
				frappe.msgprint({ title: __("Copy failed"), message: e.message, indicator: "red" });
			}
		}, __("Folder"));

		if (frm.doc.status === "Indexed") {
			frm.add_custom_button(__("Summarise"), async () => {
				frappe.dom.freeze(__("Summarising..."));
				try {
					await frm.call("generate_summary");
					frappe.dom.unfreeze();
					frm.reload_doc();
				} catch (error) {
					frappe.dom.unfreeze();
				}
			}).addClass("btn-primary");

			frm.add_custom_button(__("Ask About This"), () => {
				frappe.prompt(
					{
						fieldtype: "Small Text",
						fieldname: "question",
						label: __("Question"),
						reqd: 1,
					},
					(values) => {
						frappe.ai.ask(values.question, {
							knowledge_bases: [frm.doc.knowledge_base],
							documents: [frm.doc.name],
						});
					},
					__("Ask about {0}", [frm.doc.title]),
					__("Ask")
				);
			});

			frm.add_custom_button(
				__("Extract Data"),
				() => {
					frappe.prompt(
						{
							fieldtype: "Link",
							fieldname: "schema",
							label: __("Extraction Schema"),
							options: "AI Extraction Schema",
							reqd: 1,
							default: frm.doc.extraction_schema,
						},
						async (values) => {
							frappe.dom.freeze(__("Extracting..."));
							try {
								await frm.call("run_extraction", { schema: values.schema });
								frappe.dom.unfreeze();
								frm.reload_doc();
							} catch (error) {
								frappe.dom.unfreeze();
							}
						},
						__("Extract Structured Data"),
						__("Extract")
					);
				},
				__("Intelligence")
			);

			frm.add_custom_button(
				__("Compare With..."),
				() => {
					frappe.prompt(
						{
							fieldtype: "Link",
							fieldname: "other",
							label: __("Compare with"),
							options: "AI Document",
							reqd: 1,
						},
						async (values) => {
							frappe.dom.freeze(__("Comparing..."));
							try {
								const result = await frappe.xcall(
									"ai_fr_hg.api.knowledge.compare",
									{
										document_a: frm.doc.name,
										document_b: values.other,
									}
								);
								frappe.dom.unfreeze();
								frappe.msgprint({
									title: __("Comparison"),
									wide: true,
									message: frappe.markdown(result.comparison),
								});
							} catch (error) {
								frappe.dom.unfreeze();
							}
						},
						__("Compare Documents"),
						__("Compare")
					);
				},
				__("Intelligence")
			);

			frm.add_custom_button(
				__("Translate…"),
				() => open_translation_dialog(frm),
				__("Intelligence")
			);

			frm.add_custom_button(
				__("View Translations"),
				() =>
					frappe.set_route("List", "AI Translation", {
						source_document: frm.doc.name,
					}),
				__("View")
			);

			frm.add_custom_button(
				__("View Chunks"),
				async () => {
					const chunks = await frappe.xcall(
						"ai_fr_hg.api.knowledge.get_document_chunks",
						{
							document: frm.doc.name,
						}
					);
					frappe.msgprint({
						title: __("{0} Chunks", [chunks.length]),
						wide: true,
						message: chunks
							.map(
								(chunk) => `
						<div style="border-bottom:1px solid var(--border-color);padding:8px 0">
							<b>#${chunk.chunk_index}</b>
							${chunk.heading ? ` · ${frappe.utils.escape_html(chunk.heading)}` : ""}
							<span class="text-muted small">
								${chunk.character_count} ${__("chars")}
								${chunk.embedded_on ? " · " + __("embedded") : " · " + __("not embedded")}
							</span>
							<div class="text-muted small" style="margin-top:4px">
								${frappe.utils.escape_html(chunk.content.slice(0, 300))}...
							</div>
						</div>`
							)
							.join(""),
					});
				},
				__("View")
			);
		}

		frm.add_custom_button(
			__("Re-process"),
			() => {
				frappe.confirm(__("Discard existing chunks and process again?"), async () => {
					await frm.call("reprocess");
					frm.reload_doc();
				});
			},
			__("Actions")
		);

		if (frm.doc.status === "Failed" && frm.doc.error_message) {
			frm.dashboard.set_headline(
				`<span class="text-danger">${frappe.utils.escape_html(
					frm.doc.error_message
				)}</span>`
			);
		}

		if (frm.doc.chunk_count) {
			frm.dashboard.add_indicator(__("{0} chunks", [frm.doc.chunk_count]), "blue");
			frm.dashboard.add_indicator(
				__("{0} embedded", [frm.doc.embedded_chunk_count]),
				frm.doc.embedded_chunk_count === frm.doc.chunk_count ? "green" : "orange"
			);
		}

		// Folder-specific provenance indicator
		if (frm.doc.source_folder) {
			frm.dashboard.add_indicator(__("Provenance: {0}", [frm.doc.source_folder]), "gray");
		}
	},
});
