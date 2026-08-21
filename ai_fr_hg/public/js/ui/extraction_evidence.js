// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Pure summarizer for durable extraction evidence shown on AI Document.
 * Does not render HTML; Desk form scripts escape and display the result.
 */

export function parseExtractionEvidence(raw) {
	if (!raw) return {};
	if (typeof raw === "object") return raw;
	try {
		const parsed = JSON.parse(raw);
		return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
}

export function summarizeExtractionEvidence(raw) {
	const evidence = parseExtractionEvidence(raw);
	const detector = evidence.detector || {};
	const structure = evidence.structure || {};
	const provenance = evidence.provenance || {};
	const embedded = Array.isArray(evidence.embedded_objects) ? evidence.embedded_objects : [];
	const mismatch = Boolean(detector.mismatch);
	const empty = !evidence.reader && !detector.family && !provenance.bytes;
	return {
		kind: empty ? "empty" : mismatch ? "mismatch" : "aligned",
		empty,
		reader: evidence.reader || "",
		family: detector.family || "",
		magic: detector.magic || "",
		extension: detector.extension || "",
		mismatch,
		reason: detector.reason || "",
		blocks: Number(structure.block_count || 0),
		kinds: structure.kinds && typeof structure.kinds === "object" ? structure.kinds : {},
		embedded: embedded.length,
		bytes: Number(provenance.bytes || 0),
		checksum: provenance.checksum_sha256 || "",
		word_count: Number(provenance.word_count || 0),
		page_count: Number(provenance.page_count || 0),
	};
}
