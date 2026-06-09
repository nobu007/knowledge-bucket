"""Tests for Parquet export."""

import os

import pyarrow.parquet as pq
import pytest

from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path
from kb.graph import init_graph_tables
from kb.index import index_document, index_path, init_db


@pytest.fixture
def kb(tmp_path):
    root = str(tmp_path)
    ensure_dirs(root)
    return root


def _add_doc(root, title="Test", source_type="web", source=None, concepts=None,
             body="Some content."):
    ulid = generate_ulid()
    rel = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel)

    fm = f"---\nid: {ulid}\ntitle: {title}\nsource_type: {source_type}\n"
    if source:
        fm += f"source: {source}\n"
    if concepts:
        fm += "concepts:\n"
        for c in concepts:
            fm += f"  - {c}\n"
    fm += "---\n\n"

    with open(abs_path, "w") as f:
        f.write(fm)
        f.write(body)
        if not body.endswith("\n"):
            f.write("\n")
    return ulid


def _index_doc(root, doc_id, title, source_type="web", source=None, content="content"):
    db = index_path(root)
    conn = init_db(db)
    rel = f"records/doc/{doc_id[:2]}/{doc_id}.md"
    index_document(conn, doc_id, title, source, source_type, rel, content)
    conn.close()


def _setup_graph(root, doc_id, source_type="web", concepts=None, importance=0.5):
    from datetime import UTC, datetime

    db = index_path(root)
    conn = init_db(db)
    init_graph_tables(conn)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
        "VALUES (?, ?, 0, ?, ?)",
        (doc_id, source_type, importance, now),
    )
    if concepts:
        for c in concepts:
            conn.execute(
                "INSERT OR IGNORE INTO concepts "
                "(concept_id, label, kind, df, is_stop, created_at) "
                "VALUES (?, ?, 'concept', 1, 0, ?)",
                (c, c, now),
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES (?, ?, 'primary', 1.0)",
                (doc_id, c),
            )
    conn.commit()
    conn.close()


class TestExportParquet:
    def test_no_index_raises(self, kb):
        from kb.export import export_parquet
        with pytest.raises(FileNotFoundError):
            export_parquet(kb)

    def test_empty_index(self, kb):
        _index_doc(kb, "testid001", "Empty", content="empty")
        from kb.export import export_parquet
        results = export_parquet(kb)
        assert results["documents"] == 1
        assert results["concepts"] == 0
        assert results["doc_concepts"] == 0
        assert results["edges"] == 0
        assert results["doc_stats"] == 0

    def test_creates_parquet_files(self, kb):
        _index_doc(kb, "testid001", "Test", content="hello")
        from kb.export import export_parquet
        export_parquet(kb)

        exports_dir = os.path.join(kb, ".kb", "exports")
        assert os.path.isdir(exports_dir)
        for name in ["documents", "concepts", "doc_concepts", "edges", "doc_stats"]:
            fpath = os.path.join(exports_dir, f"{name}.parquet")
            assert os.path.isfile(fpath), f"Missing {name}.parquet"

    def test_parquet_content_correct(self, kb):
        doc_id = _add_doc(kb, title="RAG Paper", source_type="paper",
                          source="https://example.com", concepts=["rag", "llm"])
        _index_doc(kb, doc_id, "RAG Paper", source_type="paper",
                   source="https://example.com", content="RAG content")
        _setup_graph(kb, doc_id, source_type="paper", concepts=["rag", "llm"])

        from kb.export import export_parquet
        results = export_parquet(kb)
        assert results["documents"] == 1
        assert results["concepts"] == 2
        assert results["doc_concepts"] == 2

        exports_dir = os.path.join(kb, ".kb", "exports")

        # Verify documents table
        docs_table = pq.read_table(os.path.join(exports_dir, "documents.parquet"))
        assert docs_table.num_rows == 1
        assert docs_table.column("title")[0].as_py() == "RAG Paper"

        # Verify concepts table
        concepts_table = pq.read_table(os.path.join(exports_dir, "concepts.parquet"))
        assert concepts_table.num_rows == 2
        concept_ids = {concepts_table.column("concept_id")[i].as_py() for i in range(2)}
        assert "rag" in concept_ids
        assert "llm" in concept_ids

    def test_custom_output_dir(self, kb, tmp_path):
        _index_doc(kb, "testid001", "Test", content="hello")
        from kb.export import export_parquet
        custom_dir = str(tmp_path / "custom_export")
        results = export_parquet(kb, output_dir=custom_dir)
        assert results["documents"] == 1
        assert os.path.isfile(os.path.join(custom_dir, "documents.parquet"))

    def test_export_with_edges(self, kb):
        from datetime import UTC, datetime

        doc1 = _add_doc(kb, title="Doc A", concepts=["rag"])
        doc2 = _add_doc(kb, title="Doc B", concepts=["rag"])
        _index_doc(kb, doc1, "Doc A", content="a")
        _index_doc(kb, doc2, "Doc B", content="b")

        db = index_path(kb)
        conn = init_db(db)
        init_graph_tables(conn)
        now = datetime.now(UTC).isoformat()
        for doc_id in [doc1, doc2]:
            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, "
                "updated_at) VALUES (?, 'web', 0, 0.5, ?)",
                (doc_id, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO concepts "
                "(concept_id, label, kind, df, is_stop, created_at) "
                "VALUES (?, ?, 'concept', 2, 0, ?)",
                ("rag", "rag", now),
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES (?, ?, 'primary', 1.0)",
                (doc_id, "rag"),
            )
        conn.execute(
            "INSERT INTO edges (src_id, dst_id, edge_type, weight, updated_at) "
            "VALUES (?, ?, 'related', 0.8, ?)",
            (doc1, doc2, now),
        )
        conn.commit()
        conn.close()

        from kb.export import export_parquet
        results = export_parquet(kb)
        assert results["edges"] == 1

        exports_dir = os.path.join(kb, ".kb", "exports")
        edges_table = pq.read_table(os.path.join(exports_dir, "edges.parquet"))
        assert edges_table.num_rows == 1
        assert edges_table.column("edge_type")[0].as_py() == "related"
