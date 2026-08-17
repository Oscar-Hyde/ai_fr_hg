# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure, database-free helpers for the Learning Loop.

All scoring, deduplication, classification and prompt-block formatting for
learned knowledge lives here so it can be unit-tested without Frappe, a
database or a model runtime. The orchestration in :mod:`ai_fr_hg.ai.learning`
imports these and wires them to the platform (DocTypes, governance, audit).
"""

import re

#: Token regex for memory text. Unlike the document keyword pattern it does
#: NOT treat `.` or `/` as part of a token, so ``"days."`` tokenises as
#: ``"days"`` and matches the query term ``day`` instead of never matching.
#: Hyphenated and apostrophe forms (``"end-of-day"``, ``"manager's"``) stay
#: intact.
TOKEN = re.compile(r"\w+(?:[-']\w+)*")

#: Words that carry no retrieval signal. Filtering them out before scoring
#: stops a memory that merely shares "the" or "is" from being considered
#: relevant to an unrelated question.
STOPWORDS = frozenset(
	{
		"a", "about", "above", "after", "again", "against", "all", "am", "an",
		"and", "any", "are", "as", "at", "be", "because", "been", "before",
		"being", "below", "between", "both", "but", "by", "can", "did", "do",
		"does", "doing", "down", "during", "each", "few", "for", "from",
		"further", "had", "has", "have", "having", "he", "her", "here",
		"hers", "herself", "him", "himself", "his", "how", "i", "if", "in",
		"into", "is", "it", "its", "itself", "just", "me", "more", "most",
		"my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once",
		"only", "or", "other", "our", "ours", "ourselves", "out", "over",
		"own", "same", "she", "should", "so", "some", "such", "than", "that",
		"the", "their", "theirs", "them", "themselves", "then", "there",
		"these", "they", "this", "those", "through", "to", "too", "under",
		"until", "up", "very", "was", "we", "were", "what", "when", "where",
		"which", "while", "who", "whom", "why", "will", "with", "you", "your",
		"yours", "yourself", "yourselves",
	}
)


def _stem(token: str) -> str:
	"""Light suffix stemming so ``refunds`` matches ``refund``, ``using`` ``use``.

	Deliberately crude (no dictionary) - good enough to lift recall for the
	plural / progressive forms that dominate enterprise prose, without the
	noise a full stemmer can introduce.
	"""
	low = token.lower()
	if len(low) > 4 and low.endswith("ing"):
		return low[:-3]
	if len(low) > 4 and low.endswith("ed"):
		return low[:-2]
	if len(low) > 3 and low.endswith("s") and not low.endswith("ss"):
		return low[:-1]
	return low

#: A fact/preference/feedback candidate that, once approved, becomes an
#: ``AI Memory``.
MEMORY_TYPES = {"Fact", "Preference", "Feedback"}
#: A procedure candidate that, once approved, becomes an ``AI Skill``.
SKILL_TYPES = {"Instruction"}

VALID_CANDIDATE_TYPES = MEMORY_TYPES | SKILL_TYPES | {"Document"}

#: Heuristic trigger words used to classify a free-form teaching as an
#: instruction rather than a bare fact. Instructions tend to be prescriptive.
_INSTRUCTION_MARKERS = (
	" always ",
	" never ",
	" must ",
	" should ",
	" do not ",
	" don't ",
	" ensure ",
	" use ",
	" whenever ",
	" step 1",
	" step 2",
	" first ",
	" then ",
)

_PREFERENCE_MARKERS = (
	" i prefer ",
	" i want ",
	" please ",
	" prefer ",
	" i like ",
	" use ",
)

#: Jaccard overlap at or above which two texts are treated as duplicates.
DUPLICATE_THRESHOLD = 0.85


def tokenize(text: str | None) -> set[str]:
	"""Return the set of meaningful stemmed tokens in `text`.

	Stop words are dropped and the remainder lightly stemmed, so relevance and
	deduplication are not fooled by filler words or plural verb forms.
	"""
	return {_stem(token) for token in TOKEN.findall((text or "").lower()) if token not in STOPWORDS}


def score_relevance(query: str | None, text: str | None) -> float:
	"""Score how relevant `text` is to `query`, from 0 (none) to 1.

	Combines query-term *coverage* (did we cover the important words of the
	query?) with set *similarity* (Jaccard). Coverage dominates because a
	memory that contains the distinctive token of a question is what matters.
	"""
	query_tokens = tokenize(query)
	text_tokens = tokenize(text)
	if not query_tokens or not text_tokens:
		return 0.0

	overlap = query_tokens & text_tokens
	if not overlap:
		return 0.0

	coverage = len(overlap) / len(query_tokens)
	jaccard = len(overlap) / len(query_tokens | text_tokens)
	return coverage * 0.7 + jaccard * 0.3


def rank_memories(query: str | None, memories: list[dict]) -> list[dict]:
	"""Order `memories` (dicts with a ``content`` key) by relevance to `query`.

	Memories with zero overlap are dropped, so a turn only spends prompt space
	on knowledge that plausibly applies. Ties are broken by ``name`` for a
	stable, deterministic order (important for testing and caching).
	"""
	scored: list[tuple[float, dict]] = []
	for memory in memories:
		content = memory.get("content") or ""
		score = score_relevance(query, content)
		if score > 0:
			scored.append((score, memory))
	scored.sort(key=lambda row: (-row[0], row[1].get("name") or ""))
	return [memory for _, memory in scored]


def dedupe_key(text: str | None) -> str:
	"""A canonical, whitespace-normalised key for duplicate detection."""
	return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def is_near_duplicate(first: str | None, second: str | None, threshold: float = DUPLICATE_THRESHOLD) -> bool:
	"""True when two texts are token-set duplicates above `threshold`.

	Used to stop the learning loop from re-adding a memory that already
	exists (or a skill that is already installed), and to flag candidates that
	*contradict* an existing memory for human review.
	"""
	tokens_first = tokenize(first)
	tokens_second = tokenize(second)
	if not tokens_first or not tokens_second:
		return False
	jaccard = len(tokens_first & tokens_second) / len(tokens_first | tokens_second)
	return jaccard >= threshold


def classify_candidate(text: str | None) -> str:
	"""Best-effort classification of free-form teaching into a candidate type.

	Pure heuristic - the administrator can always override the type on the
	record. Instruction-like phrasing becomes an ``Instruction`` (a skill),
	preference phrasing a ``Preference``, everything else a ``Fact``.
	"""
	low = (text or "").lower().strip()
	if not low:
		return "Fact"

	has_steps = bool(re.search(r"(^|\n)\s*\d+[\.\)]", low))
	if has_steps or low.startswith(("always ", "never ", "when ", "if ")):
		return "Instruction"
	if any(marker in low for marker in _INSTRUCTION_MARKERS):
		return "Instruction"
	if any(marker in low for marker in _PREFERENCE_MARKERS):
		return "Preference"
	return "Fact"


def memory_to_dict(memory) -> dict:
	"""Render an ``AI Memory`` document into a plain dict for scoring/format."""
	return {
		"name": memory.name,
		"content": memory.content or "",
		"memory_type": memory.memory_type or "Fact",
		"scope": memory.scope or "Global",
		"scope_value": memory.scope_value,
		"confidence": memory.confidence or 0,
	}


def skill_to_dict(skill) -> dict:
	"""Render an ``AI Skill`` document into a plain dict for formatting."""
	return {
		"name": skill.skill_name or skill.name,
		"description": skill.description or "",
		"instructions": skill.instructions or "",
		"skill_type": skill.skill_type or "Procedural",
		"scope": skill.scope or "Global",
		"scope_value": skill.scope_value,
	}


def build_memory_block(memories: list[dict], max_characters: int | None = None) -> str:
	"""Format ranked memories into a numbered, citable prompt block."""
	if not memories:
		return ""
	lines: list[str] = []
	used = 0
	for index, memory in enumerate(memories, start=1):
		content = (memory.get("content") or "").strip()
		if not content:
			continue
		memory_type = memory.get("memory_type") or "memory"
		line = f"[{index}] ({memory_type}) {content}"
		if max_characters and used + len(line) > max_characters:
			break
		lines.append(line)
		used += len(line)
	return "\n".join(lines)


def build_skill_block(skills: list[dict], max_characters: int | None = None) -> str:
	"""Format enabled skills into a numbered, citable prompt block."""
	if not skills:
		return ""
	lines: list[str] = []
	used = 0
	for index, skill in enumerate(skills, start=1):
		instructions = (skill.get("instructions") or "").strip()
		if not instructions:
			continue
		name = skill.get("name") or f"Skill {index}"
		description = (skill.get("description") or "").strip()
		header = f"[{index}] SKILL: {name}"
		if description:
			header += f" - {description}"
		line = f"{header}\n{instructions}"
		if max_characters and used + len(line) > max_characters:
			break
		lines.append(line)
		used += len(line)
	return "\n\n".join(lines)
