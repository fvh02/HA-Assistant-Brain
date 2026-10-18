# Gedächtnis

Das Langzeitgedächtnis für deinen Assistenten und deine Add-ons. Die Add-ons schicken dem Gedächtnis,
was sie erfahren, und Claude sortiert es in einen **Obsidian-Vault** ein: eine Notiz pro Person, Ort,
Projekt, Gerät, Vorliebe, Organisation oder Thema, verbunden über `[[Links]]`, dazu ein Tagebuch.
So entsteht mit der Zeit ein Netz aus Erinnerungen. Andere Add-ons fragen das Gedächtnis, wenn sie etwas
wissen müssen.

Du musst den Vault nie selbst öffnen. Er liegt aber unter `/share/brain-vault`. Wenn du willst, kannst du
ihn in Obsidian öffnen (z. B. über die Samba-Freigabe) und dir das Netz als Graph ansehen.

## So läuft es

1. **Merken:** Ein Add-on oder der Assistent schickt eine Info. Das Gedächtnis versieht sie sofort mit
   **Datum und Uhrzeit mit Sekunden** (`2026-10-13 14:32:07+02:00`). Schickt das Add-on mit, *wann* etwas
   passiert ist (z. B. das Datum einer iMessage), zählt dieser Zeitpunkt.
2. **Warteschlange:** Die Info wartet kurz (Vorgabe 20 Sekunden), damit zusammengehörige Infos gemeinsam
   verarbeitet werden. Ist Claude nicht erreichbar, bleibt sie in der Warteschlange und wird später erneut
   versucht. Nichts geht verloren.
3. **Einsortieren:** Claude sucht passende Notizen und entscheidet, ob die Info neu ist, etwas ergänzt,
   einen alten Stand ersetzt oder unwichtig ist.
4. **Die neuere Info gewinnt:** Welcher Stand gilt, entscheidet nicht Claude, sondern die Uhrzeit. Der
   zeitlich spätere Stand bleibt aktiv, der ältere wandert in den **Verlauf** der Notiz. Kommt eine alte
   Nachricht verspätet an, überschreibt sie nichts Neueres.
5. **Nachts aufräumen** (Vorgabe 3 Uhr): Doppelte Notizen werden zusammengeführt („Max“ und
   „Max Müller“), lange Notizen verdichtet, Tagebuchtage älter als 60 Tage zu Monatszusammenfassungen
   gemacht und das **Profil** neu geschrieben, eine kurze Zusammenfassung deines Lebens für den Assistenten.

Jede Änderung ist ein Git-Commit im Vault. Zusammengeführte Notizen und alte Tage wandern nach `Archiv/`
und werden nie gelöscht.

## Eine Notiz

```markdown
---
titel: "Max"
typ: "person"
aliase: ["Maximilian"]
tags: ["freunde"]
erstellt: "2026-03-01 10:00:00+01:00"
aktualisiert: "2026-10-13 14:32:07+02:00"
quellen: ["assistent", "imessage"]
---
# Max

Freund von Fabian, wohnt in Bonn und spielt gern Brettspiele.

## Fakten
- Spielt gern Catan · 2026-03-01 10:00:00+01:00 · assistent
- Wohnt in Bonn · 2026-10-13 14:32:07+02:00 · imessage

## Verlauf
- Wohnt in Köln · 2026-03-01 10:00:00+01:00 · assistent · bis 2026-10-13 14:32:07+02:00

## Verknüpfungen
- [[Spieleabend]]
```

Ordner: `Personen/`, `Orte/`, `Projekte/`, `Geräte/`, `Vorlieben/`, `Organisationen/`, `Themen/`,
`Ereignisse/JJJJ/MM/JJJJ-MM-TT.md` (Tagebuch), `Profil.md`, `Archiv/`.

## Claude

Wie der Krypto-Trader fragt das Gedächtnis Claude über die **Claude-Brücke des Inkbox Claude Gateways**
(ab 0.12.0). Das Gateway muss also installiert sein und laufen. Alternativ trägst du einen eigenen Token
aus `claude setup-token` ein. Claude bekommt dabei keine Werkzeuge, es liest und schreibt nur JSON.

## Schnittstelle für Add-ons

Intern erreichbar unter `http://fad21173-brain:8096`. Alle Antworten sind JSON. Ist ein API-Schlüssel
gesetzt, muss er im Header `X-Brain-Key` mitgeschickt werden.

| Aufruf | Wozu |
|---|---|
| `POST /api/remember` | Info merken: `{"text": "...", "source": "imessage", "occurred_at": "2026-10-13T14:32:07+02:00", "subject": "Max"}`. Nur `text` ist Pflicht. Mehrere auf einmal: `{"infos": [ ... ]}` |
| `GET /api/context?q=Max&max_chars=4000` | Fertiger Text mit allem Relevanten zu einem Thema (Profil, passende Notizen, verknüpfte Notizen, Tagebuch) – zum Einfügen in einen Prompt |
| `GET /api/search?q=Spieleabend` | Passende Notizen mit kurzem Ausschnitt |
| `GET /api/note?title=Max` | Eine Notiz mit allen Fakten, Verlauf, Links und Rückverweisen |
| `GET /api/profile` | Das Profil (kurze Zusammenfassung) |
| `GET /api/events?days=7` | Das Tagebuch der letzten Tage |
| `GET /api/notes` | Alle Notizen (Titel, Typ, Zusammenfassung) |
| `GET /api/status` | Zustand, Warteschlange, Zahlen |
| `GET /api/inbox` | Die letzten eingegangenen Infos und was daraus wurde |
| `POST /api/inbox/retry` | Fehlgeschlagene Infos erneut versuchen |
| `POST /api/consolidate` | Jetzt aufräumen statt nachts |

Beispiel aus einem anderen Add-on (Python):

```python
import json, urllib.parse, urllib.request

BRAIN = "http://fad21173-brain:8096"

def remember(text, source, occurred_at=None):
    body = json.dumps({"text": text, "source": source, "occurred_at": occurred_at}).encode()
    req = urllib.request.Request(BRAIN + "/api/remember", data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)

def context(topic):
    with urllib.request.urlopen(f"{BRAIN}/api/context?q={urllib.parse.quote(topic)}", timeout=10) as r:
        return json.load(r)["text"]
```

## Dashboard

In der Seitenleiste unter **Gedächtnis**: Zahlen, Suche, einzelne Notizen mit Verlauf, die Warteschlange,
das Profil, eine Info von Hand merken und Aufräumen per Knopf.
