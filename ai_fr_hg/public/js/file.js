// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Guard Frappe's File form preview.
 *
 * Core `file.js` calls `preview_file` on refresh and does
 * `frm.doc.file_type.toLowerCase()` with no fallback. Folders and new File
 * drafts have no `file_type`, so opening them from the AI Document tree
 * throws `TypeError: can't access property "toLowerCase"`.
 */
frappe.ui.form.on("File", {
	setup(frm) {
		ensure_file_type(frm);
	},

	refresh(frm) {
		ensure_file_type(frm);
	},
});

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
