# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Prepare legacy Long Int columns for Frappe v17's supported numeric types.

Frappe v17 removed the ``Long Int`` DocField type.  Older site databases can
contain blank or non-numeric values in columns that were created while that
unsupported type was in use.  MariaDB refuses to alter such a column to the
v17 ``Float`` representation under strict SQL mode.  Run before model sync,
normalising only invalid counter values to their deterministic zero default.
"""

import frappe

# These are application-owned counter/size fields. They are intentionally
# constants, rather than request-derived identifiers, before being interpolated
# into DDL-adjacent SQL.
LEGACY_FIELDS = (
	("AI Agent", "total_tokens"),
	("AI Model", "size_bytes"),
	("AI Model", "total_tokens"),
	("AI Model Version", "size_bytes"),
	("AI Document", "file_size"),
	("AI Document", "character_count"),
	("AI Knowledge Base", "total_characters"),
	("AI Usage Snapshot", "total_tokens"),
)


def execute():
	for doctype, fieldname in LEGACY_FIELDS:
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, fieldname):
			continue

		# Counters and byte sizes are non-negative integral values.  Preserve
		# valid values and make legacy blanks, NULLs and malformed strings an
		# explicit zero before Frappe changes the column type during model sync.
		# Both identifiers come exclusively from LEGACY_FIELDS above; SQL values
		# are not accepted from requests or stored records in this pre-model patch.
		frappe.db.sql(  # nosemgrep
			f"""
			UPDATE `tab{doctype}`
			SET `{fieldname}` = CASE
				WHEN CAST(`{fieldname}` AS CHAR) REGEXP '^[0-9]+$'
					THEN CAST(`{fieldname}` AS DECIMAL(21, 0))
				ELSE 0
			END
			WHERE `{fieldname}` IS NULL
				OR CAST(`{fieldname}` AS CHAR) NOT REGEXP '^[0-9]+$'
			"""
		)
