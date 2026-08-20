// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Knowledge Explorer - search, inspect and manage indexed documents.
 */

function relative_time(value) {
	if (!value) return "";
	try {
		return frappe.datetime.comment_when(value);
	} catch (error) {
		return "";
	}
}

frappe.pages["knowledge-explorer"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Knowledge Explorer"),
		single_column: true,
	});
	wrapper.explorer = new KnowledgeExplorer(page);
};

frappe.pages["knowledge-explorer"].on_page_show = function (wrapper) {
	wrapper.explorer && wrapper.explorer.refresh();
};

class KnowledgeExplorer {
	constructor(page) {
		this.page = page;
		this.search_type = "Hybrid";
		this.selected_kbs = [];
		this.folder = null;
		this.entity_type = "";
		this.page_offset = 0;
		this.page_size = 10;
		this.last_query = "";
		this.is_manager =
			frappe.user_roles.includes("AI Manager") || frappe.user_roles.includes("System Manager");
		this.make();
		this.restore_hash();
		this.refresh();
	}

	make() {
		this.page.main.addClass("ai-ops-page");
		this.page.main.html(`
			<div class="ai-explorer">
				<div class="ai-explorer-search">
					<div class="ai-search-row">
						<input type="text" class="form-control ai-query"
							placeholder="${__("Search your documents in natural language...")}"
							aria-label="${__("Search query")}">
						<button class="btn btn-primary ai-search-btn" type="button">${__("Search")}</button>
					</div>
					<div class="ai-search-options">
						<div class="ai-search-types" role="group" aria-label="${__("Search type")}">
							<button class="ai-type-btn active" type="button" data-type="Hybrid">${__("Hybrid")}</button>
							<button class="ai-type-btn" type="button" data-type="Semantic">${__("Semantic")}</button>
							<button class="ai-type-btn" type="button" data-type="Keyword">${__("Keyword")}</button>
						</div>
						<label class="ai-ask-toggle">
							<input type="checkbox" class="ai-ask-mode">
							${__("Answer with AI")}
						</label>
						<div class="ai-kb-filters"></div>
					</div>
					<div class="ai-search-scope">
						<div class="ai-folder-control"></div>
						<select class="form-control ai-entity-type" aria-label="${__("Entity type")}">
							<option value="">${__("All entities")}</option>
						</select>
					</div>
				</div>

				<div class="ai-degraded-banner hidden" role="status"></div>
				<div class="ai-diagnostics hidden"></div>

				<div class="ai-explorer-body">
					<div class="ai-explorer-main">
						<div class="ai-answer-panel hidden"></div>
						<div class="ai-results"></div>
						<div class="ai-pagination hidden"></div>
					</div>
					<aside class="ai-explorer-side">
						<div class="ai-kb-stats"></div>
						<div class="ai-kb-list"></div>
					</aside>
				</div>
			</div>
		`);

		this.$query = this.page.main.find(".ai-query");
		this.$results = this.page.main.find(".ai-results");
		this.$answer = this.page.main.find(".ai-answer-panel");
		this.$banner = this.page.main.find(".ai-degraded-banner");
		this.$diagnostics = this.page.main.find(".ai-diagnostics");
		this.$pagination = this.page.main.find(".ai-pagination");

		this.page.set_primary_action(__("Upload Document"), () => this.upload());
		this.page.add_menu_item(__("New Knowledge Base"), () =>
			frappe.new_doc("AI Knowledge Base")
		);
		this.page.add_menu_item(__("All Documents"), () =>
			frappe.set_route("List", "AI Document")
		);
		this.page.add_menu_item(__("Supported Formats"), () => this.show_formats());

		this.page.main.find(".ai-search-btn").on("click", () => this.search({ reset: true }));
		this.$query.on("keydown", (event) => {
			if (event.key === "Enter") this.search({ reset: true });
		});

		const me = this;
		this.page.main.find(".ai-type-btn").on("click", function () {
			me.page.main.find(".ai-type-btn").removeClass("active");
			$(this).addClass("active");
			me.search_type = $(this).data("type");
			if (me.$query.val().trim()) me.search({ reset: true });
		});

		this.$results.on("click", ".ai-result-open", function () {
			frappe.set_route("Form", "AI Document", $(this).data("document"));
		});
		this.$results.on("click", ".ai-search-retry", () => this.search());

		this.folder_control = frappe.ui.form.make_control({
			parent: this.page.main.find(".ai-folder-control"),
			df: {
				fieldtype: "Link",
				options: "File",
				label: __("Folder"),
				filters: { is_folder: 1 },
			},
			render_input: true,
		});
		this.folder_control.df.onchange = () => {
			this.folder = this.folder_control.get_value() || null;
			if (this.$query.val().trim()) this.search({ reset: true });
		};

		this.page.main.find(".ai-entity-type").on("change", (event) => {
			this.entity_type = event.target.value || "";
			if (this.$query.val().trim()) this.search({ reset: true });
		});

		this.load_facets();
	}

	async refresh() {
		try {
			this.overview = await frappe.xcall("ai_fr_hg.api.knowledge.get_knowledge_overview");
		} catch (error) {
			this.$results.html(
				`<div class="ai-ops-empty text-danger">${frappe.utils.escape_html(
					error.message || __("Could not load knowledge overview.")
				)}</div>`
			);
			return;
		}
		this.render_stats();
		this.render_kb_list();
		this.render_kb_filters();
		if (this.last_query || this.$query.val().trim()) {
			this.search();
		} else {
			this.render_recent();
		}
	}

	async load_facets() {
		try {
			const facets = await frappe.xcall("ai_fr_hg.api.knowledge.get_search_facets");
			const $select = this.page.main.find(".ai-entity-type");
			(facets.entity_types || []).forEach((row) => {
				$select.append(
					`<option value="${frappe.utils.escape_html(row.entity_type)}">${frappe.utils.escape_html(
						row.entity_type
					)} (${row.total})</option>`
				);
			});
			if (this.entity_type) $select.val(this.entity_type);
		} catch (error) {
			// Facets are optional; search still works without them.
		}
	}

	render_stats() {
		const totals = this.overview.totals;
		this.page.main.find(".ai-kb-stats").html(`
			<div class="ai-side-card">
				<h6>${__("Index")}</h6>
				<div class="ai-stat-row"><span>${__("Documents")}</span><b>${totals.documents}</b></div>
				<div class="ai-stat-row"><span>${__("Chunks")}</span><b>${totals.chunks}</b></div>
				<div class="ai-stat-row">
					<span>${__("Embedded")}</span>
					<b class="${totals.embedded < totals.chunks ? "text-warning" : ""}">
						${totals.embedded}
					</b>
				</div>
				<div class="ai-stat-row">
					<span>${__("Characters")}</span><b>${this.compact(totals.characters)}</b>
				</div>
			</div>
		`);
	}

	render_kb_list() {
		const $list = this.page.main.find(".ai-kb-list").empty();
		$list.append(`<div class="ai-side-card"><h6>${__("Knowledge Bases")}</h6></div>`);
		const $card = $list.find(".ai-side-card");

		if (!this.overview.knowledge_bases.length) {
			$card.append(`<p class="text-muted small">${__("None yet.")}</p>`);
			return;
		}

		this.overview.knowledge_bases.forEach((kb) => {
			$card.append(`
				<div class="ai-kb-row">
					<div>
						<a href="/app/ai-knowledge-base/${encodeURIComponent(kb.name)}">
							${frappe.utils.escape_html(kb.knowledge_base_name)}
						</a>
						<div class="text-muted small">
							${kb.document_count || 0} ${__("docs")} · ${kb.chunk_count || 0} ${__("chunks")}
						</div>
					</div>
					<span class="indicator-pill ${kb.index_status === "Idle" ? "green" : "orange"}">
						${kb.index_status}
					</span>
				</div>
			`);
		});

		if (this.overview.failed_documents.length) {
			const $failed = $(`
				<div class="ai-side-card">
					<h6 class="text-danger">${__("Failed Documents")}</h6>
				</div>
			`);
			this.overview.failed_documents.forEach((doc) => {
				$failed.append(`
					<div class="ai-kb-row">
						<div>
							<a href="/app/ai-document/${doc.name}">${frappe.utils.escape_html(doc.title)}</a>
							<div class="text-muted small">${frappe.utils.escape_html(
								(doc.error_message || "").slice(0, 90)
							)}</div>
						</div>
					</div>
				`);
			});
			$list.append($failed);
		}
	}

	render_kb_filters() {
		const $wrap = this.page.main.find(".ai-kb-filters").empty();
		this.overview.knowledge_bases.forEach((kb) => {
			const $chip = $(
				`<button class="ai-kb-chip" data-kb="${frappe.utils.escape_html(kb.name)}">
					${frappe.utils.escape_html(kb.knowledge_base_name)}
				</button>`
			);
			if (this.selected_kbs.includes(kb.name)) $chip.addClass("active");
			$chip.on("click", () => {
				$chip.toggleClass("active");
				this.selected_kbs = $wrap
					.find(".ai-kb-chip.active")
					.map((_, el) => $(el).data("kb"))
					.get();
				if (this.$query.val().trim()) this.search({ reset: true });
			});
			$wrap.append($chip);
		});
	}

	render_recent() {
		const docs = this.overview.recent_documents || [];
		if (!docs.length) {
			this.$results.html(`
				<div class="ai-ops-empty text-muted">
					${__("No documents yet.")}
					<button class="btn btn-sm btn-primary ai-first-upload">${__("Upload your first document")}</button>
				</div>
			`);
			this.$results.find(".ai-first-upload").on("click", () => this.upload());
			return;
		}

		this.$results.html(`
			<h6 class="text-muted">${__("Recent Documents")}</h6>
			${docs
				.map(
					(doc) => `
				<div class="ai-result-card">
					<div class="ai-result-head">
						<a href="/app/ai-document/${doc.name}">${frappe.utils.escape_html(doc.title)}</a>
						<span class="indicator-pill ${
							doc.status === "Indexed"
								? "green"
								: doc.status === "Failed"
								? "red"
								: "orange"
						}">
							${doc.status}
						</span>
					</div>
					<div class="text-muted small">
						${frappe.utils.escape_html(doc.knowledge_base)} ·
						${doc.chunk_count || 0} ${__("chunks")} ·
						${relative_time(doc.modified)}
					</div>
				</div>`
				)
				.join("")}
		`);
	}

	async search(opts = {}) {
		const query = (this.$query.val() || "").trim();
		if (!query) return;
		if (opts.reset) this.page_offset = 0;
		this.last_query = query;
		this.persist_hash();

		const askMode = this.page.main.find(".ai-ask-mode").is(":checked");
		this.$results.html(`<div class="ai-loading">${__("Searching...")}</div>`);
		this.$answer.addClass("hidden").empty();
		this.$banner.addClass("hidden").empty();
		this.$diagnostics.addClass("hidden").empty();
		this.$pagination.addClass("hidden").empty();

		try {
			if (askMode) {
				const response = await frappe.xcall("ai_fr_hg.api.knowledge.ask", {
					question: query,
					knowledge_bases: this.selected_kbs.length ? this.selected_kbs : null,
					folder: this.folder,
				});
				this.$answer.removeClass("hidden").html(`
					<div class="ai-answer">
						<div class="ai-answer-label text-muted small">${__("AI Answer")}</div>
						<div class="ai-answer-body">${frappe.markdown(response.answer || "")}</div>
						<div class="text-muted small">
							${frappe.utils.escape_html(response.model || "")} · ${response.total_tokens || 0} ${__("tokens")} ·
							${((response.duration_ms || 0) / 1000).toFixed(1)}s
						</div>
					</div>
				`);
				this.render_results(response.citations || [], query);
			} else {
				const response = await frappe.xcall("ai_fr_hg.api.knowledge.search", {
					query,
					knowledge_bases: this.selected_kbs.length ? this.selected_kbs : null,
					top_k: this.page_size,
					offset: this.page_offset,
					search_type: this.search_type,
					folder: this.folder,
					entity_type: this.entity_type || null,
				});
				this.render_diagnostics(response.diagnostics);
				this.render_results(response.results || [], query, response.total, response.offset);
			}
		} catch (error) {
			const denied = /permission|not permitted|not allowed/i.test(error.message || "");
			this.$results.html(`
				<div class="ai-ops-empty text-danger">
					${frappe.utils.escape_html(
						denied
							? __("You do not have permission to search these knowledge bases.")
							: error.message || __("Search failed.")
					)}
					<button type="button" class="btn btn-sm btn-default ai-search-retry">${__("Retry")}</button>
				</div>
			`);
		}
	}

	render_diagnostics(diagnostics) {
		if (!diagnostics) return;
		if (diagnostics.degraded) {
			const reasons = (diagnostics.degraded_reasons || [diagnostics.fallback_reason])
				.filter(Boolean)
				.map((reason) => frappe.utils.escape_html(reason))
				.join(", ");
			this.$banner
				.removeClass("hidden")
				.html(
					`<strong>${__("Degraded retrieval")}</strong> — ${
						reasons || __("semantic search fell back to keyword matching.")
					}`
				);
		}
		if (!this.is_manager) return;
		const models = (diagnostics.embedding_models || [])
			.map((item) => `${frappe.utils.escape_html(item.model)} (${item.dimensions}d)`)
			.join(", ");
		this.$diagnostics.removeClass("hidden").html(`
			<div class="ai-side-card">
				<h6>${__("Retrieval diagnostics")}</h6>
				<div class="ai-stat-row"><span>${__("Strategy")}</span><b>${frappe.utils.escape_html(
					diagnostics.retrieval_strategy || ""
				)}</b></div>
				<div class="ai-stat-row"><span>${__("Corpus")}</span><b>${diagnostics.corpus_size || 0}</b></div>
				<div class="ai-stat-row"><span>${__("Candidates")}</span><b>${diagnostics.candidate_count || 0}</b></div>
				<div class="ai-stat-row"><span>${__("Keyword")}</span><b>${frappe.utils.escape_html(
					diagnostics.keyword_backend || ""
				)}</b></div>
				${models ? `<div class="text-muted small">${models}</div>` : ""}
			</div>
		`);
	}

	render_results(results, query, total, offset) {
		if (!results.length) {
			this.$results.html(`
				<div class="ai-ops-empty text-muted">
					${__("Nothing matched. Try different words, or check that the documents are indexed.")}
				</div>
			`);
			return;
		}

		this.$results.html(`
			<h6 class="text-muted">${__("{0} passages", [results.length])}</h6>
			${results
				.map(
					(result, index) => `
				<div class="ai-result-card">
					<div class="ai-result-head">
						<span>
							<span class="ai-result-index">${index + 1}</span>
							<a class="ai-result-open" data-document="${result.document}">
								${frappe.utils.escape_html(result.document_title)}
							</a>
							${
								result.heading
									? `<span class="text-muted small">· ${frappe.utils.escape_html(
											result.heading
									  )}</span>`
									: ""
							}
							${
								result.page_number
									? `<span class="text-muted small">· ${__("page")} ${
											result.page_number
									  }</span>`
									: ""
							}
						</span>
						<span class="ai-score-badge" title="${__("Relevance")}">
							${(result.score * 100).toFixed(0)}%
						</span>
					</div>
					<div class="ai-result-snippet">${this.highlight(result.content, query)}</div>
					<div class="ai-result-meta text-muted small">
						${frappe.utils.escape_html(result.knowledge_base)}
						${result.semantic_score ? ` · ${__("semantic")} ${(result.semantic_score * 100).toFixed(0)}%` : ""}
						${result.keyword_score ? ` · ${__("keyword")} ${(result.keyword_score * 100).toFixed(0)}%` : ""}
					</div>
				</div>`
				)
				.join("")}
		`);
		this.render_pagination(total, offset);
	}

	render_pagination(total, offset) {
		const known = Number(total || 0);
		if (!known || known <= this.page_size) {
			this.$pagination.addClass("hidden").empty();
			return;
		}
		const start = Number(offset || 0);
		const can_prev = start > 0;
		const can_next = start + this.page_size < known;
		this.$pagination.removeClass("hidden").html(`
			<button type="button" class="btn btn-sm btn-default ai-page-prev" ${
				can_prev ? "" : "disabled"
			}>${__("Previous")}</button>
			<span class="text-muted small">${start + 1}–${Math.min(start + this.page_size, known)} / ${known}</span>
			<button type="button" class="btn btn-sm btn-default ai-page-next" ${
				can_next ? "" : "disabled"
			}>${__("Next")}</button>
		`);
		this.$pagination.find(".ai-page-prev").on("click", () => {
			this.page_offset = Math.max(0, start - this.page_size);
			this.search();
		});
		this.$pagination.find(".ai-page-next").on("click", () => {
			this.page_offset = start + this.page_size;
			this.search();
		});
	}

	persist_hash() {
		const params = new URLSearchParams();
		const query = (this.$query.val() || "").trim();
		if (query) params.set("q", query);
		if (this.search_type && this.search_type !== "Hybrid") params.set("type", this.search_type);
		if (this.selected_kbs.length) params.set("kb", this.selected_kbs.join(","));
		if (this.folder) params.set("folder", this.folder);
		if (this.entity_type) params.set("entity", this.entity_type);
		if (this.page_offset) params.set("offset", String(this.page_offset));
		const hash = params.toString();
		if (typeof history !== "undefined" && history.replaceState) {
			history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
		}
	}

	restore_hash() {
		const raw = (typeof location !== "undefined" && location.hash ? location.hash.slice(1) : "") || "";
		if (!raw) return;
		const params = new URLSearchParams(raw);
		if (params.get("q")) this.$query.val(params.get("q"));
		if (params.get("type")) {
			this.search_type = params.get("type");
			this.page.main.find(".ai-type-btn").removeClass("active");
			this.page.main.find('.ai-type-btn[data-type="' + this.search_type + '"]').addClass("active");
		}
		if (params.get("kb")) this.selected_kbs = params.get("kb").split(",").filter(Boolean);
		if (params.get("folder")) {
			this.folder = params.get("folder");
			if (this.folder_control) this.folder_control.set_value(this.folder);
		}
		if (params.get("entity")) this.entity_type = params.get("entity");
		if (params.get("offset")) this.page_offset = parseInt(params.get("offset"), 10) || 0;
	}

	highlight(content, query) {
		const escaped = frappe.utils.escape_html((content || "").slice(0, 600));
		const terms = (query || "")
			.split(/\s+/)
			.filter((term) => term.length > 2)
			.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

		if (!terms.length) return escaped;
		return escaped.replace(new RegExp(`(${terms.join("|")})`, "gi"), "<mark>$1</mark>");
	}

	async upload() {
		const me = this;
		// The global FileUploader extension supplies the native in-dialog folder
		// selector, including creation of a child folder when needed.
		new frappe.ui.FileUploader({
			on_success(file) {
				frappe.prompt(
					[
						{
							fieldtype: "Link",
							fieldname: "knowledge_base",
							label: __("Knowledge Base"),
							options: "AI Knowledge Base",
							reqd: 1,
							default: (me.overview.knowledge_bases[0] || {}).name,
						},
						{
							fieldtype: "Data",
							fieldname: "title",
							label: __("Title"),
							default: file.file_name,
							reqd: 1,
						},
					],
					async (values) => {
						const result = await frappe
							.xcall("ai_fr_hg.api.knowledge.upload_document", {
								file_url: file.file_url,
								file_record: file.name,
								knowledge_base: values.knowledge_base,
								title: values.title,
							})
							.catch((error) => {
								frappe.msgprint({
									title: __("Upload could not be ingested"),
									message:
										error?.message ||
										error?._server_messages ||
										__("The uploaded file could not be read."),
									indicator: "red",
								});
								return null;
							});
						if (!result) return;
						frappe.show_alert({
							message: __("Processing {0}...", [values.title]),
							indicator: "blue",
						});
						setTimeout(() => me.refresh(), 3000);
					},
					__("Add to Knowledge Base"),
					__("Upload")
				);
			},
		});
	}

	async show_formats() {
		const formats = await frappe.xcall("ai_fr_hg.api.knowledge.get_supported_formats");
		frappe.msgprint({
			title: __("Supported Formats"),
			wide: true,
			message: Object.keys(formats.by_reader)
				.sort()
				.map(
					(reader) =>
						`<p><b>${frappe.utils.escape_html(reader)}</b>: ${formats.by_reader[reader]
							.map((ext) => `<code>.${ext}</code>`)
							.join(" ")}</p>`
				)
				.join(""),
		});
	}

	compact(value) {
		const number = Number(value || 0);
		if (number >= 1e9) return (number / 1e9).toFixed(1) + "B";
		if (number >= 1e6) return (number / 1e6).toFixed(1) + "M";
		if (number >= 1e3) return (number / 1e3).toFixed(1) + "K";
		return String(number);
	}
}
