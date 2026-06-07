"""Concept note management: suggest and generate concept notes."""

import os
import sqlite3

from .core import CONCEPT_DIR, RECORDS_DIR


def suggest_concept_notes(
    conn: sqlite3.Connection,
    root: str,
    min_df: int = 2,
) -> list[dict]:
    """Find concepts that need a note file.

    Returns concepts with df >= min_df that don't have a .md file
    in records/concept/, ordered by df descending.
    """
    concept_dir = os.path.join(root, RECORDS_DIR, CONCEPT_DIR)

    rows = conn.execute(
        "SELECT c.concept_id, c.label, c.df "
        "FROM concepts c "
        "WHERE c.df >= ? AND c.is_stop = 0 "
        "ORDER BY c.df DESC",
        (min_df,),
    ).fetchall()

    candidates = []
    for concept_id, label, df in rows:
        note_path = os.path.join(concept_dir, f"{concept_id}.md")
        if os.path.exists(note_path):
            continue

        doc_ids = conn.execute(
            "SELECT doc_id FROM doc_concepts WHERE concept_id = ? LIMIT 5",
            (concept_id,),
        ).fetchall()

        doc_titles: list[str] = []
        for (doc_id,) in doc_ids:
            row = conn.execute(
                "SELECT title FROM docs WHERE id = ?", (doc_id,),
            ).fetchone()
            if row and row[0]:
                doc_titles.append(row[0])

        candidates.append({
            "concept_id": concept_id,
            "label": label,
            "df": df,
            "doc_titles": doc_titles,
        })

    return candidates


def generate_concept_note(
    root: str,
    concept_id: str,
    label: str,
    df: int,
    doc_titles: list[str] | None = None,
) -> str:
    """Generate a concept note file in records/concept/.

    Returns the relative path to the created file.
    """
    concept_dir = os.path.join(root, RECORDS_DIR, CONCEPT_DIR)
    os.makedirs(concept_dir, exist_ok=True)

    note_path = os.path.join(concept_dir, f"{concept_id}.md")

    lines = [
        f"# {label}",
        "",
        f"Appears in {df} document(s).",
    ]

    if doc_titles:
        lines.append("")
        lines.append("## Documents")
        for title in doc_titles:
            lines.append(f"- {title}")

    lines.append("")

    with open(note_path, "w") as f:
        f.write("\n".join(lines))

    return os.path.join(RECORDS_DIR, CONCEPT_DIR, f"{concept_id}.md")
