// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Translation form.
 *
 * The review experience is the point of this form: a bilingual, direction-aware
 * side-by-side of every segment, with the flagged ones surfaced first and a
 * one-click re-run for each. All mutations go through whitelisted controller
 * methods so the form never writes derived translation state itself.
 */

const LANGUAGE_LABELS = { ar: __("Arabic"), en: __("English"), he: __("Hebrew") };
const RTL = new Set(["ar", "he"]);

function label_for(code) {
	return LANGUAGE_LABELS[code] || code || __("Auto detect");
}

function direction_for(code) {
	return RTL.has(code) ? "rtl" : "ltr";
}

function score_indicator(score) {
	if (score >= 90) return "green";
	if (score >= 70) return "blue";
	if (score > 0) return "orange";
	return "red";
}

function apply_direction(frm) {
	const target = frm.fields_dict.translated_text?.$input;
	if (target) target.attr("dir", direction_for(frm.doc.target_language));

	const source = frm.fields_dict.source_text?.$input;
	if (source) source.attr("dir", direction_for(frm.doc.source_language));
}

function segment_rows(frm, only_flagged) {
	const rows = (frm.doc.segments || [])
		.slice()
		.sort((a, b) => a.segment_index - b.segment_index);
	return only_flagged ? rows.filter((row) => ["Flagged", "Failed"].includes(row.status)) : rows;
}

function render_review(frm, only_flagged) {
	const rows = segment_rows(frm, only_flagged);
	if (!rows.length) {
		frappe.msgprint(only_flagged ? __("No segments are flagged.") : __("No segments yet."));
		return;
	}

	const source_dir = direction_for(frm.doc.source_language);
	const target_dir = direction_for(frm.doc.target_language);

	const dialog = new frappe.ui.Dialog({
		title: __("Review Translation"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "review" }],
		primary_action_label: __("Close"),
		primary_action: () => dialog.hide(),
	});

	const body = rows
		.map((row) => {
			const flagged = ["Flagged", "Failed"].includes(row.status);
			const issues = (row.issues || "")
				.split("\n")
				.filter(Boolean)
				.map(
					(issue) =>
						`<div class="text-danger small">• ${frappe.utils.escape_html(issue)}</div>`
				)
				.join("");
			return `
			<div class="ai-translation-segment" data-index="${row.segment_index}"
				style="border:1px solid var(--border-color);border-left:3px solid var(--${
					flagged ? "red" : "green"
				}-400);border-radius:6px;padding:10px;margin-bottom:10px">
				<div class="d-flex justify-content-between text-muted small" style="margin-bottom:6px">
					<span>#${row.segment_index} · ${frappe.utils.escape_html(row.kind || "paragraph")}${
				row.page_number ? ` · ${__("page")} ${row.page_number}` : ""
			}${row.reused ? ` · ${__("reused")}` : ""}</span>
					<span>${frappe.utils.escape_html(row.status || "")} · ${Math.round(row.quality_score || 0)}%</span>
				</div>
				<div class="row">
					<div class="col-sm-6" dir="${source_dir}" style="white-space:pre-wrap">${frappe.utils.escape_html(
				row.source_text || ""
			)}</div>
					<div class="col-sm-6" dir="${target_dir}" style="white-space:pre-wrap">${frappe.utils.escape_html(
				row.translated_text || ""
			)}</div>
				</div>
				${issues}
				<div style="margin-top:6px">
					<button class="btn btn-xs btn-default ai-retranslate" data-index="${row.segment_index}">
						${__("Re-translate segment")}
					</button>
				</div>
			</div>`;
		})
		.join("");

	dialog.fields_dict.review.$wrapper.html(
		`<div style="max-height:60vh;overflow:auto">${body}</div>`
	);

	dialog.$wrapper.on("click", ".ai-retranslate", async (event) => {
		const index = $(event.currentTarget).data("index");
		frappe.prompt(
			{
				fieldtype: "Small Text",
				fieldname: "instructions",
				label: __("Instruction for this segment (optional)"),
				description: __(
					"For example: keep the clause numbering, or use the formal register."
				),
			},
			async (values) => {
				frappe.dom.freeze(__("Re-translating..."));
				try {
					await frm.call("retranslate", {
						segment_index: index,
						instructions: values.instructions || "",
					});
					frappe.dom.unfreeze();
					dialog.hide();
					await frm.reload_doc();
					render_review(frm, only_flagged);
				} catch (error) {
					frappe.dom.unfreeze();
				}
			},
			__("Re-translate segment {0}", [index]),
			__("Re-translate")
		);
	});

	dialog.show();
}

frappe.ui.form.on("AI Translation", {
	refresh(frm) {
		apply_direction(frm);
		if (frm.is_new()) return;

		frm.page.set_indicator(
			frm.doc.status,
			frappe.ai?.status_color ? frappe.ai.status_color(frm.doc.status) : "blue"
		);

		if (frm.doc.quality_score) {
			frm.dashboard.add_indicator(
				__("Quality {0}%", [Math.round(frm.doc.quality_score)]),
				score_indicator(frm.doc.quality_score)
			);
		}
		if (frm.doc.segment_count) {
			frm.dashboard.add_indicator(__("{0} segments", [frm.doc.segment_count]), "blue");
		}
		if (frm.doc.flagged_segments) {
			frm.dashboard.add_indicator(__("{0} flagged", [frm.doc.flagged_segments]), "orange");
		}
		if (frm.doc.memory_hits) {
			frm.dashboard.add_indicator(__("{0} reused", [frm.doc.memory_hits]), "green");
		}
		frm.dashboard.add_indicator(
			__("{0} → {1}", [
				label_for(frm.doc.source_language),
				label_for(frm.doc.target_language),
			]),
			"gray"
		);

		if (["Queued", "Translating"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Cancel"), async () => {
				await frappe.xcall("ai_fr_hg.api.translation.cancel", {
					translation: frm.doc.name,
				});
				frappe.show_alert({ message: __("Cancellation requested"), indicator: "orange" });
				frm.reload_doc();
			}).addClass("btn-danger");
			if (!frm._ai_translation_realtime) {
				frm._ai_translation_realtime = frappe.realtime.on(
					"ai_translation_progress",
					(data) => {
						if (data && data.translation === frm.doc.name) {
							frm.reload_doc();
						}
					}
				);
				frm.script_manager &&
					frm.script_manager.extend &&
					$(frm.wrapper).on("hide", () => {
						if (frm._ai_translation_realtime) {
							frappe.realtime.off(
								"ai_translation_progress",
								frm._ai_translation_realtime
							);
							frm._ai_translation_realtime = null;
						}
					});
			}
		}

		if (["Draft", "Failed", "Completed", "Needs Review"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Translate Now"), async () => {
				await frm.call("translate", { background: true });
				frappe.show_alert({ message: __("Queued for translation"), indicator: "blue" });
				frm.reload_doc();
			}).addClass("btn-primary");

			frm.add_custom_button(
				__("Translate in Foreground"),
				async () => {
					frappe.dom.freeze(__("Translating..."));
					try {
						await frm.call("translate", { background: false });
					} finally {
						frappe.dom.unfreeze();
						frm.reload_doc();
					}
				},
				__("Actions")
			);
		}

		if ((frm.doc.segments || []).length) {
			frm.add_custom_button(
				__("Review Segments"),
				() => render_review(frm, false),
				__("Review")
			);
			if (frm.doc.flagged_segments) {
				frm.add_custom_button(
					__("Review Flagged Only"),
					() => render_review(frm, true),
					__("Review")
				);
			}
			frm.add_custom_button(
				__("Mark as Reviewed"),
				() =>
					frappe.confirm(__("Accept this translation as reviewed?"), async () => {
						await frm.call("mark_reviewed");
						frm.reload_doc();
					}),
				__("Review")
			);
		}

		if (frm.doc.translated_text) {
			frm.add_custom_button(
				__("Copy Translation"),
				() => {
					frappe.utils.copy_to_clipboard(frm.doc.translated_text);
				},
				__("Actions")
			);

			frm.add_custom_button(
				__("Download as Text"),
				() => {
					const blob = new Blob([frm.doc.translated_text], {
						type: "text/plain;charset=utf-8",
					});
					const link = document.createElement("a");
					link.href = URL.createObjectURL(blob);
					link.download = `${frm.doc.name}-${frm.doc.target_language}.txt`;
					link.click();
					URL.revokeObjectURL(link.href);
				},
				__("Actions")
			);

			if (!frm.doc.translated_document) {
				frm.add_custom_button(
					__("Index as Document"),
					async () => {
						frappe.dom.freeze(__("Indexing..."));
						try {
							const result = await frm.call("index_output_document");
							frappe.dom.unfreeze();
							frappe.show_alert({
								message: __("Created {0}", [result.message.document]),
								indicator: "green",
							});
							frm.reload_doc();
						} catch (error) {
							frappe.dom.unfreeze();
						}
					},
					__("Actions")
				);
			}
		}

		if (frm.doc.status === "Failed" && frm.doc.error_message) {
			frm.dashboard.set_headline(
				`<span class="text-danger">${frappe.utils.escape_html(
					frm.doc.error_message
				)}</span>`
			);
		}
	},

	target_language(frm) {
		frm.set_value("direction", direction_for(frm.doc.target_language));
		apply_direction(frm);
	},

	source_language(frm) {
		apply_direction(frm);
	},
});
