# HA-Assistant-Brain

Home-Assistant-Add-on-Repository mit dem **Gedächtnis**: dem Langzeitgedächtnis für den Assistenten
(Inkbox Claude Gateway) und deine anderen Add-ons, gespeichert als Obsidian-Vault.

- Add-ons schicken Infos, das Gedächtnis versieht sie mit Datum und Uhrzeit (mit Sekunden).
- Claude sortiert sie in Notizen über Personen, Orte, Projekte usw. ein, die über `[[Links]]` ein Netz bilden.
- Bei widersprüchlichen Infos gewinnt die zeitlich neuere, die ältere bleibt im Verlauf.
- Nachts wird aufgeräumt: Doppelte zusammenführen, verdichten, alte Tage zusammenfassen.
- Andere Add-ons lesen das Gedächtnis über eine kleine HTTP-Schnittstelle.

## Installation

Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories →
`https://github.com/fvh02/HA-Assistant-Brain` hinzufügen, dann **Gedächtnis** installieren.

Voraussetzung: das Inkbox Claude Gateway (ab 0.12.0) aus `fvh02/fabiaiagent` läuft, oder du trägst
einen eigenen Claude-Token ein. Details in [brain/DOCS.md](brain/DOCS.md).
