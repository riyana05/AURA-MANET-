"""
store.py — RAG ingestion and retrieval (FAISS).

Ingestion
    Every ``.txt`` / ``.md`` file in the knowledge-base folder (recursively),
    every ``.pdf`` (research papers; needs ``pypdf``), and the fault-case log
    ``fault_cases.jsonl`` are split into overlapping chunks, embedded and
    stored in a FAISS ``IndexFlatIP`` under ``RAG_INDEX_DIR``::

        rag/index/index.faiss   vectors
        rag/index/chunks.json   chunk text + source
        rag/index/meta.json     embedder name + fingerprint of the input files

Retrieval
    :meth:`Retriever.retrieve` embeds the query and returns the top-k chunks
    as :class:`schemas.RetrievedChunk`.  If the index is missing, stale (the
    knowledge files changed) or was built with another embedder, it is rebuilt
    automatically.

Fault cases
    :func:`append_fault_case` records a diagnosed case **only after its action
    was actually executed and verified**, so retrieved "previous cases" are
    real outcomes, not unverified LLM opinions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import faiss
import numpy as np

from rag.embeddings import Embedder, HashingEmbedder, make_embedder
from schemas import RetrievedChunk

logger = logging.getLogger(__name__)

FAULT_CASES_FILE = "fault_cases.jsonl"
TEXT_SUFFIXES = {".txt", ".md"}


# --------------------------------------------------------------------------- #
# Loading and chunking                                                         #
# --------------------------------------------------------------------------- #


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("Skipping %s: install 'pypdf' to ingest PDF papers", path.name)
        return ""
    try:
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read %s: %s", path.name, exc)
        return ""


def _fault_case_text(case: dict) -> str:
    anomalies = ", ".join(sorted({a.get("type", "?") for a in case.get("anomalies", [])}))
    verification = case.get("verification", {})
    return (
        f"Previous fault case {case.get('case_id')} (run {case.get('simulation_id')}, "
        f"t={case.get('time')}s). Anomalies: {anomalies}. "
        f"Diagnosed root cause: {case.get('root_cause')}. "
        f"Action taken: {case.get('action', {}).get('action_type')} "
        f"{json.dumps(case.get('action', {}).get('parameters', {}))}. "
        f"Verified outcome: {verification.get('status')} "
        f"(PDR change {verification.get('change_pct', {}).get('pdr')} %)."
    )


def load_documents(kb_dir: Path) -> list[tuple[str, str]]:
    """Return ``(source, text)`` pairs for every knowledge file."""
    docs: list[tuple[str, str]] = []
    for path in sorted(kb_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(kb_dir))
        if path.name == FAULT_CASES_FILE:
            for line in path.read_text().splitlines():
                if line.strip():
                    case = json.loads(line)
                    docs.append((f"{rel}:{case.get('case_id')}", _fault_case_text(case)))
        elif path.suffix.lower() in TEXT_SUFFIXES:
            docs.append((rel, path.read_text(encoding="utf-8", errors="replace")))
        elif path.suffix.lower() == ".pdf":
            text = _read_pdf(path)
            if text.strip():
                docs.append((rel, text))
    return docs


_HEADING = re.compile(r"^(#{1,6} .+|[A-Z0-9][A-Z0-9 .,:&()'/-]{8,})$", re.MULTILINE)


def _sections(text: str) -> list[tuple[str, str]]:
    """Split on markdown headings; returns ``(heading, body)`` pairs."""
    lines = text.splitlines()
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        if line.startswith("#"):
            out.append((line.lstrip("#").strip(), []))
        else:
            out[-1][1].append(line)
    return [(h, "\n".join(body).strip()) for h, body in out if "\n".join(body).strip()]


def chunk_text(text: str, max_chars: int, overlap: int) -> list[tuple[str, str]]:
    """Chunk *text* into ``(heading, chunk)`` pieces of at most ~``max_chars``."""
    chunks: list[tuple[str, str]] = []
    for heading, body in _sections(text):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        current = ""
        for para in paragraphs:
            while len(para) > max_chars:  # very long paragraph: hard split
                head, para = para[:max_chars], para[max_chars - overlap :]
                if current:
                    chunks.append((heading, current))
                    current = ""
                chunks.append((heading, head))
            if current and len(current) + len(para) + 2 > max_chars:
                chunks.append((heading, current))
                current = current[-overlap:] + "\n\n" + para if overlap else para
            else:
                current = f"{current}\n\n{para}" if current else para
        if current:
            chunks.append((heading, current))
    return chunks


def _fingerprint(kb_dir: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(kb_dir.rglob("*")):
        if path.is_file():
            h.update(str(path.relative_to(kb_dir)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Index build / load                                                           #
# --------------------------------------------------------------------------- #


def build_index(
    kb_dir: Path,
    index_dir: Path,
    embedder: Embedder,
    chunk_chars: int = 900,
    overlap: int = 150,
) -> int:
    """Ingest *kb_dir* into a FAISS index in *index_dir*.  Returns chunk count."""
    docs = load_documents(kb_dir)
    if not docs:
        raise RuntimeError(f"No knowledge files found in {kb_dir}")

    chunks: list[dict] = []
    for source, text in docs:
        stem = Path(source.split(":")[0]).stem
        suffix = source.split(":")[1] if ":" in source else ""
        for i, (heading, piece) in enumerate(chunk_text(text, chunk_chars, overlap)):
            cid = f"{stem}{'-' + suffix if suffix else ''}#{i}"
            title = f"{heading}\n" if heading else ""
            chunks.append({"chunk_id": cid, "source": source, "heading": heading,
                           "text": f"{title}{piece}"})

    vectors = embedder.embed([c["text"] for c in chunks])
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)

    index_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_dir / "index.faiss"))
    (index_dir / "chunks.json").write_text(json.dumps(chunks, indent=1))
    (index_dir / "meta.json").write_text(
        json.dumps(
            {
                "embedder": embedder.name,
                "dim": int(vectors.shape[1]),
                "fingerprint": _fingerprint(kb_dir),
                "chunks": len(chunks),
                "built_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=1,
        )
    )
    logger.info("RAG index built: %d chunks from %d documents", len(chunks), len(docs))
    return len(chunks)


class Retriever:
    """Top-k semantic search over the knowledge base."""

    def __init__(
        self,
        kb_dir: Path,
        index_dir: Path,
        embedder: Embedder,
        chunk_chars: int = 900,
        overlap: int = 150,
    ):
        self.kb_dir = Path(kb_dir)
        self.index_dir = Path(index_dir)
        self.embedder = embedder
        self.chunk_chars = chunk_chars
        self.overlap = overlap
        self._index = None
        self._chunks: list[dict] = []
        self.fallback_reason: str | None = None

    @classmethod
    def from_settings(cls, cfg) -> "Retriever":
        return cls(
            cfg.RAG_KB_DIR,
            cfg.RAG_INDEX_DIR,
            make_embedder(cfg.RAG_EMBEDDER, cfg.RAG_EMBED_MODEL),
            cfg.RAG_CHUNK_CHARS,
            cfg.RAG_CHUNK_OVERLAP,
        )

    # ------------------------------------------------------------------ #

    def _stale(self) -> bool:
        meta_path = self.index_dir / "meta.json"
        if not (meta_path.exists() and (self.index_dir / "index.faiss").exists()):
            return True
        meta = json.loads(meta_path.read_text())
        return meta.get("embedder") != self.embedder.name or meta.get(
            "fingerprint"
        ) != _fingerprint(self.kb_dir)

    def rebuild(self) -> int:
        n = build_index(self.kb_dir, self.index_dir, self.embedder, self.chunk_chars, self.overlap)
        self._index = None
        return n

    def _ensure_embedder(self) -> None:
        """Fall back to the hashing embedder if the real model cannot load."""
        ready = getattr(self.embedder, "ensure_ready", None)
        if ready is None:
            return
        try:
            ready()
        except Exception as exc:  # noqa: BLE001 - offline, auth error, ...
            self.fallback_reason = f"{self.embedder.name} unavailable ({type(exc).__name__}: {exc})"
            logger.warning("RAG: %s; falling back to hashing embedder", self.fallback_reason)
            self.embedder = HashingEmbedder()

    @property
    def embedder_name(self) -> str:
        return self.embedder.name

    def _ensure_loaded(self) -> None:
        if self._index is not None:
            return
        self._ensure_embedder()
        if self._stale():
            self.rebuild()
        self._index = faiss.read_index(str(self.index_dir / "index.faiss"))
        self._chunks = json.loads((self.index_dir / "chunks.json").read_text())

    def retrieve(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        """Return the *k* chunks most similar to *query*."""
        self._ensure_loaded()
        assert self._index is not None
        k = min(k, len(self._chunks))
        if k == 0:
            return []
        vec = self.embedder.embed([query])
        scores, ids = self._index.search(np.asarray(vec, dtype="float32"), k)
        out: list[RetrievedChunk] = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:
                continue
            c = self._chunks[int(idx)]
            out.append(
                RetrievedChunk(
                    chunk_id=c["chunk_id"], source=c["source"], text=c["text"], score=float(score)
                )
            )
        return out


def append_fault_case(kb_dir: Path, state: dict) -> dict | None:
    """Store a verified case in ``fault_cases.jsonl`` (the index rebuilds on next use).

    Only cases whose action was really executed and verified are stored.
    Returns the stored case, or ``None`` if the state does not qualify.
    """
    execution = state.get("execution_result") or {}
    verification = state.get("verification_result") or {}
    if not execution.get("executed") or verification.get("mode") != "counterfactual_rerun":
        return None
    if verification.get("status") not in {"success", "no_improvement", "degraded"}:
        return None
    case_id = hashlib.sha1(
        f"{state.get('simulation_id')}|{state.get('current_time')}|"
        f"{json.dumps(state.get('recovery_action'), sort_keys=True)}".encode()
    ).hexdigest()[:10]
    case = {
        "case_id": case_id,
        "simulation_id": state.get("simulation_id"),
        "time": state.get("current_time"),
        "anomalies": [{"type": a.get("type"), "severity": a.get("severity")}
                      for a in state.get("anomalies", [])],
        "root_cause": (state.get("diagnosis") or {}).get("root_cause"),
        "action": state.get("recovery_action"),
        "verification": {k: verification.get(k) for k in ("status", "change_pct", "mode")},
    }
    path = Path(kb_dir) / FAULT_CASES_FILE
    existing = path.read_text() if path.exists() else ""
    if f'"case_id": "{case_id}"' in existing:
        return case
    with path.open("a") as fh:
        fh.write(json.dumps(case) + "\n")
    return case
