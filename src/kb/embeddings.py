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


# Module-level cache of loaded SentenceTransformerEngine instances, keyed by
# model name. Loading bge-m3 takes ~1-2s; without this cache every
# embedding_search() call reloaded the model from disk.
_engine_cache: dict[str, "SentenceTransformerEngine"] = {}


class SentenceTransformerEngine:
    """Real local embedding model via sentence-transformers (MPS-accelerated on Apple Silicon).

    Default model is BAAI/bge-m3: multilingual (JA/EN), 8192-token context, top MTEB.
    Override with KB_EMBED_MODEL. Requires the optional [embedding] extra:
    ``pip install -e ".[embedding]"``.
    """

    def __new__(cls, model: str | None = None):
        # Resolve the effective model name the same way __init__ does so the
        # cache key matches across calls that omit the argument.
        name = model or os.environ.get("KB_EMBED_MODEL", "BAAI/bge-m3")
        cached = _engine_cache.get(name)
        if cached is not None:
            return cached
        instance = super().__new__(cls)
        return instance

    def __init__(self, model: str | None = None):
        # If this instance came from the cache, __init__ would otherwise re-run
        # and clobber state. Guard against re-initialization.
        name = model or os.environ.get("KB_EMBED_MODEL", "BAAI/bge-m3")
        if name in _engine_cache and _engine_cache[name] is not self:
            # Another instance already serves this model; nothing to do.
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers not installed. "
                'Run: pip install -e ".[embedding]"'
            ) from e
        import os as _os

        import torch

        # Silence noisy HuggingFace hub download / loading progress bars.
        _os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

        self._model_name = name
        device = "mps" if torch.backends.mps.is_available() else (
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        self._model = SentenceTransformer(self._model_name, device=device)
        # The getter was renamed get_sentence_embedding_dimension ->
        # get_embedding_dimension in newer sentence-transformers releases, but
        # the old name still emits a FutureWarning. Prefer the new name and fall
        # back to the old one for compatibility.
        getter = getattr(self._model, "get_embedding_dimension", None)
        if getter is None:
            getter = self._model.get_sentence_embedding_dimension
        self._dim = getter()

        _engine_cache[self._model_name] = self

    @property
    def dim(self) -> int:
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Truncate to a representative prefix. Long READMEs blow up the
        # attention buffer (O(seq_len^2)) — bge-m3 at 8192 tokens requests a
        # 128GB MPS buffer. The title + opening + dense summary carry the
        # signal; full-text adds noise, not recall.
        capped = [t[:2000] for t in texts]
        vecs = self._model.encode(
            capped, normalize_embeddings=True, show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vecs]



def _get_engine(engine: str, **kwargs) -> EmbeddingEngine:
    if engine == "openai":
        return OpenAIEngine(**kwargs)
    if engine in ("local", "embedding"):
        # Real local model (sentence-transformers). KB_EMBED_MODEL selects the
        # model; default BAAI/bge-m3 for multilingual JA/EN + long context.
        return SentenceTransformerEngine(**kwargs)
    if engine == "hash":
        return LocalHashEngine(**kwargs)
    raise ValueError(
        f"Unknown embedding engine: {engine!r}. "
        "Use 'local' (sentence-transformers), 'openai', or 'hash'."
    )


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

    # Record which model produced these vectors so search can embed the query
    # with the SAME model — mixing engines (e.g. bge-m3 docs vs hash query)
    # yields meaningless cosine scores.
    model_name = getattr(eng, "_model_name", engine)

    path = embeddings_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(
        path, ids=np.array(doc_ids), vectors=mat,
        dim=np.array([dim]), model=np.array([model_name]),
    )

    return {"docs_vectorized": len(doc_ids), "engine": engine, "dim": dim}


def embedding_search(root: str, query: str, limit: int = 20) -> list[dict]:
    """Search using embedding cosine similarity."""
    import numpy as np

    path = embeddings_path(root)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Embedding index not found. Run 'kb vectorize --engine embedding' first."
        )

    data = np.load(path, allow_pickle=True)
    doc_ids = data["ids"]
    vectors = data["vectors"]
    stored_dim = int(data["dim"][0])
    # The query MUST be embedded with the same model that built the doc vectors.
    # Newer .npz files record the model name; fall back to "hash" for legacy.
    model_name = str(data["model"][0]) if "model" in data.files else "hash"

    if model_name in ("hash", "local"):
        eng = LocalHashEngine(dim=stored_dim)
    elif model_name.startswith(("http", "openai")) or model_name == "openai":
        eng = OpenAIEngine()
    else:
        # HuggingFace model id (e.g. BAAI/bge-m3) → load the same local model.
        eng = SentenceTransformerEngine(model=model_name)
    q_vecs = eng.embed_texts([query])
    if eng.dim != stored_dim:
        raise ValueError(f"Dimension mismatch: query={eng.dim}, stored={stored_dim}")

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
