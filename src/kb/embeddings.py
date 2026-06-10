"""Embedding-based vector index for semantic search."""

import json
import os
import sqlite3
import urllib.request
from typing import Protocol

EMBEDDINGS_FILENAME = "embeddings.npz"


def embeddings_path(root: str) -> str:
    return os.path.join(root, ".kb", EMBEDDINGS_FILENAME)


class EmbeddingEngine(Protocol):
    """Protocol for embedding backends."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...

    @property
    def dim(self) -> int:
        ...


class OpenAIEngine:
    """Generate embeddings via the OpenAI API (text-embedding-3-small by default)."""

    def __init__(self, model: str = "text-embedding-3-small", api_key: str | None = None,
                 base_url: str | None = None):
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            raise RuntimeError("dim unknown until first embed_texts call")
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps({
            "model": self._model,
            "input": texts,
        }).encode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        url = f"{self._base_url}/embeddings"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        embeddings = [d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"])]
        self._dim = len(embeddings[0])
        return embeddings


class LocalHashEngine:
    """Deterministic hash-based embeddings for offline use (no external deps).

    Not semantically meaningful but provides a reproducible baseline that
    exercises the same storage and search paths as real embeddings.
    """

    def __init__(self, dim: int = 256):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        results = []
        for text in texts:
            vec = [0.0] * self._dim
            # Use overlapping 4-gram hashing to spread signal across dimensions
            words = text.lower().split()
            for i in range(len(words)):
                gram = " ".join(words[max(0, i - 1):i + 2])
                h = int(hashlib.sha256(gram.encode()).hexdigest(), 16)
                idx = h % self._dim
                vec[idx] += 1.0
            # L2 normalize
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            results.append(vec)
        return results


def _get_engine(engine: str, **kwargs) -> EmbeddingEngine:
    if engine == "openai":
        return OpenAIEngine(**kwargs)
    if engine == "local":
        return LocalHashEngine(**kwargs)
    raise ValueError(f"Unknown embedding engine: {engine!r}. Use 'openai' or 'local'.")


def build_embeddings(root: str, engine: str = "openai", **kwargs) -> dict:
    """Build embedding vectors for all indexed documents."""
    import numpy as np

    db_path = os.path.join(root, ".kb", "index.db")
    if not os.path.exists(db_path):
        raise FileNotFoundError("No index found. Run 'kb index' first.")

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, title, content FROM docs").fetchall()
    conn.close()

    if not rows:
        return {"docs_vectorized": 0, "engine": engine}

    eng = _get_engine(engine, **kwargs)
    doc_ids = [r[0] for r in rows]
    texts = [f"{r[1]} {r[2]}" for r in rows]

    # Batch in groups of 100 to respect API limits
    batch_size = 100
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        all_embeddings.extend(eng.embed_texts(batch))

    dim = eng.dim
    mat = np.array(all_embeddings, dtype=np.float32).reshape(len(doc_ids), dim)

    # L2 normalize rows
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms

    path = embeddings_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, ids=np.array(doc_ids), vectors=mat, dim=np.array([dim]))

    return {"docs_vectorized": len(doc_ids), "engine": engine, "dim": dim}


def embedding_search(root: str, query: str, limit: int = 20) -> list[dict]:
    """Search using embedding cosine similarity."""
    import numpy as np

    path = embeddings_path(root)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Embedding index not found. Run 'kb vectorize --engine embedding' first."
        )

    data = np.load(path)
    doc_ids = data["ids"]
    vectors = data["vectors"]
    stored_dim = int(data["dim"][0])

    # Determine which engine to use for query embedding
    # Try OpenAI first, fall back to local hash
    eng: EmbeddingEngine
    try:
        eng = OpenAIEngine()
        q_vecs = eng.embed_texts([query])
        if eng.dim != stored_dim:
            raise ValueError(f"Dimension mismatch: query={eng.dim}, stored={stored_dim}")
    except (Exception):
        eng = LocalHashEngine(dim=stored_dim)
        q_vecs = eng.embed_texts([query])

    q_vec = np.array(q_vecs[0], dtype=np.float32)
    norm = float(np.linalg.norm(q_vec))
    if norm == 0:
        return []
    q_vec /= norm

    scores = vectors @ q_vec
    top_idx = np.argsort(scores)[::-1][:limit]

    results = []
    for idx in top_idx:
        if scores[idx] <= 0:
            break
        results.append({
            "id": str(doc_ids[idx]),
            "score": round(float(scores[idx]), 4),
        })
    return results
