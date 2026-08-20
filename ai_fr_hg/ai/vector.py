# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Native vector store.

Embeddings live in the `AI Document Chunk` DocType as base64-encoded float32
buffers, so the platform needs no external vector database. Vectors are stored
pre-normalised, which reduces cosine similarity to a dot product and lets a
pure-Python implementation stay fast; numpy is used automatically when present.
"""

import base64
import math
import struct

try:
	import numpy as _np
except ImportError:  # numpy is optional
	_np = None


def encode_vector(vector: list[float]) -> str:
	"""Pack a float vector into a compact base64 float32 buffer."""
	if not vector:
		return ""
	if _np is not None:
		buffer = _np.asarray(vector, dtype=_np.float32).tobytes()
	else:
		buffer = struct.pack(f"<{len(vector)}f", *vector)
	return base64.b64encode(buffer).decode("ascii")


def decode_vector(encoded: str | None) -> list[float]:
	"""Unpack a base64 float32 buffer back into a list of floats."""
	if not encoded:
		return []
	try:
		buffer = base64.b64decode(encoded)
	except Exception:
		return []
	count = len(buffer) // 4
	if not count:
		return []
	if _np is not None:
		return _np.frombuffer(buffer, dtype=_np.float32).tolist()
	return list(struct.unpack(f"<{count}f", buffer[: count * 4]))


def norm(vector: list[float]) -> float:
	"""Euclidean length of a vector."""
	if not vector:
		return 0.0
	if _np is not None:
		# Normalisation happens before float32 storage. Keep float64 precision here
		# so large but finite provider values do not overflow during validation.
		return float(_np.hypot.reduce(_np.asarray(vector, dtype=_np.float64)))
	# ``hypot`` scales intermediate values and avoids overflow for otherwise
	# finite provider output where a naive sum-of-squares would become infinity.
	return math.hypot(*vector)


def normalize(vector: list[float]) -> list[float]:
	"""Scale a vector to unit length so cosine similarity is a dot product."""
	length = norm(vector)
	if not length:
		return list(vector)
	if _np is not None:
		return (_np.asarray(vector, dtype=_np.float64) / length).tolist()
	return [value / length for value in vector]


def dot(a: list[float], b: list[float]) -> float:
	"""Dot product of two equal-length vectors."""
	if not a or not b:
		return 0.0
	if len(a) != len(b):
		# Mismatched dimensions mean the chunks were embedded by different models.
		return 0.0
	if _np is not None:
		return float(_np.dot(_np.asarray(a, dtype=_np.float32), _np.asarray(b, dtype=_np.float32)))
	return sum(x * y for x, y in zip(a, b, strict=True))


def cosine_similarity(a: list[float], b: list[float]) -> float:
	"""Cosine similarity of two vectors, safe against zero vectors."""
	length_a, length_b = norm(a), norm(b)
	if not length_a or not length_b:
		return 0.0
	return dot(a, b) / (length_a * length_b)


def score_pairs(query_vector: list[float], candidates: list[tuple[str, list[float]]]) -> list[tuple[str, float]]:
	"""Score every compatible candidate. Incompatible dimensions are omitted.

	Unlike :func:`rank`, this does not truncate: callers that page a corpus
	must see every comparable row so a relevant vector beyond an old 200-row
	boundary cannot disappear.
	"""
	if not query_vector or not candidates:
		return []

	query = normalize(query_vector)
	dimensions = len(query)
	usable = [(key, vec) for key, vec in candidates if len(vec) == dimensions]
	if not usable:
		return []

	if _np is not None and len(usable) > 32:
		matrix = _np.asarray([vec for _, vec in usable], dtype=_np.float32)
		scores = matrix @ _np.asarray(query, dtype=_np.float32)
		return [(usable[i][0], float(scores[i])) for i in range(len(usable))]

	return [(key, dot(query, vector)) for key, vector in usable]


def rank(query_vector: list[float], candidates: list[tuple[str, list[float]]], top_k: int = 10):
	"""Score candidates against the query and return the best `top_k`.

	`candidates` is a list of `(identifier, vector)` pairs, all expected to be
	unit length. Returns `(identifier, score)` pairs sorted best first.
	"""
	scored = score_pairs(query_vector, candidates)
	if not scored:
		return []
	scored.sort(key=lambda row: row[1], reverse=True)
	return scored[:top_k]
