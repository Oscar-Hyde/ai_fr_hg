// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * AI Assistant - the platform's primary conversational interface.
 *
 * Three-panel layout: conversation list, message thread, context inspector.
 */

frappe.pages["ai-assistant"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI Assistant"),
		single_column: true,
	});

	wrapper.assistant = new AIAssistant(page, wrapper);
};

frappe.pages["ai-assistant"].on_page_show = function (wrapper) {
	wrapper.assistant && wrapper.assistant.refresh_conversations();
};

class AIAssistant {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.conversation = null;
		this.context = {};
		this.sending = false;
		this.selected_kbs = [];

		this.make();
		this.load_context();
		this.bind_realtime();
	}

	make() {
		this.page.main.addClass("ai-assistant-page");
		this.page.main.html(`
			<div class="ai-assistant">
				<aside class="ai-sidebar">
					<div class="ai-sidebar-header">
						<button class="btn btn-primary btn-sm btn-block ai-new-chat">
							${frappe.utils.icon("add", "sm")} ${__("New Conversation")}
						</button>
					</div>
					<div class="ai-sidebar-search">
						<input type="text" class="form-control input-sm ai-search-conv"
							placeholder="${__("Search conversations")}">
					</div>
					<div class="ai-conversation-list"></div>
				</aside>

				<main class="ai-main">
					<header class="ai-main-header">
						<div class="ai-header-left">
							<span class="ai-conversation-title">${__("New Conversation")}</span>
							<span class="ai-conversation-meta text-muted"></span>
						</div>
						<div class="ai-header-right">
							<div class="ai-agent-select"></div>
							<div class="ai-model-select"></div>
							<button class="btn btn-default btn-sm ai-toggle-context"
								title="${__("Toggle context panel")}">
								${frappe.utils.icon("info", "sm")}
							</button>
						</div>
					</header>

					<div class="ai-messages">
						<div class="ai-empty-state">
							<div class="ai-empty-icon">${frappe.utils.icon("message", "lg")}</div>
							<h4>${__("Local AI Assistant")}</h4>
							<p class="text-muted">${__("Everything runs on this machine. Nothing leaves your network.")}</p>
							<div class="ai-suggestions"></div>
						</div>
					</div>

					<footer class="ai-composer">
						<div class="ai-composer-kbs"></div>
						<div class="ai-composer-input">
							<textarea class="form-control ai-input" rows="1"
								placeholder="${__("Ask anything, or attach a document...")}"></textarea>
							<div class="ai-composer-actions">
								<button class="btn btn-default btn-sm ai-attach"
									title="${__("Attach a document")}">
									${frappe.utils.icon("attachment", "sm")}
								</button>
								<button class="btn btn-primary btn-sm ai-send" disabled>
									${frappe.utils.icon("right", "sm")}
								</button>
							</div>
						</div>
						<div class="ai-composer-hint text-muted small">
							${__("Enter to send, Shift+Enter for a new line")}
						</div>
					</footer>
				</main>

				<aside class="ai-context-panel hidden">
					<div class="ai-context-header">
						<strong>${__("Context")}</strong>
						<button class="btn btn-xs btn-default ai-close-context">
							${frappe.utils.icon("close", "sm")}
						</button>
					</div>
					<div class="ai-context-body">
						<p class="text-muted small">${__("Sources cited in the latest answer appear here.")}</p>
					</div>
				</aside>
			</div>
		`);

		this.$sidebar = this.page.main.find(".ai-conversation-list");
		this.$messages = this.page.main.find(".ai-messages");
		this.$input = this.page.main.find(".ai-input");
		this.$send = this.page.main.find(".ai-send");
		this.$context = this.page.main.find(".ai-context-panel");
		this.$title = this.page.main.find(".ai-conversation-title");
		this.$meta = this.page.main.find(".ai-conversation-meta");

		this.bind_events();
		this.add_page_actions();
	}

	add_page_actions() {
		this.page.set_secondary_action(__("Refresh"), () => this.load_context());

		this.page.add_menu_item(__("Knowledge Explorer"), () =>
			frappe.set_route("knowledge-explorer")
		);
		this.page.add_menu_item(__("Summarise Conversation"), () => this.summarize());
		this.page.add_menu_item(__("Archive Conversation"), () => this.archive());
		this.page.add_menu_item(__("Delete Conversation"), () => this.delete_conversation());
	}

	bind_events() {
		const me = this;

		this.page.main.find(".ai-new-chat").on("click", () => this.new_conversation());
		this.page.main
			.find(".ai-toggle-context")
			.on("click", () => this.$context.toggleClass("hidden"));
		this.page.main
			.find(".ai-close-context")
			.on("click", () => this.$context.addClass("hidden"));
		this.page.main.find(".ai-attach").on("click", () => this.attach_document());

		this.$input.on("input", function () {
			// Auto-grow the composer up to a sensible ceiling.
			this.style.height = "auto";
			this.style.height = Math.min(this.scrollHeight, 200) + "px";
			me.$send.prop("disabled", !this.value.trim() || me.sending);
		});

		this.$input.on("keydown", (event) => {
			if (event.key === "Enter" && !event.shiftKey) {
				event.preventDefault();
				this.send();
			}
		});

		this.$send.on("click", () => this.send());

		this.page.main.find(".ai-search-conv").on(
			"input",
			frappe.utils.debounce((event) => this.filter_conversations(event.target.value), 200)
		);

		this.$sidebar.on("click", ".ai-conversation-item", function () {
			me.open_conversation($(this).data("name"));
		});

		this.$messages.on("click", ".ai-citation", function () {
			const doc = $(this).data("document");
			if (doc) frappe.set_route("Form", "AI Document", doc);
		});

		this.$messages.on("click", ".ai-feedback", function () {
			me.send_feedback($(this).data("message"), $(this).data("value"), $(this));
		});

		this.$messages.on("click", ".ai-copy", function () {
			frappe.utils.copy_to_clipboard(
				$(this).closest(".ai-message").find(".ai-bubble").text()
			);
		});
	}

	bind_realtime() {
		frappe.realtime.on("ai_document_processed", (data) => {
			frappe.show_alert({
				message: __("Document {0} is indexed and searchable.", [data.document]),
				indicator: "green",
			});
		});
	}

	async load_context() {
		try {
			this.context = await frappe.xcall("ai_fr_hg.api.chat.get_chat_context");
		} catch (error) {
			this.render_error(__("Could not load the AI configuration."));
			return;
		}

		if (!this.context.settings.platform_enabled) {
			this.render_error(
				__("The AI Platform is disabled. Enable it in AI Platform Settings."),
				__("Open Settings"),
				() => frappe.set_route("Form", "AI Platform Settings")
			);
			return;
		}

		if (!this.context.agents.length) {
			this.render_error(__("No AI agent is configured yet."), __("Create an Agent"), () =>
				frappe.new_doc("AI Agent")
			);
			return;
		}

		this.render_selectors();
		this.render_knowledge_chips();
		this.render_suggestions();
		this.refresh_conversations();
	}

	render_selectors() {
		const me = this;
		const $agent = this.page.main.find(".ai-agent-select").empty();
		const $model = this.page.main.find(".ai-model-select").empty();

		this.agent_control = frappe.ui.form.make_control({
			parent: $agent,
			df: {
				fieldtype: "Select",
				fieldname: "agent",
				options: this.context.agents.map((a) => ({ label: a.agent_name, value: a.name })),
				change() {
					me.selected_agent = this.get_value();
				},
			},
			render_input: true,
		});
		this.agent_control.set_value(this.context.agents[0].name);
		this.selected_agent = this.context.agents[0].name;

		const models = this.context.models.filter((m) => m.status !== "Missing");
		const defaultModel =
			models.find((m) => m.name === this.context.settings.default_chat_model) ||
			models.find((m) => m.is_default && m.model_type === "Chat") ||
			models.find((m) => m.model_type === "Chat");
		if (defaultModel) this.selected_model = defaultModel.name;
		this.model_control = frappe.ui.form.make_control({
			parent: $model,
			df: {
				fieldtype: "Select",
				fieldname: "model",
				options: [{ label: __("Default model"), value: "" }].concat(
					models.map((m) => ({ label: m.model_label, value: m.name }))
				),
				change() {
					me.selected_model = this.get_value();
				},
			},
			render_input: true,
		});
		if (defaultModel) this.model_control.set_value(defaultModel.name);
	}

	render_knowledge_chips() {
		const $wrap = this.page.main.find(".ai-composer-kbs").empty();
		if (!this.context.knowledge_bases.length) return;

		$wrap.append(`<span class="ai-kb-label text-muted small">${__("Knowledge")}:</span>`);
		this.context.knowledge_bases.forEach((kb) => {
			const $chip = $(`
				<button class="ai-kb-chip" data-kb="${frappe.utils.escape_html(kb.name)}">
					${frappe.utils.escape_html(kb.knowledge_base_name)}
					<span class="ai-kb-count">${kb.document_count || 0}</span>
				</button>
			`);
			$chip.on("click", () => {
				$chip.toggleClass("active");
				this.selected_kbs = $wrap
					.find(".ai-kb-chip.active")
					.map((_, el) => $(el).data("kb"))
					.get();
			});
			$wrap.append($chip);
		});
	}

	render_suggestions() {
		const suggestions = [
			__("Summarise the documents I uploaded this week"),
			__("What does our refund policy say?"),
			__("List the open items that need my attention"),
		];
		const $wrap = this.page.main.find(".ai-suggestions").empty();
		suggestions.forEach((text) => {
			$(`<button class="ai-suggestion">${frappe.utils.escape_html(text)}</button>`)
				.on("click", () => {
					this.$input.val(text).trigger("input").focus();
				})
				.appendTo($wrap);
		});
	}

	async refresh_conversations() {
		try {
			this.conversations = await frappe.xcall("ai_fr_hg.api.chat.list_conversations", {
				limit: 100,
			});
		} catch (error) {
			this.conversations = [];
		}
		this.render_conversations(this.conversations);
	}

	render_conversations(list) {
		if (!list || !list.length) {
			this.$sidebar.html(
				`<div class="ai-sidebar-empty text-muted small">${__(
					"No conversations yet"
				)}</div>`
			);
			return;
		}

		this.$sidebar.html(
			list
				.map((conv) => {
					const active = conv.name === this.conversation ? " active" : "";
					const when = conv.last_message_on
						? frappe.datetime.comment_when(conv.last_message_on)
						: "";
					return `
						<div class="ai-conversation-item${active}" data-name="${conv.name}">
							<div class="ai-conv-title">
								${conv.pinned ? frappe.utils.icon("pin", "xs") : ""}
								${frappe.utils.escape_html(conv.title || __("Untitled"))}
							</div>
							<div class="ai-conv-meta text-muted">
								<span>${conv.message_count || 0} ${__("messages")}</span>
								<span>${when}</span>
							</div>
						</div>`;
				})
				.join("")
		);
	}

	filter_conversations(term) {
		const needle = (term || "").toLowerCase();
		this.render_conversations(
			(this.conversations || []).filter((conv) =>
				(conv.title || "").toLowerCase().includes(needle)
			)
		);
	}

	new_conversation() {
		this.conversation = null;
		this.$title.text(__("New Conversation"));
		this.$meta.text("");
		this.$messages.html(`
			<div class="ai-empty-state">
				<div class="ai-empty-icon">${frappe.utils.icon("message", "lg")}</div>
				<h4>${__("Local AI Assistant")}</h4>
				<p class="text-muted">${__("Everything runs on this machine.")}</p>
				<div class="ai-suggestions"></div>
			</div>
		`);
		this.render_suggestions();
		this.$sidebar.find(".ai-conversation-item").removeClass("active");
		this.$input.focus();
	}

	async open_conversation(name) {
		this.conversation = name;
		this.$sidebar.find(".ai-conversation-item").removeClass("active");
		this.$sidebar.find(`[data-name="${name}"]`).addClass("active");
		this.$messages.html(`<div class="ai-loading">${__("Loading...")}</div>`);

		const data = await frappe.xcall("ai_fr_hg.api.chat.get_conversation", {
			conversation: name,
		});

		this.$title.text(data.conversation.title || __("Untitled"));
		this.$meta.text(
			`${data.messages.length} ${__("messages")} · ${
				data.conversation.total_tokens || 0
			} ${__("tokens")}`
		);

		this.$messages.empty();
		data.messages.forEach((message) => this.append_message(message));
		this.scroll_to_bottom();
	}

	append_message(message) {
		if (message.role === "Tool") {
			this.append_tool_message(message);
			return;
		}

		const isUser = message.role === "User";
		const rendered = isUser
			? frappe.utils.escape_html(message.content || "").replace(/\n/g, "<br>")
			: frappe.markdown(message.content || "");

		const $el = $(`
			<div class="ai-message ai-message-${message.role.toLowerCase()}">
				<div class="ai-avatar">${isUser ? frappe.utils.icon("user", "sm") : "AI"}</div>
				<div class="ai-body">
					<div class="ai-bubble">${rendered}</div>
					<div class="ai-message-footer"></div>
				</div>
			</div>
		`);

		const $footer = $el.find(".ai-message-footer");

		if (message.citations && message.citations.length) {
			const $cites = $(`<div class="ai-citations"></div>`);
			$cites.append(`<span class="text-muted small">${__("Sources")}:</span>`);
			message.citations.forEach((cite, index) => {
				$cites.append(`
					<button class="ai-citation" data-document="${cite.document}"
						title="${frappe.utils.escape_html(cite.content || "").slice(0, 300)}">
						[${index + 1}] ${frappe.utils.escape_html(cite.document_title || cite.document)}
					</button>
				`);
			});
			$footer.append($cites);
			this.render_context(message.citations);
		}

		if (!isUser) {
			const meta = [];
			if (message.model) meta.push(message.model);
			if (message.total_tokens) meta.push(`${message.total_tokens} ${__("tokens")}`);
			if (message.duration_ms) meta.push(`${(message.duration_ms / 1000).toFixed(1)}s`);

			$footer.append(`
				<div class="ai-message-actions">
					<span class="text-muted small">${meta.join(" · ")}</span>
					<button class="ai-icon-btn ai-copy" title="${__("Copy")}">
						${frappe.utils.icon("copy", "xs")}
					</button>
					<button class="ai-icon-btn ai-feedback ${message.feedback === "Positive" ? "active" : ""}"
						data-message="${message.name}" data-value="Positive" title="${__("Helpful")}">
						${frappe.utils.icon("up", "xs")}
					</button>
					<button class="ai-icon-btn ai-feedback ${message.feedback === "Negative" ? "active" : ""}"
						data-message="${message.name}" data-value="Negative" title="${__("Not helpful")}">
						${frappe.utils.icon("down", "xs")}
					</button>
				</div>
			`);
		}

		this.$messages.find(".ai-empty-state").remove();
		this.$messages.append($el);
	}

	append_tool_message(message) {
		let args = message.tool_arguments;
		try {
			args = JSON.stringify(JSON.parse(args || "{}"));
		} catch (error) {
			// leave as-is
		}
		this.$messages.append(`
			<div class="ai-tool-call">
				${frappe.utils.icon("tool", "xs")}
				<code>${frappe.utils.escape_html(message.tool || "tool")}</code>
				<span class="text-muted small">${frappe.utils.escape_html((args || "").slice(0, 160))}</span>
			</div>
		`);
	}

	render_context(citations) {
		const $body = this.$context.find(".ai-context-body").empty();
		if (!citations || !citations.length) {
			$body.html(`<p class="text-muted small">${__("No sources for this answer.")}</p>`);
			return;
		}

		citations.forEach((cite, index) => {
			$body.append(`
				<div class="ai-context-item">
					<div class="ai-context-item-header">
						<strong>[${index + 1}] ${frappe.utils.escape_html(cite.document_title)}</strong>
						<span class="ai-score">${(cite.score * 100).toFixed(0)}%</span>
					</div>
					${
						cite.heading
							? `<div class="text-muted small">${frappe.utils.escape_html(
									cite.heading
							  )}</div>`
							: ""
					}
					<div class="ai-context-snippet">${frappe.utils.escape_html(
						(cite.content || "").slice(0, 400)
					)}</div>
					<a class="small" href="/app/ai-document/${cite.document}">${__("Open document")}</a>
				</div>
			`);
		});
	}

	get_error_message(error, fallback) {
		return (
			error?.message ||
			error?.exc?.server_messages ||
			error?.server_messages ||
			error?.responseJSON?.exception ||
			fallback ||
			__("The request failed.")
		);
	}

	async send() {
		const message = (this.$input.val() || "").trim();
		if (!message || this.sending) return;

		this.sending = true;
		this.$send.prop("disabled", true);
		this.$input.val("").css("height", "auto");

		this.append_message({ role: "User", content: message, name: "pending" });
		this.scroll_to_bottom();

		const $thinking = $(`
			<div class="ai-message ai-message-assistant ai-thinking">
				<div class="ai-avatar">AI</div>
				<div class="ai-body">
					<div class="ai-bubble">
						<span class="ai-dot"></span><span class="ai-dot"></span><span class="ai-dot"></span>
					</div>
				</div>
			</div>
		`);
		this.$messages.append($thinking);
		this.scroll_to_bottom();

		try {
			const response = await frappe.xcall("ai_fr_hg.api.chat.send_message", {
				message,
				conversation: this.conversation,
				agent: this.selected_agent,
				model: this.selected_model || null,
				knowledge_bases: this.selected_kbs.length ? this.selected_kbs : null,
			});

			$thinking.remove();

			const isNew = !this.conversation;
			this.conversation = response.conversation;

			(response.tool_invocations || []).forEach((invocation) => {
				this.append_tool_message({
					tool: invocation.tool,
					tool_arguments: JSON.stringify(invocation.arguments),
				});
			});

			this.append_message({
				role: "Assistant",
				content: response.answer,
				citations: response.citations,
				name: response.message,
				model: response.model,
				total_tokens: response.total_tokens,
				duration_ms: response.duration_ms,
			});

			if (isNew) this.refresh_conversations();
		} catch (error) {
			$thinking.remove();
			this.$messages.append(`
				<div class="ai-message ai-message-error">
					<div class="ai-avatar">!</div>
					<div class="ai-body">
						<div class="ai-bubble ai-bubble-error">
							${frappe.utils.escape_html(
								this.get_error_message(error, __("The request failed."))
							)}
						</div>
					</div>
				</div>
			`);
		} finally {
			this.sending = false;
			this.$send.prop("disabled", !this.$input.val().trim());
			this.scroll_to_bottom();
			this.$input.focus();
		}
	}

	async send_feedback(message, value, $button) {
		if (!message || message === "pending") return;
		const active = $button.hasClass("active");
		await frappe.xcall("ai_fr_hg.api.chat.submit_feedback", {
			message,
			feedback: active ? "" : value,
		});
		$button.closest(".ai-message-actions").find(".ai-feedback").removeClass("active");
		if (!active) $button.addClass("active");
	}

	attach_document() {
		const me = this;
		if (!this.context.knowledge_bases || !this.context.knowledge_bases.length) {
			frappe.msgprint(__("Create a knowledge base before uploading documents."));
			return;
		}
		new frappe.ui.FileUploader({
			folder: "Home/Attachments",
			on_success(file) {
				frappe.prompt(
					[
						{
							fieldtype: "Link",
							fieldname: "knowledge_base",
							label: __("Knowledge Base"),
							options: "AI Knowledge Base",
							reqd: 1,
							default: (me.context.knowledge_bases[0] || {}).name,
						},
						{
							fieldtype: "Data",
							fieldname: "title",
							label: __("Title"),
							default: file.file_name,
						},
					],
					async (values) => {
						const result = await frappe.xcall(
							"ai_fr_hg.api.knowledge.upload_document",
							{
								file_url: file.file_url,
								knowledge_base: values.knowledge_base,
								title: values.title,
							}
						);
						frappe.show_alert({
							message: __(
								"Processing {0}. You will be notified when it is searchable.",
								[values.title]
							),
							indicator: "blue",
						});
						me.$input
							.val(__("Summarise the document I just uploaded: {0}", [values.title]))
							.trigger("input");
					},
					__("Add to Knowledge Base"),
					__("Upload")
				);
			},
		});
	}

	async summarize() {
		if (!this.conversation) {
			frappe.msgprint(__("Open a conversation first."));
			return;
		}
		frappe.show_alert({ message: __("Summarising..."), indicator: "blue" });
		const result = await frappe.xcall("ai_fr_hg.api.chat.summarize_conversation", {
			conversation: this.conversation,
		});
		frappe.msgprint({
			title: __("Conversation Summary"),
			message: frappe.markdown(result.summary),
			wide: true,
		});
	}

	async archive() {
		if (!this.conversation) return;
		await frappe.xcall("ai_fr_hg.api.chat.archive_conversation", {
			conversation: this.conversation,
		});
		frappe.show_alert({ message: __("Archived"), indicator: "green" });
		this.new_conversation();
		this.refresh_conversations();
	}

	delete_conversation() {
		if (!this.conversation) return;
		frappe.confirm(__("Delete this conversation and all of its messages?"), async () => {
			await frappe.xcall("ai_fr_hg.api.chat.delete_conversation", {
				conversation: this.conversation,
			});
			this.new_conversation();
			this.refresh_conversations();
		});
	}

	render_error(message, action_label, action) {
		this.$messages.html(`
			<div class="ai-empty-state">
				<div class="ai-empty-icon text-danger">${frappe.utils.icon("solid-warning", "lg")}</div>
				<h4>${__("Not ready")}</h4>
				<p class="text-muted">${frappe.utils.escape_html(message)}</p>
				${
					action_label
						? `<button class="btn btn-primary btn-sm ai-error-action">${action_label}</button>`
						: ""
				}
			</div>
		`);
		if (action) this.$messages.find(".ai-error-action").on("click", action);
	}

	scroll_to_bottom() {
		this.$messages.scrollTop(this.$messages[0].scrollHeight);
	}
}
