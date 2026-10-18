"""Search index over the vault (SQLite FTS5). Only derived from the files: it is rebuilt at every start."""

import re
import sqlite3
import threading
from pathlib import Path

from vault import LINK, Note, Vault, key

WORD = re.compile(r"[\wäöüÄÖÜß-]{3,}")


class Index:
    def __init__(self, db: Path, vault: Vault):
        self.vault = vault
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(db), check_same_thread=False)
        self.db.executescript("""
            DROP TABLE IF EXISTS docs; DROP TABLE IF EXISTS docs_fts; DROP TABLE IF EXISTS links;
            CREATE TABLE docs (path TEXT PRIMARY KEY, kind TEXT, title TEXT, title_key TEXT, type TEXT,
                               aliases TEXT, summary TEXT, updated TEXT, facts INTEGER);
            CREATE VIRTUAL TABLE docs_fts USING fts5(path UNINDEXED, title, aliases, summary, body, tokenize='trigram');
            CREATE TABLE links (src TEXT, dst_key TEXT);
            CREATE INDEX links_dst ON links(dst_key);
        """)

    def rebuild(self) -> int:
        with self.lock:
            self.db.executescript("DELETE FROM docs; DELETE FROM docs_fts; DELETE FROM links;")
        n = 0
        for rel in self.vault.note_files():
            note = self.vault.read(rel)
            if note:
                self.put(note)
                n += 1
        for rel in self.vault.event_files():
            self.put_events(rel)
        return n

    def _store(self, path, kind, title, type_, aliases, summary, updated, facts, body, links) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM docs WHERE path=?", (path,))
            self.db.execute("DELETE FROM docs_fts WHERE path=?", (path,))
            self.db.execute("DELETE FROM links WHERE src=?", (path,))
            self.db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?)",
                            (path, kind, title, key(title), type_, " | ".join(aliases), summary, updated, facts))
            self.db.execute("INSERT INTO docs_fts VALUES (?,?,?,?,?)", (path, title, " ".join(aliases), summary, body))
            self.db.executemany("INSERT INTO links VALUES (?,?)", [(path, key(t)) for t in set(links)])

    def put(self, note: Note) -> None:
        body = "\n".join(f.text for f in note.facts) + "\n" + "\n".join(f.text for f in note.history) + "\n" + " ".join(note.tags)
        self._store(note.path, "notiz", note.title, note.type, note.aliases, note.summary,
                    note.updated.isoformat() if note.updated else "", len(note.facts), body, note.links)

    def put_events(self, rel: str) -> None:
        events = self.vault.read_events(rel)
        if not events:
            self.remove(rel)
            return
        body = "\n".join(f"{e['time']} {e['text']}" for e in events)
        links = [t.strip() for e in events for t in LINK.findall(e["text"])]
        day = Path(rel).stem
        self._store(rel, "ereignisse", day, "ereignisse", [], "", day, len(events), body, links)

    def remove(self, path: str) -> None:
        with self.lock, self.db:
            for table in ("docs", "docs_fts"):
                self.db.execute(f"DELETE FROM {table} WHERE path=?", (path,))
            self.db.execute("DELETE FROM links WHERE src=?", (path,))

    # -- lookups -----------------------------------------------------------------------------------
    def by_title(self, title: str) -> str | None:
        """Path of the note with this title or alias (case does not matter)."""
        k = key(title)
        with self.lock:
            row = self.db.execute("SELECT path FROM docs WHERE kind='notiz' AND title_key=?", (k,)).fetchone()
            if row:
                return row[0]
            for path, aliases in self.db.execute("SELECT path, aliases FROM docs WHERE kind='notiz' AND aliases != ''"):
                if k in {key(a) for a in aliases.split(" | ")}:
                    return path
        return None

    def search(self, query: str, limit: int = 8, kind: str | None = None) -> list[dict]:
        words = list(dict.fromkeys(w.lower() for w in WORD.findall(query or "")))[:12]
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        sql = ("SELECT d.path, d.kind, d.title, d.type, d.summary, d.updated, "
               "snippet(docs_fts, 4, '**', '**', ' … ', 12), bm25(docs_fts, 0, 8.0, 6.0, 3.0, 1.0) AS rank "
               "FROM docs_fts JOIN docs d ON d.path = docs_fts.path WHERE docs_fts MATCH ?")
        args: list = [match]
        if kind:
            sql += " AND d.kind = ?"
            args.append(kind)
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit)
        with self.lock:
            try:
                rows = self.db.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return []
        return [{"path": r[0], "kind": r[1], "title": r[2], "type": r[3], "summary": r[4], "updated": r[5], "snippet": r[6]}
                for r in rows]

    def backlinks(self, title: str, aliases: list[str] = ()) -> list[str]:
        keys = [key(title)] + [key(a) for a in aliases]
        with self.lock:
            rows = self.db.execute(f"SELECT DISTINCT d.title, d.kind FROM links l JOIN docs d ON d.path = l.src "
                                   f"WHERE l.dst_key IN ({','.join('?' * len(keys))})", keys).fetchall()
        return [t for t, kind in rows if kind == "notiz"]

    def titles(self, limit: int = 600) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT path, title, type, aliases, summary, facts, updated FROM docs WHERE kind='notiz' "
                                   "ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
        return [{"path": r[0], "title": r[1], "type": r[2], "aliases": [a for a in r[3].split(" | ") if a], "summary": r[4],
                 "facts": r[5], "updated": r[6]} for r in rows]

    def stats(self) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT kind, type, COUNT(*), SUM(facts) FROM docs GROUP BY kind, type").fetchall()
            links = self.db.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        out = {"notizen": 0, "fakten": 0, "ereignis_tage": 0, "ereignisse": 0, "verknuepfungen": links, "typen": {}}
        for kind, type_, n, facts in rows:
            if kind == "notiz":
                out["notizen"] += n
                out["fakten"] += facts or 0
                out["typen"][type_] = n
            else:
                out["ereignis_tage"] += n
                out["ereignisse"] += facts or 0
        return out

    def most_linked(self, limit: int = 40) -> list[str]:
        with self.lock:
            rows = self.db.execute("SELECT d.path FROM docs d LEFT JOIN links l ON l.dst_key = d.title_key "
                                   "WHERE d.kind='notiz' GROUP BY d.path ORDER BY COUNT(l.src) DESC, d.facts DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [r[0] for r in rows]
