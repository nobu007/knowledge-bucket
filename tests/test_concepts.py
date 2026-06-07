"""Tests for concept note suggestion and generation."""

import os
import tempfile

from kb.concepts import generate_concept_note, suggest_concept_notes
from kb.core import CONCEPT_DIR, DOC_DIR, RECORDS_DIR, ensure_dirs
from kb.graph import init_graph_tables
from kb.index import init_db


def _make_doc(root, doc_id, title, concepts=None, source_type="web"):
    """Create a minimal document with front matter."""
    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, "ab", "cd")
    os.makedirs(doc_dir, exist_ok=True)
    path = os.path.join(doc_dir, f"{doc_id}.md")
    fm = f"---\nid: {doc_id}\ntitle: {title}\nsource_type: {source_type}\n"
    if concepts:
        fm += "concepts:\n"
        for c in concepts:
            fm += f"  - {c}\n"
    fm += "---\n\nBody text.\n"
    with open(path, "w") as f:
        f.write(fm)
    return path


def _seed_graph_db(root, conn):
    """Seed the graph DB with concepts and doc_concepts."""
    init_graph_tables(conn)
    now = "2026-01-01T00:00:00"

    # Concept: rag (df=3)
    conn.execute(
        "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
        "VALUES ('rag', 'RAG', 3, 0, ?)", (now,),
    )
    # Concept: knowledge-graph (df=2)
    conn.execute(
        "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
        "VALUES ('knowledge-graph', 'Knowledge Graph', 2, 0, ?)", (now,),
    )
    # Concept: python (stop concept, df=5)
    conn.execute(
        "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
        "VALUES ('python', 'Python', 5, 1, ?)", (now,),
    )
    # Concept: embedding (df=1, below min_df)
    conn.execute(
        "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
        "VALUES ('embedding', 'Embedding', 1, 0, ?)", (now,),
    )

    # doc_concepts links
    for doc_id in ("d1", "d2", "d3"):
        conn.execute(
            "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
            "VALUES (?, 'rag', 'primary', 1.0)", (doc_id,),
        )
    for doc_id in ("d1", "d2"):
        conn.execute(
            "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
            "VALUES (?, 'knowledge-graph', 'primary', 1.0)", (doc_id,),
        )
    conn.execute(
        "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
        "VALUES ('d1', 'python', 'primary', 1.0)",
    )
    conn.execute(
        "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
        "VALUES ('d4', 'embedding', 'primary', 1.0)",
    )

    # FTS docs for title lookup
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES ('d1', 'Doc One', '', 'web', 'records/doc/d1.md', 'body')",
    )
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES ('d2', 'Doc Two', '', 'web', 'records/doc/d2.md', 'body')",
    )
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES ('d3', 'Doc Three', '', 'web', 'records/doc/d3.md', 'body')",
    )
    conn.commit()


class TestSuggestConceptNotes:
    def test_returns_candidates_above_min_df(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = os.path.join(tmp, ".kb", "index.db")
            conn = init_db(db)
            _seed_graph_db(tmp, conn)

            candidates = suggest_concept_notes(conn, tmp, min_df=2)
            ids = {c["concept_id"] for c in candidates}
            assert "rag" in ids
            assert "knowledge-graph" in ids
            assert "embedding" not in ids  # df=1
            assert "python" not in ids  # is_stop
            conn.close()

    def test_excludes_existing_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = os.path.join(tmp, ".kb", "index.db")
            conn = init_db(db)
            _seed_graph_db(tmp, conn)

            # Create existing note for 'rag'
            concept_dir = os.path.join(tmp, RECORDS_DIR, CONCEPT_DIR)
            os.makedirs(concept_dir, exist_ok=True)
            with open(os.path.join(concept_dir, "rag.md"), "w") as f:
                f.write("# RAG\n")

            candidates = suggest_concept_notes(conn, tmp, min_df=2)
            ids = {c["concept_id"] for c in candidates}
            assert "rag" not in ids
            assert "knowledge-graph" in ids
            conn.close()

    def test_includes_doc_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = os.path.join(tmp, ".kb", "index.db")
            conn = init_db(db)
            _seed_graph_db(tmp, conn)

            candidates = suggest_concept_notes(conn, tmp, min_df=2)
            rag = next(c for c in candidates if c["concept_id"] == "rag")
            assert len(rag["doc_titles"]) >= 1
            assert "Doc One" in rag["doc_titles"]
            conn.close()

    def test_ordered_by_df_desc(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = os.path.join(tmp, ".kb", "index.db")
            conn = init_db(db)
            _seed_graph_db(tmp, conn)

            candidates = suggest_concept_notes(conn, tmp, min_df=2)
            assert candidates[0]["concept_id"] == "rag"  # df=3
            assert candidates[1]["concept_id"] == "knowledge-graph"  # df=2
            conn.close()

    def test_empty_when_no_concepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = os.path.join(tmp, ".kb", "index.db")
            conn = init_db(db)
            init_graph_tables(conn)
            candidates = suggest_concept_notes(conn, tmp, min_df=2)
            assert candidates == []
            conn.close()


class TestGenerateConceptNote:
    def test_creates_note_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = generate_concept_note(tmp, "rag", "RAG", 3)
            assert os.path.exists(os.path.join(tmp, rel))

    def test_note_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate_concept_note(
                tmp, "rag", "RAG", 3, ["Doc One", "Doc Two"],
            )
            concept_dir = os.path.join(tmp, RECORDS_DIR, CONCEPT_DIR)
            with open(os.path.join(concept_dir, "rag.md")) as f:
                text = f.read()
            assert "# RAG" in text
            assert "3 document(s)" in text
            assert "Doc One" in text
            assert "Doc Two" in text

    def test_note_without_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate_concept_note(tmp, "kg", "Knowledge Graph", 2)
            concept_dir = os.path.join(tmp, RECORDS_DIR, CONCEPT_DIR)
            with open(os.path.join(concept_dir, "kg.md")) as f:
                text = f.read()
            assert "# Knowledge Graph" in text
            assert "## Documents" not in text
