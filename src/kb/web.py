"""Local web UI for Knowledge Bucket: search, document detail, related docs."""

import os
import sqlite3

from flask import Flask, abort, render_template_string, request

from .core import DOC_DIR, RECORDS_DIR
from .graph import (
    _parse_front_matter_yaml,
    init_graph_tables,
    load_taxonomy,
    resolve_virtual_collection,
)
from .health import compute_health
from .index import index_path, search_index
from .related import find_cooccurring_concepts, find_related

_VALID_SOURCE_TYPES = {"web", "paper", "pdf", "git_repo", "repo", "video", "memo"}


def _sort_order(sort: str) -> str:
    if sort == "importance":
        return "COALESCE(ds.importance, 0.0) DESC, d.id DESC"
    if sort == "type":
        return "d.source_type ASC, d.id DESC"
    return "d.id DESC"


def create_app(kb_root: str) -> Flask:
    app = Flask(__name__)
    app.config["KB_ROOT"] = kb_root

    @app.route("/")
    def index_page():
        q = request.args.get("q", "").strip()
        page = request.args.get("page", 1, type=int)
        sort = request.args.get("sort", "date")
        if sort not in ("date", "importance", "type"):
            sort = "date"
        per_page = 20
        results = []
        recent = []
        total = 0
        db_file = index_path(kb_root)
        if q:
            if os.path.exists(db_file):
                conn = sqlite3.connect(db_file)
                try:
                    all_results = search_index(conn, q, limit=1000)
                    total = len(all_results)
                    offset = (page - 1) * per_page
                    results = all_results[offset:offset + per_page]
                finally:
                    conn.close()
        else:
            if os.path.exists(db_file):
                conn = sqlite3.connect(db_file)
                try:
                    init_graph_tables(conn)
                    order = _sort_order(sort)
                    rows = conn.execute(
                        f"SELECT d.id, d.title, d.source, d.source_type, "
                        f"COALESCE(ds.importance, 0.0) "
                        f"FROM docs d LEFT JOIN doc_stats ds ON ds.doc_id = d.id "
                        f"ORDER BY {order} LIMIT ? OFFSET ?",
                        (per_page, (page - 1) * per_page),
                    ).fetchall()
                    recent = [
                        {"id": r[0], "title": r[1], "source": r[2],
                         "source_type": r[3], "importance": r[4]}
                        for r in rows
                    ]
                    total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
                finally:
                    conn.close()
        total_pages = max(1, -(-total // per_page))
        has_prev = page > 1
        has_next = page < total_pages
        return render_template_string(
            _INDEX_HTML, query=q, results=results, recent=recent,
            page=page, total_pages=total_pages, has_prev=has_prev, has_next=has_next,
            sort=sort,
        )

    @app.route("/doc/<doc_id>")
    def doc_detail(doc_id: str):
        doc_dir = os.path.join(kb_root, RECORDS_DIR, DOC_DIR)
        found_path = None
        for dirpath, _dirnames, filenames in os.walk(doc_dir):
            for fn in filenames:
                if fn == f"{doc_id}.md":
                    found_path = os.path.join(dirpath, fn)
                    break
            if found_path:
                break

        if not found_path:
            abort(404)

        with open(found_path) as f:
            text = f.read()
        meta, body = _parse_front_matter_yaml(text)

        related_docs = []
        db = index_path(kb_root)
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                related_docs = find_related(conn, doc_id, limit=10)
            finally:
                conn.close()

        concepts = meta.get("concepts", [])
        if isinstance(concepts, str):
            concepts = [c.strip() for c in concepts.split(",") if c.strip()]

        return render_template_string(
            _DOC_HTML,
            doc_id=doc_id,
            meta=meta,
            body=body.strip(),
            concepts=concepts,
            related=related_docs,
        )

    @app.route("/recent")
    def recent_page():
        page = request.args.get("page", 1, type=int)
        sort = request.args.get("sort", "date")
        if sort not in ("date", "importance", "type"):
            sort = "date"
        per_page = 20
        offset = (page - 1) * per_page
        docs = []
        total = 0
        db = index_path(kb_root)
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                init_graph_tables(conn)
                total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
                order = _sort_order(sort)
                rows = conn.execute(
                    f"SELECT d.id, d.title, d.source, d.source_type, "
                    f"COALESCE(ds.importance, 0.0) "
                    f"FROM docs d LEFT JOIN doc_stats ds ON ds.doc_id = d.id "
                    f"ORDER BY {order} LIMIT ? OFFSET ?",
                    (per_page, offset),
                ).fetchall()
                docs = [
                    {"id": r[0], "title": r[1], "source": r[2],
                     "source_type": r[3], "importance": r[4]}
                    for r in rows
                ]
            finally:
                conn.close()
        total_pages = max(1, -(-total // per_page))
        has_prev = page > 1
        has_next = page < total_pages
        return render_template_string(
            _RECENT_HTML, docs=docs, page=page,
            total_pages=total_pages, has_prev=has_prev, has_next=has_next,
            sort=sort,
        )

    @app.route("/api/recent")
    def api_recent():
        limit = request.args.get("limit", 20, type=int)
        limit = min(limit, 100)
        db = index_path(kb_root)
        if not os.path.exists(db):
            return {"docs": []}
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT id, title, source, source_type FROM docs "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return {
            "docs": [
                {"id": r[0], "title": r[1], "source": r[2],
                 "source_type": r[3]}
                for r in rows
            ],
        }

    @app.route("/api/search")
    def api_search():
        q = request.args.get("q", "").strip()
        if not q:
            return {"results": []}
        db = index_path(kb_root)
        if not os.path.exists(db):
            return {"results": []}
        conn = sqlite3.connect(db)
        try:
            results = search_index(conn, q, limit=50)
        finally:
            conn.close()
        return {"results": results}

    @app.route("/api/stats")
    def api_stats():
        db = index_path(kb_root)
        if not os.path.exists(db):
            return {"docs": 0, "concepts": 0}
        conn = sqlite3.connect(db)
        try:
            init_graph_tables(conn)
            doc_count = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            concept_count = conn.execute(
                "SELECT COUNT(*) FROM concepts WHERE is_stop=0"
            ).fetchone()[0]
        finally:
            conn.close()
        return {"docs": doc_count, "concepts": concept_count}

    @app.route("/api/graph")
    def api_graph():
        db = index_path(kb_root)
        if not os.path.exists(db):
            return {"nodes": [], "links": []}
        conn = sqlite3.connect(db)
        try:
            init_graph_tables(conn)
            # Top concepts by document frequency
            concept_rows = conn.execute(
                "SELECT concept_id, label, df FROM concepts "
                "WHERE is_stop=0 AND df >= 1 ORDER BY df DESC LIMIT 50"
            ).fetchall()
            concept_ids = [r[0] for r in concept_rows]
            if not concept_ids:
                return {"nodes": [], "links": []}
            placeholders = ",".join("?" * len(concept_ids))
            # Documents connected to these concepts
            doc_rows = conn.execute(
                f"SELECT DISTINCT d.id, d.title, d.source "
                f"FROM docs d "
                f"JOIN doc_concepts dc ON dc.doc_id = d.id "
                f"WHERE dc.concept_id IN ({placeholders}) "
                f"ORDER BY d.title ASC LIMIT 100",
                concept_ids,
            ).fetchall()
            doc_ids = [r[0] for r in doc_rows]
            # Edges between docs and concepts
            edge_rows = conn.execute(
                f"SELECT dc.doc_id, dc.concept_id, dc.weight "
                f"FROM doc_concepts dc "
                f"WHERE dc.doc_id IN ({','.join('?' * len(doc_ids))}) "
                f"AND dc.concept_id IN ({placeholders})",
                doc_ids + concept_ids,
            ).fetchall()
        finally:
            conn.close()

        nodes = []
        for cid, label, df in concept_rows:
            nodes.append({"id": f"c:{cid}", "label": label, "type": "concept", "df": df})
        for did, title, source in doc_rows:
            nodes.append({
                "id": f"d:{did}", "label": title or did,
                "type": "doc", "source": source,
            })
        links = [{"source": f"d:{e[0]}", "target": f"c:{e[1]}", "weight": e[2]} for e in edge_rows]
        return {"nodes": nodes, "links": links}

    @app.route("/graph")
    def graph_page():
        return render_template_string(_GRAPH_HTML)

    @app.route("/api/health")
    def api_health():
        report = compute_health(kb_root)
        return report

    @app.route("/health")
    def health_page():
        report = compute_health(kb_root)
        return render_template_string(_HEALTH_HTML, report=report)

    @app.route("/categories")
    def categories_page():
        db = index_path(kb_root)
        categories = []
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                init_graph_tables(conn)
                rows = conn.execute(
                    "SELECT source_type, COUNT(*) as cnt FROM doc_stats "
                    "GROUP BY source_type ORDER BY cnt DESC"
                ).fetchall()
                for source_type, cnt in rows:
                    categories.append({"type": source_type, "count": cnt})
            finally:
                conn.close()
        return render_template_string(
            _CATEGORIES_HTML, categories=categories,
        )

    @app.route("/categories/<source_type>")
    def category_detail(source_type: str):
        db = index_path(kb_root)
        docs = []
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT d.id, d.title, d.source, ds.importance "
                    "FROM docs d "
                    "JOIN doc_stats ds ON ds.doc_id = d.id "
                    "WHERE ds.source_type = ? "
                    "ORDER BY ds.importance DESC, d.title ASC",
                    (source_type,),
                ).fetchall()
                for r in rows:
                    docs.append({
                        "id": r[0], "title": r[1],
                        "source": r[2], "importance": r[3],
                    })
            finally:
                conn.close()
        return render_template_string(
            _CATEGORY_DETAIL_HTML,
            source_type=source_type, docs=docs,
        )

    @app.route("/concepts")
    def concepts_page():
        db = index_path(kb_root)
        concepts = []
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                init_graph_tables(conn)
                rows = conn.execute(
                    "SELECT concept_id, label, df FROM concepts "
                    "WHERE is_stop=0 AND df >= 1 "
                    "ORDER BY df DESC, label ASC LIMIT 200"
                ).fetchall()
                for r in rows:
                    concepts.append({
                        "concept_id": r[0], "label": r[1], "df": r[2],
                    })
            finally:
                conn.close()
        return render_template_string(
            _CONCEPTS_HTML, concepts=concepts,
        )

    @app.route("/concepts/<concept_id>")
    def concept_detail(concept_id: str):
        db = index_path(kb_root)
        docs = []
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                init_graph_tables(conn)
                rows = conn.execute(
                    "SELECT d.id, d.title, d.source, dc.weight "
                    "FROM doc_concepts dc "
                    "JOIN docs d ON d.id = dc.doc_id "
                    "WHERE dc.concept_id = ? "
                    "ORDER BY dc.weight DESC, d.title ASC",
                    (concept_id,),
                ).fetchall()
                for r in rows:
                    docs.append({
                        "id": r[0], "title": r[1],
                        "source": r[2], "weight": r[3],
                    })
                label_row = conn.execute(
                    "SELECT label FROM concepts WHERE concept_id = ?",
                    (concept_id,),
                ).fetchone()
                concept_label = label_row[0] if label_row else concept_id
                cooc = find_cooccurring_concepts(conn, concept_id, limit=15)
            finally:
                conn.close()
        else:
            concept_label = concept_id
            cooc = []
        return render_template_string(
            _CONCEPT_DETAIL_HTML,
            concept_id=concept_id, concept_label=concept_label, docs=docs,
            cooc=cooc,
        )

    @app.route("/collections")
    def collections_page():
        taxonomy = load_taxonomy(kb_root)
        db = index_path(kb_root)
        collections = []
        if taxonomy and os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                init_graph_tables(conn)
                for name, cdef in taxonomy.items():
                    docs = resolve_virtual_collection(conn, cdef)
                    collections.append({
                        "name": name,
                        "label": cdef.get("label", name),
                        "count": len(docs),
                    })
            finally:
                conn.close()
        return render_template_string(
            _COLLECTIONS_HTML, collections=collections,
        )

    @app.route("/collections/<name>")
    def collection_detail(name: str):
        taxonomy = load_taxonomy(kb_root)
        cdef = taxonomy.get(name)
        if not cdef:
            abort(404)
        db = index_path(kb_root)
        docs = []
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                init_graph_tables(conn)
                docs = resolve_virtual_collection(conn, cdef)
            finally:
                conn.close()
        return render_template_string(
            _COLLECTION_DETAIL_HTML,
            name=name, label=cdef.get("label", name), docs=docs,
        )

    return app


_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; }
form { margin-bottom: 2rem; }
input[type=text] {
  width: 100%; padding: 0.6rem; font-size: 1rem;
  border: 1px solid #ccc; border-radius: 4px;
}
.result { border-bottom: 1px solid #eee; padding: 1rem 0; }
.result-title { font-weight: 600; font-size: 1.1rem; }
.result-title a { color: #2563eb; text-decoration: none; }
.result-meta { font-size: 0.85rem; color: #666; margin-top: 0.25rem; }
.result-snippet { margin-top: 0.4rem; color: #444; font-size: 0.95rem; }
.result-snippet mark {
  background: #fef08a; padding: 0 2px; border-radius: 2px;
}
.empty { color: #888; margin-top: 1rem; }
.page-link {
  display: inline-block; padding: 0.4rem 0.8rem;
  background: #f0f0f0; border-radius: 4px;
  color: #2563eb; text-decoration: none; font-size: 0.9rem;
}
.page-link:hover { background: #e0e0e0; }
.page-info { font-size: 0.85rem; color: #666; }
</style>
</head>
<body>
<h1>Knowledge Bucket</h1>
<nav style="margin-bottom:1.5rem;font-size:0.9rem;">
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
<form method="get">
  <input type="text" name="q" value="{{ query }}"
         placeholder="Search documents..." autofocus>
</form>
{% if query and not results %}
<p class="empty">No results found for &ldquo;{{ query }}&rdquo;</p>
{% endif %}
{% for r in results %}
<div class="result">
  <div class="result-title">
    <a href="/doc/{{ r.id }}">{{ r.title }}</a>
  </div>
  <div class="result-meta">
    {{ r.source_type }}
    {%- if r.source %} &middot; {{ r.source }}{% endif %}
  </div>
  <div class="result-snippet">{{ r.snippet }}</div>
</div>
{% endfor %}
{% if not query and recent %}
<h2 style="font-size:1.1rem;margin-bottom:0.75rem;">Recent Documents</h2>
<div style="margin-bottom:0.5rem;font-size:0.9rem;">
  Sort by:
  <a href="/?sort=date{% if page > 1 %}&amp;page={{ page }}{% endif %}"
     {% if sort == 'date' %}style="font-weight:bold;"{% endif %}>Date</a> &middot;
  <a href="/?sort=importance{% if page > 1 %}&amp;page={{ page }}{% endif %}"
     {% if sort == 'importance' %}style="font-weight:bold;"{% endif %}>Importance</a> &middot;
  <a href="/?sort=type{% if page > 1 %}&amp;page={{ page }}{% endif %}"
     {% if sort == 'type' %}style="font-weight:bold;"{% endif %}>Type</a>
</div>
{% for r in recent %}
<div class="result">
  <div class="result-title">
    <a href="/doc/{{ r.id }}">{{ r.title or r.id }}</a>
  </div>
  <div class="result-meta">
    {{ r.source_type }}
    {%- if r.source %} &middot; {{ r.source }}{% endif %}
    {%- if sort == 'importance' %}
      &middot; importance: {{ "%.2f"|format(r.importance) }}
    {%- endif %}
  </div>
</div>
{% endfor %}
<p style="margin-top:1rem;font-size:0.85rem;">
  <a href="/recent">View more recent documents</a>
</p>
{% endif %}
{% if total_pages > 1 %}
<div style="margin-top:1.5rem;display:flex;gap:0.5rem;align-items:center;">
  {% if has_prev %}
  <a href="/?{% if query %}q={{ query }}&amp;{%- endif -%}
    {%- if not query and sort != 'date' %}sort={{ sort }}&amp;{%- endif -%}
    page={{ page - 1 }}"
     class="page-link">&laquo; Prev</a>
  {% endif %}
  <span class="page-info">Page {{ page }} of {{ total_pages }}</span>
  {% if has_next %}
  <a href="/?{% if query %}q={{ query }}&amp;{%- endif -%}
    {%- if not query and sort != 'date' %}sort={{ sort }}&amp;{%- endif -%}
    page={{ page + 1 }}"
     class="page-link">Next &raquo;</a>
  {% endif %}
</div>
{% endif %}
</body>
</html>
"""

_DOC_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ meta.get('title', doc_id) }} - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }
.meta { color: #666; font-size: 0.85rem; margin-bottom: 1rem; }
.meta span { margin-right: 1rem; }
.concepts { margin-bottom: 1rem; }
.concepts .tag {
  display: inline-block; background: #f0f0f0;
  padding: 2px 8px; border-radius: 3px;
  font-size: 0.85rem; margin: 2px;
  color: #2563eb; text-decoration: none;
}
.body { white-space: pre-wrap; line-height: 1.6; margin-bottom: 2rem; }
h2 {
  font-size: 1.1rem; margin: 1.5rem 0 0.5rem;
  border-bottom: 1px solid #eee; padding-bottom: 0.3rem;
}
.related-item { padding: 0.5rem 0; border-bottom: 1px solid #f5f5f5; }
.related-item a { color: #2563eb; text-decoration: none; font-weight: 500; }
.related-item .weight { font-size: 0.8rem; color: #999; }
.back {
  display: inline-block; margin-bottom: 1rem;
  color: #2563eb; text-decoration: none;
}
</style>
</head>
<body>
<nav style="margin-bottom:1rem;font-size:0.9rem;">
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
<a class="back" href="/">&larr; Search</a>
<h1>{{ meta.get('title', doc_id) }}</h1>
<div class="meta">
  <span>ID: {{ doc_id }}</span>
  <span>Type: {{ meta.get('source_type', 'web') }}</span>
  {% if meta.get('source') %}
  <span>Source: <a href="{{ meta.source }}">{{ meta.source }}</a></span>
  {% endif %}
  {% if meta.get('created') %}
  <span>Created: {{ meta.created }}</span>
  {% endif %}
</div>
{% if concepts %}
<div class="concepts">
  {% for c in concepts %}<a href="/concepts/{{ c }}" class="tag">{{ c }}</a>{% endfor %}
</div>
{% endif %}
<div class="body">{{ body }}</div>
{% if related %}
<h2>Related Documents</h2>
{% for r in related %}
<div class="related-item">
  <a href="/doc/{{ r.doc_id }}">{{ r.title or r.doc_id }}</a>
  <span class="weight">(weight: {{ "%.2f"|format(r.weight) }})</span>
</div>
{% endfor %}
{% endif %}
</body>
</html>
"""

_RECENT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recent Documents - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; }
nav { margin-bottom: 1.5rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
.doc-item { border-bottom: 1px solid #eee; padding: 0.75rem 0; }
.doc-item a { color: #2563eb; text-decoration: none; font-weight: 500; font-size: 1.05rem; }
.doc-item .meta { font-size: 0.85rem; color: #666; margin-top: 0.25rem; }
.empty { color: #888; margin-top: 1rem; }
.page-link {
  display: inline-block; padding: 0.4rem 0.8rem;
  background: #f0f0f0; border-radius: 4px;
  color: #2563eb; text-decoration: none; font-size: 0.9rem;
}
.page-link:hover { background: #e0e0e0; }
.page-info { font-size: 0.85rem; color: #666; }
</style>
</head>
<body>
<h1>Recent Documents</h1>
<div style="margin-bottom:1rem;font-size:0.9rem;">
  Sort by:
  <a href="/recent?sort=date{% if page > 1 %}&amp;page={{ page }}{% endif %}"
     {% if sort == 'date' %}style="font-weight:bold;"{% endif %}>Date</a> &middot;
  <a href="/recent?sort=importance{% if page > 1 %}&amp;page={{ page }}{% endif %}"
     {% if sort == 'importance' %}style="font-weight:bold;"{% endif %}>Importance</a> &middot;
  <a href="/recent?sort=type{% if page > 1 %}&amp;page={{ page }}{% endif %}"
     {% if sort == 'type' %}style="font-weight:bold;"{% endif %}>Type</a>
</div>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
{% if not docs %}
<p class="empty">No documents yet. Use <code>kb add</code> to add documents.</p>
{% endif %}
{% for d in docs %}
<div class="doc-item">
  <a href="/doc/{{ d.id }}">{{ d.title or d.id }}</a>
  <div class="meta">
    {{ d.source_type }}
    {%- if d.source %} &middot; {{ d.source }}{% endif %}
    {%- if sort == 'importance' %}
      &middot; importance: {{ "%.2f"|format(d.importance) }}
    {%- endif %}
  </div>
</div>
{% endfor %}
{% if total_pages > 1 %}
<div class="pagination" style="margin-top:1.5rem;display:flex;gap:0.5rem;align-items:center;">
  {% if has_prev %}
  <a href="/recent?page={{ page - 1 }}&amp;sort={{ sort }}" class="page-link">&laquo; Prev</a>
  {% endif %}
  <span class="page-info">Page {{ page }} of {{ total_pages }}</span>
  {% if has_next %}
  <a href="/recent?page={{ page + 1 }}&amp;sort={{ sort }}" class="page-link">Next &raquo;</a>
  {% endif %}
</div>
{% endif %}
</body>
</html>
"""

_CATEGORIES_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Categories - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; }
nav { margin-bottom: 1.5rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
.cat-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.75rem 0; border-bottom: 1px solid #eee;
}
.cat-item a { color: #2563eb; text-decoration: none; font-weight: 500; font-size: 1.05rem; }
.cat-item .count { color: #666; font-size: 0.85rem; }
.empty { color: #888; margin-top: 1rem; }
</style>
</head>
<body>
<h1>Categories</h1>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
{% if not categories %}
<p class="empty">No categories yet. Run <code>kb graph build</code> first.</p>
{% endif %}
{% for cat in categories %}
<div class="cat-item">
  <a href="/categories/{{ cat.type }}">{{ cat.type }}</a>
  <span class="count">{{ cat.count }} document{{ 's' if cat.count != 1 else '' }}</span>
</div>
{% endfor %}
</body>
</html>
"""

_CATEGORY_DETAIL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ source_type }} - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }
nav { margin-bottom: 1rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
.back { display: inline-block; margin-bottom: 1rem; color: #2563eb; text-decoration: none; }
.doc-item { border-bottom: 1px solid #eee; padding: 0.75rem 0; }
.doc-item a { color: #2563eb; text-decoration: none; font-weight: 500; }
.doc-item .meta { font-size: 0.85rem; color: #666; margin-top: 0.2rem; }
.empty { color: #888; margin-top: 1rem; }
</style>
</head>
<body>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
<a class="back" href="/categories">&larr; All categories</a>
<h1>{{ source_type }}</h1>
{% if not docs %}
<p class="empty">No documents in this category.</p>
{% endif %}
{% for d in docs %}
<div class="doc-item">
  <a href="/doc/{{ d.id }}">{{ d.title or d.id }}</a>
  <div class="meta">
    {% if d.source %}<span>{{ d.source }}</span>{% endif %}
    <span>importance: {{ "%.2f"|format(d.importance) }}</span>
  </div>
</div>
{% endfor %}
</body>
</html>
"""

_GRAPH_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Concept Graph - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  color: #1a1a1a; background: #fafafa;
}
nav { padding: 1rem; font-size: 0.9rem; background: #fff; border-bottom: 1px solid #eee; }
nav a { color: #2563eb; text-decoration: none; }
#graph { width: 100vw; height: calc(100vh - 50px); }
.node { cursor: pointer; }
.node circle { stroke-width: 1.5px; }
.node.concept circle { fill: #2563eb; stroke: #1d4ed8; }
.node.doc circle { fill: #f59e0b; stroke: #d97706; }
.node text { font-size: 11px; pointer-events: none; }
.link { stroke: #999; stroke-opacity: 0.4; }
.tooltip {
  position: absolute; padding: 6px 10px; background: #1a1a1a; color: #fff;
  border-radius: 4px; font-size: 12px; pointer-events: none; display: none;
}
.empty { text-align: center; padding: 4rem 1rem; color: #888; }
</style>
</head>
<body>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
<div id="graph"></div>
<div class="tooltip" id="tooltip"></div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(async function() {
  const resp = await fetch('/api/graph');
  const data = await resp.json();
  if (!data.nodes.length) {
    document.getElementById('graph').innerHTML =
      '<div class="empty">No graph data yet. ' +
      'Add documents and run <code>kb graph build</code>.</div>';
    return;
  }
  const width = document.getElementById('graph').clientWidth;
  const height = document.getElementById('graph').clientHeight;
  const svg = d3.select('#graph').append('svg').attr('width', width).attr('height', height);
  const tooltip = document.getElementById('tooltip');

  const maxDf = d3.max(data.nodes.filter(n => n.type === 'concept'), n => n.df) || 1;
  const nodeSize = n => n.type === 'concept'
    ? 8 + (n.df / maxDf) * 16
    : 7;

  const sim = d3.forceSimulation(data.nodes)
    .force('link', d3.forceLink(data.links).id(d => d.id).distance(60))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => nodeSize(d) + 4));

  const link = svg.append('g').selectAll('line').data(data.links).join('line')
    .attr('class', 'link')
    .attr('stroke-width', d => Math.max(1, d.weight));

  const node = svg.append('g').selectAll('g').data(data.nodes).join('g')
    .attr('class', d => 'node ' + d.type)
    .call(d3.drag()
      .on('start', (e, d) => {
          if (!e.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  node.append('circle').attr('r', d => nodeSize(d));
  node.append('text').attr('dx', d => nodeSize(d) + 4).attr('dy', 4).text(d => d.label);

  node.on('mouseover', (e, d) => {
    tooltip.style.display = 'block';
    tooltip.style.left = (e.pageX + 10) + 'px';
    tooltip.style.top = (e.pageY - 10) + 'px';
    if (d.type === 'concept') {
      tooltip.textContent = d.label + ' (' + d.df + ' docs)';
    } else {
      tooltip.innerHTML = '<b>' + d.label + '</b>' + (d.source ? '<br>' + d.source : '');
    }
  }).on('mouseout', () => { tooltip.style.display = 'none'; });

  node.on('click', (e, d) => {
    if (d.type === 'doc') window.location.href = '/doc/' + d.id.substring(2);
    else window.location.href = '/concepts/' + d.id.substring(2);
  });

  sim.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => 'translate(' + d.x + ',' + d.y + ')');
  });
})();
</script>
</body>
</html>
"""

_CONCEPTS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Concepts - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; }
nav { margin-bottom: 1.5rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
.concept-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.5rem 0; border-bottom: 1px solid #eee;
}
.concept-item a { color: #2563eb; text-decoration: none; font-weight: 500; }
.concept-item .df { color: #666; font-size: 0.85rem; }
.empty { color: #888; margin-top: 1rem; }
</style>
</head>
<body>
<h1>Concepts</h1>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
{% if not concepts %}
<p class="empty">No concepts yet. Run <code>kb graph build</code> first.</p>
{% endif %}
{% for c in concepts %}
<div class="concept-item">
  <a href="/concepts/{{ c.concept_id }}">{{ c.label }}</a>
  <span class="df">{{ c.df }} doc{{ 's' if c.df != 1 else '' }}</span>
</div>
{% endfor %}
</body>
</html>
"""

_CONCEPT_DETAIL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ concept_label }} - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }
nav { margin-bottom: 1rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
.back { display: inline-block; margin-bottom: 1rem; color: #2563eb; text-decoration: none; }
.doc-item { border-bottom: 1px solid #eee; padding: 0.75rem 0; }
.doc-item a { color: #2563eb; text-decoration: none; font-weight: 500; }
.doc-item .meta { font-size: 0.85rem; color: #666; margin-top: 0.2rem; }
.empty { color: #888; margin-top: 1rem; }
.cooc-item {
  display: inline-block; background: #eff6ff;
  padding: 3px 10px; border-radius: 3px; margin: 2px;
  font-size: 0.85rem;
}
.cooc-item a { color: #2563eb; text-decoration: none; font-weight: 500; }
.cooc-item .cooc-count { color: #666; font-size: 0.8rem; }
.section-title {
  font-size: 1.1rem; margin: 1.5rem 0 0.5rem;
  border-bottom: 1px solid #eee; padding-bottom: 0.3rem;
}
</style>
</head>
<body>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
<a class="back" href="/concepts">&larr; All concepts</a>
<h1>{{ concept_label }}</h1>
{% if not docs %}
<p class="empty">No documents with this concept.</p>
{% endif %}
{% for d in docs %}
<div class="doc-item">
  <a href="/doc/{{ d.id }}">{{ d.title or d.id }}</a>
  <div class="meta">
    {% if d.source %}<span>{{ d.source }}</span>{% endif %}
  </div>
</div>
{% endfor %}
{% if cooc %}
<h2 class="section-title">Co-occurring Concepts</h2>
<div>
{% for c in cooc %}
  <span class="cooc-item">
    <a href="/concepts/{{ c.concept_id }}">{{ c.label }}</a>
    <span class="cooc-count">({{ c.cooccurrence }})</span>
  </span>
{% endfor %}
</div>
{% endif %}
</body>
</html>
"""

_COLLECTIONS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Collections - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; }
nav { margin-bottom: 1.5rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
.col-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.75rem 0; border-bottom: 1px solid #eee;
}
.col-item a { color: #2563eb; text-decoration: none; font-weight: 500; font-size: 1.05rem; }
.col-item .count { color: #666; font-size: 0.85rem; }
.empty { color: #888; margin-top: 1rem; }
</style>
</head>
<body>
<h1>Virtual Collections</h1>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
{% if not collections %}
<p class="empty">No virtual collections defined.
Edit <code>config/taxonomy.yml</code> to add collections.</p>
{% endif %}
{% for c in collections %}
<div class="col-item">
  <a href="/collections/{{ c.name }}">{{ c.label }}</a>
  <span class="count">{{ c.count }} document{{ 's' if c.count != 1 else '' }}</span>
</div>
{% endfor %}
</body>
</html>
"""

_COLLECTION_DETAIL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ label }} - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }
nav { margin-bottom: 1rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
.back { display: inline-block; margin-bottom: 1rem; color: #2563eb; text-decoration: none; }
.doc-item { border-bottom: 1px solid #eee; padding: 0.75rem 0; }
.doc-item a { color: #2563eb; text-decoration: none; font-weight: 500; }
.doc-item .meta { font-size: 0.85rem; color: #666; margin-top: 0.2rem; }
.empty { color: #888; margin-top: 1rem; }
</style>
</head>
<body>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
<a class="back" href="/collections">&larr; All collections</a>
<h1>{{ label }}</h1>
{% if not docs %}
<p class="empty">No documents in this collection.</p>
{% endif %}
{% for d in docs %}
<div class="doc-item">
  <a href="/doc/{{ d.id }}">{{ d.title or d.id }}</a>
  <div class="meta">
    {% if d.source %}<span>{{ d.source }}</span>{% endif %}
    <span>importance: {{ "%.2f"|format(d.importance) }}</span>
  </div>
</div>
{% endfor %}
</body>
</html>
"""

_HEALTH_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Health - Knowledge Bucket</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, system-ui, sans-serif;
  max-width: 800px; margin: 2rem auto; padding: 0 1rem;
  color: #1a1a1a;
}
h1 { margin-bottom: 1rem; font-size: 1.5rem; }
nav { margin-bottom: 1.5rem; font-size: 0.9rem; }
nav a { color: #2563eb; text-decoration: none; }
h2 {
  font-size: 1.1rem; margin: 1.5rem 0 0.5rem;
  border-bottom: 1px solid #eee; padding-bottom: 0.3rem;
}
.grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 1rem; margin: 1rem 0;
}
.stat-card {
  background: #f8f9fa; border-radius: 6px; padding: 1rem; text-align: center;
}
.stat-card .value { font-size: 1.8rem; font-weight: 700; color: #1a1a1a; }
.stat-card .label { font-size: 0.8rem; color: #666; margin-top: 0.25rem; }
.stat-card.warn .value { color: #d97706; }
.stat-card.good .value { color: #16a34a; }
.bar-row { display: flex; align-items: center; margin: 0.3rem 0; }
.bar-row .bar-label { width: 120px; font-size: 0.85rem; text-align: right; padding-right: 0.5rem; }
.bar-row .bar { height: 20px; background: #2563eb; border-radius: 3px; min-width: 2px; }
.bar-row .bar-count { font-size: 0.85rem; color: #666; margin-left: 0.5rem; }
.concept-list { margin-top: 0.5rem; }
.concept-item {
  display: inline-block; background: #f0f0f0;
  padding: 2px 8px; border-radius: 3px; margin: 2px;
  font-size: 0.85rem;
}
.concept-item .df { color: #666; }
.metrics-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.75rem; margin: 0.5rem 0;
}
.metric-item { padding: 0.5rem; background: #f8f9fa; border-radius: 4px; }
.metric-item .metric-value { font-weight: 600; font-size: 1.1rem; }
.metric-item .metric-label { font-size: 0.8rem; color: #666; }
.error { color: #dc2626; margin-top: 1rem; }
</style>
</head>
<body>
<h1>Graph Health</h1>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/recent">Recent</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a> &middot;
  <a href="/graph">Graph</a> &middot;
  <a href="/health">Health</a> &middot;
  <a href="/collections">Collections</a>
</nav>
{% if report.get('error') %}
<p class="error">{{ report.error }}</p>
{% else %}
{% set ov = report.overview %}
<div class="grid">
  <div class="stat-card">
    <div class="value">{{ ov.total_documents }}</div>
    <div class="label">Documents</div>
  </div>
  <div class="stat-card">
    <div class="value">{{ ov.total_concepts }}</div>
    <div class="label">Concepts</div>
  </div>
  <div class="stat-card">
    <div class="value">{{ ov.total_edges }}</div>
    <div class="label">Edges</div>
  </div>
  <div class="stat-card {{ 'warn' if ov.orphan_documents > 0 else 'good' }}">
    <div class="value">{{ ov.orphan_documents }}</div>
    <div class="label">Orphan Docs</div>
  </div>
  <div class="stat-card {{ 'warn' if ov.isolated_documents > 0 else 'good' }}">
    <div class="value">{{ ov.isolated_documents }}</div>
    <div class="label">Isolated Docs</div>
  </div>
</div>

<h2>Source Types</h2>
{% for st, count in report.source_types.items() %}
<div class="bar-row">
  <span class="bar-label">{{ st }}</span>
  <div class="bar" style="width: {{ count * 20 }}px"></div>
  <span class="bar-count">{{ count }}</span>
</div>
{% endfor %}

<h2>Importance Distribution</h2>
{% set dist = report.importance_distribution %}
<div class="grid">
  <div class="stat-card good">
    <div class="value">{{ dist.high }}</div><div class="label">High (&ge;0.7)</div>
  </div>
  <div class="stat-card">
    <div class="value">{{ dist.medium }}</div><div class="label">Medium (0.4-0.7)</div>
  </div>
  <div class="stat-card">
    <div class="value">{{ dist.low }}</div><div class="label">Low (&gt;0.0)</div>
  </div>
  <div class="stat-card warn">
    <div class="value">{{ dist.unscored }}</div><div class="label">Unscored</div>
  </div>
</div>

<h2>Metrics</h2>
<div class="metrics-grid">
  <div class="metric-item">
    <div class="metric-value">{{ report.metrics.avg_concepts_per_doc }}</div>
    <div class="metric-label">Avg concepts/doc</div>
  </div>
  <div class="metric-item">
    <div class="metric-value">{{ report.metrics.avg_edges_per_doc }}</div>
    <div class="metric-label">Avg edges/doc</div>
  </div>
  <div class="metric-item">
    <div class="metric-value">{{ "%.1f"|format(report.metrics.connectivity_ratio * 100) }}%</div>
    <div class="metric-label">Connectivity</div>
  </div>
  {% if report.concepts_missing_notes > 0 %}
  <div class="metric-item">
    <div class="metric-value">{{ report.concepts_missing_notes }}</div>
    <div class="metric-label">Concepts missing notes (df>=2)</div>
  </div>
  {% endif %}
</div>

<h2>Top Concepts</h2>
{% if report.top_concepts %}
<div class="concept-list">
{% for c in report.top_concepts[:20] %}
  <span class="concept-item">
    <a href="/concepts/{{ c.id }}">{{ c.label }}</a>
    <span class="df">(df={{ c.df }})</span>
  </span>
{% endfor %}
</div>
{% else %}
<p style="color:#888;margin-top:0.5rem;">No concepts yet.</p>
{% endif %}
{% endif %}
</body>
</html>
"""
