// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Knowledge Base", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.page.set_indicator(frm.doc.index_status, frappe.ai.status_color(frm.doc.index_status));

		frm.add_custom_button(__("Upload Document"), () => {
			new frappe.ui.FileUploader({
				folder: "Home/Attachments",
				async on_success(file) {
					await frappe.xcall("ai_fr_hg.api.knowledge.upload_document", {
						file_url: file.file_url,
						knowledge_base: frm.doc.name,
						title: file.file_name,
					});
					frappe.show_alert({
						message: __("Processing {0}...", [file.file_name]),
						indicator: "blue",
					});
				},
			});
		}).addClass("btn-primary");

		frm.add_custom_button(__("Add Text"), () => {
			frappe.prompt(
				[
					{ fieldtype: "Data", fieldname: "title", label: __("Title"), reqd: 1 },
					{ fieldtype: "Text Editor", fieldname: "text", label: __("Content"), reqd: 1 },
				],
				async (values) => {
					await frappe.xcall("ai_fr_hg.api.knowledge.add_text", {
						text: values.text,
						title: values.title,
						knowledge_base: frm.doc.name,
					});
					frappe.show_alert({ message: __("Queued for indexing"), indicator: "blue" });
				},
				__("Add Text to Knowledge Base"),
				__("Add")
			);
		});

		frm.add_custom_button(__("Search"), () => frappe.set_route("knowledge-explorer"));

		frm.add_custom_button(
			__("Re-index All"),
			() => {
				frappe.confirm(
					__("Re-process every document in this knowledge base? This may take a while."),
					async () => {
						const result = await frm.call("reindex");
						frappe.show_alert({
							message: __("{0} document(s) queued.", [result.message.queued]),
							indicator: "blue",
						});
					}
				);
			},
			__("Actions")
		);

		frm.add_custom_button(
			__("Refresh Statistics"),
			async () => {
				await frm.call("refresh_stats");
				frm.reload_doc();
			},
			__("Actions")
		);

		frm.add_custom_button(
			__("Export"),
			async () => {
				const result = await frappe.xcall("ai_fr_hg.api.admin.export_knowledge_base", {
					knowledge_base: frm.doc.name,
				});
				window.open(result.file_url);
			},
			__("Actions")
		);

		frm.add_custom_button(
			__("Documents"),
			() => {
				frappe.set_route("List", "AI Document", { knowledge_base: frm.doc.name });
			},
			__("View")
		);

		if (frm.doc.chunk_count) {
			const pct = Math.round((frm.doc.embedded_chunk_count / frm.doc.chunk_count) * 100);
			frm.dashboard.add_progress(__("Embedding Coverage"), [
				{
					title: __("{0}% embedded", [pct]),
					width: pct + "%",
					progress_class: pct === 100 ? "progress-bar-success" : "progress-bar-warning",
				},
			]);
		}

		if (!frm.doc.embedding_model) {
			frm.dashboard.set_headline(
				`<span class="text-warning">${__(
					"No embedding model is set, so semantic search is unavailable for this knowledge base."
				)}</span>`
			);
		}
	},
});
