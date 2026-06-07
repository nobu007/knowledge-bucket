"""Local web UI for Knowledge Bucket: search, document detail, related docs."""

import os
import sqlite3

from flask import Flask, abort, render_template_string, request

from .core import DOC_DIR, RECORDS_DIR
from .graph import _parse_front_matter_yaml, init_graph_tables
from .index import index_path, search_index
from .related import find_related


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
  {% for c in concepts %}<span class="tag">{{ c }}</span>{% endfor %}
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
