// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Pipeline Run", {
	refresh(frm) {
		if (frm.is_new()) return;

		const colors = {
			Queued: "grey",
			Running: "blue",
			"Waiting Approval": "orange",
			Completed: "green",
			Failed: "red",
			Cancelled: "orange",
		};
		frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "grey");

		frm.add_custom_button(__("Pipeline"), () => frappe.set_route("Form", "AI Pipeline", frm.doc.pipeline));

		if (["Queued", "Running", "Waiting Approval"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Cancel"), async () => {
				await frm.call("cancel_run");
				frm.reload_doc();
			}).addClass("btn-danger");
		}
		if (["Failed", "Cancelled"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Retry"), async () => {
				const result = await frm.call("retry");
				frappe.set_route("Form", "AI Pipeline Run", result.message.run);
			});
		}
		if (frm.doc.waiting_invocation) {
			frm.add_custom_button(__("Open Approval"), () =>
				frappe.set_route("Form", "AI Tool Invocation", frm.doc.waiting_invocation)
			);
		}

		if (frm.doc.step_logs && frm.doc.step_logs.length) {
			frm.dashboard.add_section(
				`<div class="pipeline-steps">
					<h6>${__("Step Logs")}</h6>
					${frm.doc.step_logs
						.map(
							(s) =>
								`<div class="row mb-1">
									<div class="col-sm-4">${frappe.utils.escape_html(s.step_name)}</div>
									<div class="col-sm-2">
										<span class="indicator ${
											s.status === "Success"
												? "green"
												: s.status === "Failed"
													? "red"
													: s.status === "Waiting Approval"
														? "orange"
														: "grey"
										}">
											${__(s.status)}
										</span>
									</div>
									<div class="col-sm-6 text-muted small">${frappe.utils.escape_html(s.output || s.error_message || "")}</div>
								</div>`
						)
						.join("")}
				</div>`
			);
		}
		frappe.realtime.on("ai_pipeline_finished", (data) => {
			if (data && data.run === frm.doc.name) frm.reload_doc();
		});
	},
});
