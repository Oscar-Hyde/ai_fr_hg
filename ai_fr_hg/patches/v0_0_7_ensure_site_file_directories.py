# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Restore native upload directories omitted by a site restore/reinstall."""

from ai_fr_hg.install import ensure_site_file_directories


def execute():
	ensure_site_file_directories()
