// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Pipeline Run", {
	refresh(frm) {
		if (frm.is_new()) return;

		const colors = {
			Queued: "grey",
			Running: "blue",
			Completed: "green",
			Failed: "red",
			Cancelled: "orange",
		};
		frm.page.set_indicator(
			__(frm.doc.status),
			colors[frm.doc.status] || "grey"
		);

		frm.add_custom_button(__("Pipeline"), () =>
			frappe.set_route("Form", "AI Pipeline", frm.doc.pipeline)
		);

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
										<span class="indicator ${s.status === "Success" ? "green" : s.status === "Failed" ? "red" : "grey"}">
											${__(s.status)}
										</span>
									</div>
									<div class="col-sm-6 text-muted small">${frappe.utils.escape_html(s.output_preview || "")}</div>
								</div>`
						)
						.join("")}
				</div>`
			);
		}
	},
});