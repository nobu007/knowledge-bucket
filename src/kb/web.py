"""Local web UI for Knowledge Bucket: search, document detail, related docs."""

import os
import sqlite3

from flask import Flask, abort, render_template_string, request

from .core import DOC_DIR, RECORDS_DIR
from .graph import _parse_front_matter_yaml, init_graph_tables
from .index import index_path, search_index
from .related import find_related

_VALID_SOURCE_TYPES = {"web", "paper", "pdf", "git_repo", "repo", "video", "memo"}


def create_app(kb_root: str) -> Flask:
    app = Flask(__name__)
    app.config["KB_ROOT"] = kb_root

    @app.route("/")
    def index_page():
        q = request.args.get("q", "").strip()
        results = []
        if q:
            db = index_path(kb_root)
            if os.path.exists(db):
                conn = sqlite3.connect(db)
                try:
                    results = search_index(conn, q, limit=50)
                finally:
                    conn.close()
        return render_template_string(_INDEX_HTML, query=q, results=results)

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
            finally:
                conn.close()
        else:
            concept_label = concept_id
        return render_template_string(
            _CONCEPT_DETAIL_HTML,
            concept_id=concept_id, concept_label=concept_label, docs=docs,
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
</style>
</head>
<body>
<h1>Knowledge Bucket</h1>
<nav style="margin-bottom:1.5rem;font-size:0.9rem;">
  <a href="/">Search</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a>
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
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a>
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
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a>
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
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a>
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
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a>
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
</style>
</head>
<body>
<nav>
  <a href="/">Search</a> &middot;
  <a href="/categories">Categories</a> &middot;
  <a href="/concepts">Concepts</a>
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
</body>
</html>
"""
