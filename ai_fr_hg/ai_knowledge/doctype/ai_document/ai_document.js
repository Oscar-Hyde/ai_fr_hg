// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
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

		// Folder organization actions — always wired to canonical service
		frm.add_custom_button(__("Move to Folder…"), async () => {
			if (!frm.doc.source_file) {
				frappe.msgprint(__("This document has no source file to move."));
				return;
			}
			const target = await prompt_for_folder({
				default_folder: frm.doc.folder || "Home",
				title: __("Select Destination Folder"),
			});
			if (!target) return;
			const file_name = await frappe.db.get_value("File", { file_url: frm.doc.source_file }, "name");
			const real = file_name.message?.name || file_name?.name || file_name;
			if (!real) {
				frappe.msgprint(__("File record not found for {0}", [frm.doc.source_file]));
				return;
			}
			try {
				await frappe.xcall("ai_fr_hg.api.folders.move_file", { file_name: real, target_folder: target });
				// Persist provenance on AI Document
				await frappe.db.set_value("AI Document", frm.doc.name, { folder: target, source_folder: target });
				frappe.show_alert({ message: __("Moved to {0}", [target]), indicator: "green" });
				frm.reload_doc();
			} catch (e) {
				frappe.msgprint({ title: __("Move failed"), message: e.message, indicator: "red" });
			}
		}, __("Folder"));

		frm.add_custom_button(__("Open Files"), () => {
			frappe.set_route("List", "File", ...(frm.doc.folder || "Home").split("/"));
		}, __("Folder"));

		frm.add_custom_button(__("Copy File To…"), async () => {
			if (!frm.doc.source_file) {
				frappe.msgprint(__("No source file to copy."));
				return;
			}
			const target = await prompt_for_folder({
				default_folder: frm.doc.folder || "Home",
				title: __("Select Destination Folder"),
			});
			if (!target) return;
			const file_name = await frappe.db.get_value("File", { file_url: frm.doc.source_file }, "name");
			const real = file_name.message?.name || file_name?.name || file_name;
			try {
				await frappe.xcall("ai_fr_hg.api.folders.copy_file", { file_name: real, target_folder: target });
				frappe.show_alert({ message: __("File copied to {0}", [target]), indicator: "green" });
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
