"""The Obsidian vault: Markdown notes with facts, their history and links, plus daily event files.

The files are the truth; the search index is only derived from them. Every fact carries the time it
happened (date, time with seconds and UTC offset) and its source, so a newer info about the same thing
can replace an older one while the older one moves to the note's history instead of being lost.
"""

import difflib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FOLDERS = {
    "person": "Personen", "ort": "Orte", "projekt": "Projekte", "geraet": "Geräte", "vorliebe": "Vorlieben",
    "organisation": "Organisationen", "thema": "Themen",
}
TYPES = list(FOLDERS)
EVENTS = "Ereignisse"
ARCHIVE = "Archiv"
PROFILE = "Profil.md"
SKIP = {".git", ".obsidian", ".trash", ARCHIVE}

FACT = re.compile(r"^- (?P<text>.+?) · (?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:[+-]\d\d:\d\d)?) · (?P<src>[^·]+?)"
                  r"(?: · bis (?P<until>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:[+-]\d\d:\d\d)?))?$")
EVENT = re.compile(r"^- (?P<time>\d\d:\d\d:\d\d) · (?P<src>[^·]+?) · (?P<text>.+)$")
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]')


def now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def parse_ts(value) -> datetime | None:
    """ISO time from a sender or a note; without an offset it counts as local time."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return ts.replace(microsecond=0)


def fmt_ts(ts: datetime) -> str:
    return ts.astimezone().isoformat(sep=" ")


def key(text: str) -> str:
    """Comparison form of a title or fact: case, spaces and trailing dots do not matter."""
    return re.sub(r"\s+", " ", str(text)).strip().strip(".").casefold()


def clean(text: str, limit: int = 400) -> str:
    """One line without the separators of the file format."""
    return re.sub(r"\s+", " ", str(text)).replace("·", "-").strip()[:limit]


def safe_title(title: str) -> str:
    return re.sub(r"\s+", " ", BAD_NAME.sub("", str(title))).strip(" .")[:80]


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, key(a), key(b)).ratio()


@dataclass
class Fact:
    text: str
    ts: datetime
    source: str
    until: datetime | None = None

    def line(self) -> str:
        tail = f" · bis {fmt_ts(self.until)}" if self.until else ""
        return f"- {self.text} · {fmt_ts(self.ts)} · {self.source}{tail}"

    def as_dict(self) -> dict:
        d = {"text": self.text, "time": fmt_ts(self.ts), "source": self.source}
        if self.until:
            d["until"] = fmt_ts(self.until)
        return d


@dataclass
class Note:
    path: str
    title: str
    type: str = "thema"
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    created: datetime | None = None
    updated: datetime | None = None
    summary: str = ""
    facts: list[Fact] = field(default_factory=list)
    history: list[Fact] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    extra: str = ""  # text the format does not know (kept as it is)

    # -- changes, all decided by time ----------------------------------------------------------
    def touch(self, ts: datetime, source: str) -> None:
        if not self.created or ts < self.created:
            self.created = ts
        if not self.updated or ts > self.updated:
            self.updated = ts
        if source and source not in self.sources:
            self.sources.append(source)

    def find(self, text: str, facts: list[Fact] | None = None) -> Fact | None:
        facts = self.facts if facts is None else facts
        k = key(text)
        for f in facts:
            if key(f.text) == k:
                return f
        best = max(facts, key=lambda f: similar(f.text, text), default=None)
        return best if best and similar(best.text, text) >= 0.85 else None

    def add(self, text: str, ts: datetime, source: str) -> str:
        text = clean(text)
        if not text:
            return "leer"
        self.touch(ts, source)
        same = self.find(text)
        if same and key(same.text) == key(text) or same and similar(same.text, text) >= 0.95:
            if ts > same.ts:  # the same statement again: it is now confirmed at the newer time
                same.ts, same.source = ts, source
            return "bestätigt"
        self.facts.append(Fact(text, ts, source))
        return "neu"

    def replace(self, old: str, text: str, ts: datetime, source: str) -> str:
        """A new value for the same thing: the newer one stays active, the older one goes to the history."""
        text = clean(text)
        current = self.find(old) if old else None
        if not current and old and (past := self.find(old, self.history)):
            # The old value was already replaced by something else (maybe in this same batch).
            if ts < past.until:
                self.touch(ts, source)
                self.history.append(Fact(text, ts, source, until=past.ts if ts < past.ts else past.until))
                return "älter"
            successors = [f for f in self.facts if f.ts == past.until]
            current = successors[0] if len(successors) == 1 else None
        if not current:
            return self.add(text, ts, source)
        self.touch(ts, source)
        if key(current.text) == key(text):
            if ts > current.ts:
                current.ts, current.source = ts, source
            return "bestätigt"
        if ts >= current.ts:
            self.facts.remove(current)
            current.until = ts
            self.history.append(current)
            self.facts.append(Fact(text, ts, source))
            return "ersetzt"
        # The info arrived late: it is older than what we know, so it only belongs to the history.
        self.history.append(Fact(text, ts, source, until=current.ts))
        return "älter"

    def retire(self, old: str, ts: datetime, source: str) -> str:
        """A fact is no longer true (without a new value)."""
        current = self.find(old)
        if not current:
            return "unbekannt"
        if ts < current.ts:
            return "älter"
        self.touch(ts, source)
        self.facts.remove(current)
        current.until = ts
        self.history.append(current)
        return "beendet"

    def link(self, titles) -> None:
        own = {key(self.title)} | {key(a) for a in self.aliases}
        have = {key(t) for t in self.links}
        for t in titles or []:
            t = safe_title(t)
            if t and key(t) not in own and key(t) not in have:
                self.links.append(t)
                have.add(key(t))

    def merge_list(self, attr: str, values, limit: int = 20) -> None:
        current = getattr(self, attr)
        have = {key(v) for v in current}
        for v in values or []:
            v = clean(v, 60).lstrip("#")
            if v and key(v) not in have and key(v) != key(self.title):
                current.append(v)
                have.add(key(v))
        del current[limit:]

    # -- file format ----------------------------------------------------------------------------
    def render(self) -> str:
        self.facts.sort(key=lambda f: f.ts)
        self.history.sort(key=lambda f: (f.until or f.ts, f.ts))
        meta = {
            "titel": self.title, "typ": self.type, "aliase": self.aliases, "tags": self.tags,
            "erstellt": fmt_ts(self.created or now()), "aktualisiert": fmt_ts(self.updated or now()),
            "quellen": self.sources,
        }
        out = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()] + ["---", f"# {self.title}", ""]
        if self.summary:
            out += [self.summary, ""]
        out += ["## Fakten"] + [f.line() for f in self.facts] + [""]
        if self.history:
            out += ["## Verlauf"] + [f.line() for f in self.history] + [""]
        if self.links:
            out += ["## Verknüpfungen"] + [f"- [[{t}]]" for t in self.links] + [""]
        if self.extra.strip():
            out += [self.extra.strip(), ""]
        return "\n".join(out)

    @classmethod
    def parse(cls, path: str, text: str) -> "Note":
        meta, body = split_front(text)
        note = cls(path=path, title=str(meta.get("titel") or Path(path).stem))
        note.type = meta.get("typ") if meta.get("typ") in TYPES else "thema"
        note.aliases = [str(a) for a in meta.get("aliase") or []]
        note.tags = [str(t) for t in meta.get("tags") or []]
        note.sources = [str(s) for s in meta.get("quellen") or []]
        note.created, note.updated = parse_ts(meta.get("erstellt")), parse_ts(meta.get("aktualisiert"))
        section, summary, extra = "", [], []
        for line in body.splitlines():
            if line.startswith("# ") and not section:
                continue
            if line.startswith("## "):
                section = line[3:].strip().casefold()
                if section not in ("fakten", "verlauf", "verknüpfungen"):
                    extra.append(line)
                continue
            m = FACT.match(line)
            if section in ("fakten", "verlauf") and m:
                f = Fact(m["text"], parse_ts(m["ts"]), m["src"].strip(), parse_ts(m["until"]))
                (note.history if section == "verlauf" or f.until else note.facts).append(f)
            elif section == "verknüpfungen" and LINK.search(line):
                note.links += [t.strip() for t in LINK.findall(line)]
            elif not section:
                summary.append(line)
            elif line.strip() or extra:
                extra.append(line)
        note.summary = "\n".join(summary).strip()
        note.extra = "\n".join(extra).strip()
        return note

    def brief(self, history: bool = True) -> str:
        """Compact text for a prompt or another add-on."""
        lines = [f"### {self.title} ({self.type})" + (f" – auch: {', '.join(self.aliases)}" if self.aliases else "")]
        if self.summary:
            lines.append(self.summary)
        lines += [f"- {f.text} [{fmt_ts(f.ts)}, {f.source}]" for f in self.facts]
        if history and self.history:
            lines += [f"- früher: {f.text} [{fmt_ts(f.ts)} bis {fmt_ts(f.until)}]" for f in self.history[-5:] if f.until]
        if self.links:
            lines.append("Verknüpft: " + ", ".join(self.links))
        return "\n".join(lines)


def split_front(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        k, sep, v = line.partition(":")
        if not sep:
            continue
        try:
            meta[k.strip()] = json.loads(v.strip())
        except ValueError:
            meta[k.strip()] = v.strip()
    return meta, text[end + 4:].lstrip("\n")


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._git_ready = False

    # -- notes -----------------------------------------------------------------------------------
    def note_files(self):
        for path in sorted(self.root.rglob("*.md")):
            rel = path.relative_to(self.root)
            if rel.parts[0] in SKIP or rel.parts[0] == EVENTS or str(rel) == PROFILE:
                continue
            yield str(rel)

    def event_files(self):
        base = self.root / EVENTS
        for path in sorted(base.rglob("*.md")) if base.exists() else []:
            yield str(path.relative_to(self.root))

    def read(self, rel: str) -> Note | None:
        path = self.root / rel
        try:
            return Note.parse(rel, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None

    def path_for(self, title: str, type_: str) -> str:
        return f"{FOLDERS.get(type_, FOLDERS['thema'])}/{safe_title(title)}.md"

    def write(self, note: Note) -> None:
        path = self.root / note.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(note.render(), encoding="utf-8")
        tmp.replace(path)

    def archive(self, rel: str) -> str:
        """Move a file to the archive instead of deleting it."""
        src = self.root / rel
        dst = self.root / ARCHIVE / rel
        if dst.exists():
            dst = dst.with_name(f"{dst.stem} {now().strftime('%Y%m%d-%H%M%S')}{dst.suffix}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return str(dst.relative_to(self.root))

    def rename_links(self, old: str, new: str) -> int:
        """[[old]] → [[new]] in every note and event file; returns how many files changed."""
        pattern = re.compile(r"\[\[" + re.escape(old) + r"(#[^\]|]*)?(\|[^\]]*)?\]\]", re.IGNORECASE)
        changed = 0
        for rel in list(self.note_files()) + list(self.event_files()) + [PROFILE]:
            path = self.root / rel
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            new_text = pattern.sub(lambda m: f"[[{new}{m[1] or ''}{m[2] or ''}]]", text)
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")
                changed += 1
        return changed

    # -- events ------------------------------------------------------------------------------------
    def event_path(self, ts: datetime) -> str:
        d = ts.astimezone()
        return f"{EVENTS}/{d:%Y}/{d:%m}/{d:%Y-%m-%d}.md"

    def add_event(self, ts: datetime, source: str, text: str) -> str:
        """Insert an event line into its day file, sorted by time (late infos land in the right place)."""
        ts = ts.astimezone()
        rel = self.event_path(ts)
        path = self.root / rel
        line = f"- {ts:%H:%M:%S} · {clean(source, 40)} · {clean(text, 1000)}"
        if path.exists():
            text_now = path.read_text(encoding="utf-8")
        else:
            text_now = f"---\ntyp: \"ereignisse\"\ndatum: \"{ts:%Y-%m-%d}\"\n---\n# {ts:%d.%m.%Y}\n\n"
        head, entries, seen = [], [], False
        for raw in text_now.splitlines():
            if EVENT.match(raw):
                seen = True
                entries.append(raw)
            elif not seen:
                head.append(raw)
            else:
                entries.append(raw)
        if line in entries:
            return rel
        entries.append(line)
        entries.sort(key=lambda l: EVENT.match(l)["time"] if EVENT.match(l) else "99")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(head).rstrip() + "\n\n" + "\n".join(entries) + "\n", encoding="utf-8")
        return rel

    def read_events(self, rel: str) -> list[dict]:
        try:
            text = (self.root / rel).read_text(encoding="utf-8")
        except OSError:
            return []
        day = Path(rel).stem
        return [{"day": day, "time": m["time"], "source": m["src"], "text": m["text"]}
                for m in (EVENT.match(l) for l in text.splitlines()) if m]

    # -- profile -----------------------------------------------------------------------------------
    def read_profile(self) -> str:
        try:
            _, body = split_front((self.root / PROFILE).read_text(encoding="utf-8"))
            return body.strip()
        except OSError:
            return ""

    def write_profile(self, text: str) -> None:
        body = f"---\ntyp: \"profil\"\naktualisiert: \"{fmt_ts(now())}\"\n---\n{text.strip()}\n"
        (self.root / PROFILE).write_text(body, encoding="utf-8")

    # -- git: every change can be undone -------------------------------------------------------------
    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=120)

    def commit(self, message: str) -> None:
        try:
            if not self._git_ready:
                if not (self.root / ".git").exists():
                    self._git("init", "-q")
                    (self.root / ".gitignore").write_text(".obsidian/workspace*\n.trash/\n*.tmp\n")
                self._git("config", "user.name", "HA-Assistant-Brain")
                self._git("config", "user.email", "brain@homeassistant.local")
                self._git_ready = True
            self._git("add", "-A")
            if self._git("diff", "--cached", "--quiet").returncode:
                self._git("commit", "-q", "-m", message[:200])
        except (OSError, subprocess.SubprocessError):
            pass
