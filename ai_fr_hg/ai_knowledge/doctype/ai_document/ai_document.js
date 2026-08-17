// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Document", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.status, frappe.ai.status_color(frm.doc.status));

		if (["Draft", "Failed", "Queued"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Process Now"), async () => {
				await frm.call("process");
				frappe.show_alert({ message: __("Queued for processing"), indicator: "blue" });
				frm.reload_doc();
			}).addClass("btn-primary");
		}

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
						// Scope the answer to this document so the reply is
						// grounded in the record itself, not the whole KB.
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
	},
});
