from __future__ import annotations

import re
import sqlite3
from pathlib import Path

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
STOPWORDS = frozenset("""
a about after again all am an and any are as at be because been before being below
between both but by can did do does doing down during each few for from further had
has have having he her here hers herself him himself his how i if in into is it its
itself just me more most my myself no nor not now of off on once only or other our
ours ourselves out over own same she should so some such than that the their theirs
them themselves then there these they this those through to too under until up very
was we were what when where which while who whom why will with you your yours
yourself yourselves
""".split())


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def query_terms(text: str) -> list[str]:
    return [w for w in tokenize(text) if w not in STOPWORDS and len(w) >= 3]


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return True
    except sqlite3.OperationalError:
        return False


def build_fts5_index(db_path: Path, docs: list[dict[str, str]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE VIRTUAL TABLE pages USING fts5(path, title, body)")
    con.executemany(
        "INSERT INTO pages(path, title, body) VALUES (?, ?, ?)",
        [(d["path"], d["title"], d["text"]) for d in docs]
    )
    con.commit()
    con.close()


def search_fts5(db_path: Path, query: str, limit: int = 5) -> list[dict[str, str]]:
    terms = query_terms(query)
    if not terms or not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        match_expr = " OR ".join(f'"{t}"' for t in terms)
        rows = con.execute(
            "SELECT path, title, snippet(pages, 2, '', '', '…', 12) "
            "FROM pages WHERE pages MATCH ? ORDER BY bm25(pages) LIMIT ?",
            (match_expr, limit),
        ).fetchall()
        return [{"path": r[0], "title": r[1], "snippet": r[2]} for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
