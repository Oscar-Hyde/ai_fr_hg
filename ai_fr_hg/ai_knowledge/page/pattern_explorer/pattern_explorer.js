// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

frappe.pages["pattern-explorer"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Pattern Explorer"),
		single_column: true,
	});
	wrapper.explorer = new PatternExplorer(page);
};

frappe.pages["pattern-explorer"].on_page_show = function (wrapper) {
	wrapper.explorer && wrapper.explorer.refresh();
};

class PatternExplorer {
	constructor(page) {
		this.page = page;
		this.offset = 0;
		this.limit = 50;
		this.entity_type = "";
		this.knowledge_base = "";
		this.make();
		this.refresh();
	}

	make() {
		this.page.main.addClass("ai-ops-page");
		const types = ["email", "url", "phone", "ip", "hash", "date", "identifier", "money"];
		const options = types
			.map((type) => `<option value="${type}">${type}</option>`)
			.join("");
		this.page.main.html(`
			<div class="ai-explorer">
				<div class="ai-search-options">
					<select class="form-control ai-entity-type" aria-label="${__("Entity type")}">
						<option value="">${__("All types")}</option>
						${options}
					</select>
					<div class="ai-kb-control"></div>
				</div>
				<div class="ai-results"></div>
				<div class="ai-pagination hidden"></div>
			</div>
		`);
		this.$results = this.page.main.find(".ai-results");
		this.$pagination = this.page.main.find(".ai-pagination");
		this.page.main.find(".ai-entity-type").on("change", (event) => {
			this.entity_type = event.target.value || "";
			this.offset = 0;
			this.refresh();
		});
		this.kb_control = frappe.ui.form.make_control({
			parent: this.page.main.find(".ai-kb-control"),
			df: {
				fieldtype: "Link",
				options: "AI Knowledge Base",
				label: __("Knowledge Base"),
			},
			render_input: true,
		});
		this.kb_control.df.onchange = () => {
			this.knowledge_base = this.kb_control.get_value() || "";
			this.offset = 0;
			this.refresh();
		};
		this.$results.on("click", ".ai-retry", () => this.refresh());
	}

	async refresh() {
		this.$results.html(`<div class="ai-loading">${__("Loading patterns...")}</div>`);
		try {
			const payload = await frappe.xcall("ai_fr_hg.api.knowledge.explore_pattern_entities", {
				knowledge_base: this.knowledge_base || null,
				entity_type: this.entity_type || null,
				limit: this.limit,
				offset: this.offset,
			});
			this.render(payload);
		} catch (error) {
			const denied = /permission|not permitted|not allowed/i.test(error.message || "");
			const message = denied
				? __("You do not have permission to list pattern entities.")
				: error.message || __("Could not load pattern entities.");
			this.$results.html(`
				<div class="ai-ops-empty text-danger">
					${frappe.utils.escape_html(message)}
					<button type="button" class="btn btn-sm btn-default ai-retry">${__("Retry")}</button>
				</div>
			`);
		}
	}

	render(payload) {
		const rows = payload.entities || [];
		if (!rows.length) {
			this.$results.html(
				`<div class="ai-ops-empty text-muted">${__("No pattern entities match these filters.")}</div>`
			);
			this.$pagination.addClass("hidden").empty();
			return;
		}
		const counts = payload.entity_counts || {};
		const summary = Object.keys(counts)
			.map((key) => `${frappe.utils.escape_html(key)}: ${counts[key]}`)
			.join(" · ");
		this.$results.html(`
			<div class="text-muted small">${summary}</div>
			${rows
				.map(
					(row) => `
				<div class="ai-result-card">
					<div class="ai-result-head">
						<a href="/app/ai-document/${encodeURIComponent(row.document)}">${frappe.utils.escape_html(
							row.document
						)}</a>
						<span class="indicator-pill grey">${frappe.utils.escape_html(row.entity_type)}</span>
					</div>
					<div><code>${frappe.utils.escape_html(row.value)}</code> × ${row.occurrences || 1}</div>
					<div class="text-muted small">${frappe.utils.escape_html(row.context_quote || "")}</div>
				</div>`
				)
				.join("")}
		`);
		const start = Number(payload.offset || 0);
		const can_prev = start > 0;
		const can_next = rows.length >= this.limit;
		this.$pagination.removeClass("hidden").html(`
			<button type="button" class="btn btn-sm btn-default ai-page-prev" ${
				can_prev ? "" : "disabled"
			}>${__("Previous")}</button>
			<span class="text-muted small">${start + 1}–${start + rows.length}</span>
			<button type="button" class="btn btn-sm btn-default ai-page-next" ${
				can_next ? "" : "disabled"
			}>${__("Next")}</button>
		`);
		this.$pagination.find(".ai-page-prev").on("click", () => {
			this.offset = Math.max(0, start - this.limit);
			this.refresh();
		});
		this.$pagination.find(".ai-page-next").on("click", () => {
			this.offset = start + this.limit;
			this.refresh();
		});
	}
}
