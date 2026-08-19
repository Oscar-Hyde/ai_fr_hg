// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Guard Frappe's File form preview.
 *
 * Core `file.js` calls `preview_file` on refresh and does
 * `frm.doc.file_type.toLowerCase()` with no fallback. Folders and new File
 * drafts have no `file_type`, so opening them from the AI Document tree
 * throws `TypeError: can't access property "toLowerCase"`.
 *
 * This script is loaded via `doctype_js` for the File DocType only, but on
 * Desk navigation Frappe may re-evaluate the module in a context where
 * `frappe.ui.form` is not yet available (SPA boot / cached Desk). Guard
 * against that and defer registration so returning to Desk never throws.
 */
(() => {
	if (typeof frappe === "undefined") return;

	function register() {
		if (!frappe.ui || !frappe.ui.form || typeof frappe.ui.form.on !== "function") return false;
		// Avoid double-registration when Desk is revisited and the module is
		// re-executed from cache.
		if (frappe.ui.form.__ai_file_guard__) return true;
		frappe.ui.form.__ai_file_guard__ = true;

		frappe.ui.form.on("File", {
			setup(frm) {
				try {
					ensure_file_type(frm);
				} catch (_error) {
					// Never let a preview guard break the form lifecycle.
				}
			},
			refresh(frm) {
				try {
					ensure_file_type(frm);
				} catch (_error) {
					// Never let a preview guard break the form lifecycle.
				}
			},
		});
		return true;
	}

	if (!register()) {
		let attempts = 20;
		const timer = setInterval(() => {
			if (register() || --attempts <= 0) clearInterval(timer);
		}, 250);
	}
})();

function ensure_file_type(frm) {
	if (!frm?.doc || frm.doc.file_type) return;

	const from_name = extension_of(frm.doc.file_name);
	const from_url = extension_of(frm.doc.file_url);
	frm.doc.file_type = from_name || from_url || (frm.doc.is_folder ? "folder" : "");
}

function extension_of(value) {
	const name = String(value || "").split("?")[0];
	if (!name.includes(".")) return "";
	const ext = name.split(".").pop() || "";
	return /^[A-Za-z0-9]{1,12}$/.test(ext) ? ext : "";
}
