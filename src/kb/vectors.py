"""TF-IDF vector index for semantic search over knowledge bucket documents."""

import hashlib
import os
import re
import sqlite3
from collections import Counter

from .index import index_path

VECTOR_FILENAME = "vectors.npz"
VEC_DIM = 4096


def vector_path(root: str) -> str:
    return os.path.join(root, ".kb", VECTOR_FILENAME)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]{2,}|[0-9]+", text.lower())


def _hash_dim(token: str) -> int:
    return int(hashlib.md5(token.encode()).hexdigest(), 16) % VEC_DIM


def build_vectors(root: str) -> dict:
    """Build TF-IDF vectors for all indexed documents. Requires numpy."""
    import numpy as np

    db_path = index_path(root)
    if not os.path.exists(db_path):
        raise FileNotFoundError("No index found. Run 'kb index' first.")

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, title, content FROM docs").fetchall()
    conn.close()

    if not rows:
        return {"docs_vectorized": 0}

    n_docs = len(rows)
    doc_ids = []
    doc_token_lists = []

    # Count document frequency per hash bucket
    df = np.zeros(VEC_DIM, dtype=np.float32)

    for doc_id, title, content in rows:
        tokens = _tokenize(f"{title} {content}")
        doc_ids.append(doc_id)
        doc_token_lists.append(tokens)
        seen: set[int] = set()
        for t in tokens:
            h = _hash_dim(t)
            if h not in seen:
                df[h] += 1
                seen.add(h)

    # IDF with smoothing: log(1 + N / (df + 1)) ensures no zero IDF
    idf = np.log1p(n_docs / (df + 1))

    # Build TF-IDF matrix (n_docs x VEC_DIM)
    tfidf = np.zeros((n_docs, VEC_DIM), dtype=np.float32)
    for i, tokens in enumerate(doc_token_lists):
        tf: Counter[int] = Counter()
        for t in tokens:
            tf[_hash_dim(t)] += 1
        for h, count in tf.items():
            tfidf[i, h] = count * idf[h]

    # L2 normalize
    norms = np.linalg.norm(tfidf, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    tfidf /= norms

    # Save
    path = vector_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, ids=np.array(doc_ids), vectors=tfidf, idf=idf)

    return {"docs_vectorized": n_docs}


def semantic_search(root: str, query: str, limit: int = 20) -> list[dict]:
    """Search using TF-IDF cosine similarity. Requires numpy."""
    import numpy as np

    path = vector_path(root)
    if not os.path.exists(path):
        raise FileNotFoundError("Vector index not found. Run 'kb vectorize' first.")

    data = np.load(path)
    doc_ids = data["ids"]
    vectors = data["vectors"]
    idf = data["idf"]

    # Build query vector
    tokens = _tokenize(query)
    q_vec = np.zeros(VEC_DIM, dtype=np.float32)
    tf: Counter[int] = Counter()
    for t in tokens:
        tf[_hash_dim(t)] += 1
    for h, count in tf.items():
        q_vec[h] = count * idf[h]

    norm = float(np.linalg.norm(q_vec))
    if norm == 0:
        return []
    q_vec /= norm

    # Cosine similarity (vectors are already L2-normalized)
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
