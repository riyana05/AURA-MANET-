"""
embeddings.py — Text embedders for the RAG pipeline.

* :class:`SentenceTransformerEmbedder` — the real embedder
  (``all-MiniLM-L6-v2`` by default; downloaded on first use).
* :class:`HashingEmbedder` — dependency-free bag-of-words hashing.  Used by the
  unit tests and as an offline fallback; much weaker semantically.

Both return L2-normalised ``float32`` matrices so FAISS inner product equals
cosine similarity.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype="float32")
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class SentenceTransformerEmbedder:
    """Wraps ``sentence_transformers.SentenceTransformer`` (loaded lazily)."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.name = f"sentence-transformers:{model_name}"
        self._model = None
        self.dim = 0

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s", self.model_name)
            model = SentenceTransformer(self.model_name)
            get_dim = getattr(model, "get_embedding_dimension", None) or getattr(
                model, "get_sentence_embedding_dimension"
            )
            self.dim = int(get_dim())
            self._model = model
        return self._model

    def ensure_ready(self) -> None:
        """Load the model now (raises if it cannot be downloaded / loaded)."""
        self._load()

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        vecs = model.encode(texts, batch_size=32, show_progress_bar=False)
        return _normalise(vecs)


class HashingEmbedder:
    """Signed feature hashing of word unigrams + bigrams."""

    _token = re.compile(r"[a-z0-9_]+")

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"hashing:{dim}"

    def _features(self, text: str) -> list[str]:
        words = self._token.findall(text.lower())
        return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]

    def embed(self, texts: list[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            for feat in self._features(text):
                h = int.from_bytes(hashlib.md5(feat.encode()).digest()[:8], "little")
                mat[row, h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return _normalise(mat)


def make_embedder(kind: str, model_name: str) -> Embedder:
    """Factory used by the store: ``kind`` is ``sentence-transformers`` or ``hashing``."""
    if kind == "hashing":
        return HashingEmbedder()
    return SentenceTransformerEmbedder(model_name)
