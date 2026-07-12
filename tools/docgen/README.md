# docgen — Admin- und Benutzerhandbuch als PDF

Erzeugt die beiden PDF-Handbücher des Projekts aus reinem Python (reportlab),
ohne externe Systemabhängigkeiten (kein wkhtmltopdf/weasyprint/pandoc nötig).

- `docs/PVE-ZFS-Tool_Administratorhandbuch.pdf` — Architektur + vollständige
  Befehlsreferenz (welcher SSH-/Shell-Befehl bzw. externe API-Aufruf hinter
  jeder Aktion in der Oberfläche steckt)
- `docs/PVE-ZFS-Tool_Benutzerhandbuch.pdf` — anwenderorientierte Bedienungs-
  anleitung, ohne technische Details

Beide Ausgabedateien landen im `docs/`-Ordner im Repo-Root, der bereits in
`.gitignore` steht — die PDFs selbst werden also nicht ins Git-Repo committet
(kein Bloat durch Binärdateien in der Historie), nur die Erzeugungsskripte
hier. Stattdessen hängen die PDFs als **Assets an jedem GitHub Release** —
siehe „Release-Prozess" unten.

## Verwendung

```bash
pip install -r tools/docgen/requirements.txt
python tools/docgen/build.py
```

Erzeugt beide PDFs in einem Rutsch. Einzeln geht es auch:

```bash
python tools/docgen/build_admin_guide.py
python tools/docgen/build_user_guide.py
```

Die Skripte müssen aus einem Checkout dieses Repos heraus laufen (sie leiten
den Ausgabeordner relativ zu ihrem eigenen Pfad her: `tools/docgen/../../docs`).

## Aufbau

| Datei | Zweck |
|---|---|
| `pdf_common.py` | Gemeinsame reportlab-Engine: Farbschema, Absatz-/Tabellen-/Code-Box-Stile, Titelseite, zweistufiger Aufbau für ein echtes Inhaltsverzeichnis mit Seitenzahlen, PDF-Lesezeichen, Kopf-/Fußzeile |
| `build_admin_guide.py` | Inhalt des Administratorhandbuchs als reine Datenstruktur (`CONTENT`-Liste) |
| `build_user_guide.py` | Inhalt des Benutzerhandbuchs als reine Datenstruktur |
| `build.py` | Baut beide Guides nacheinander |

Der Inhalt jeder Datei ist eine Liste von Tupeln, kein HTML/Markup:

```python
("h1", "Überschrift"),
("p", "Fließtext ..."),
("bullets", ["Punkt 1", "Punkt 2"]),
("numbered", ["Schritt 1", "Schritt 2"]),
("cmd", "Aktion", "Beschreibung", ["befehl 1", "befehl 2"]),   # Befehls-Box
("note", "Hinweistext"),
("warn", "Warnungstext"),
("table", ["Spalte1", "Spalte2"], [["a", "b"]], mono_cols, col_widths),
("pagebreak",),
```

`render_content()` in `pdf_common.py` übersetzt das in reportlab-Flowables.
Neue Blocktypen lassen sich dort zentral ergänzen.

## Version in der Titelzeile

Die Titelseite zeigt automatisch `git describe --tags --always` des aktuellen
Checkouts (`repo_version()` in `pdf_common.py`) — bei einem Release-Tag also
z. B. `v0.9.891`, sonst den nächstgelegenen Tag mit Commit-Suffix. Es muss
dafür **nichts manuell gepflegt werden**; ein Neubau nach jedem Release-Tag
liefert automatisch die korrekte Versionszeile.

Steht der Checkout einen oder mehrere Commits NACH dem Release-Tag (z. B. weil
docgen-Änderungen selbst erst nach dem Tag committet wurden), zeigt
`git describe` stattdessen `vX.Y.Z-N-gabc1234`. Für ein sauberes „vX.Y.Z" in
diesem Fall die Version explizit anpinnen:

```bash
DOCGEN_VERSION=v0.9.891 python tools/docgen/build.py
```

## Release-Prozess: PDFs als GitHub-Release-Assets

Die PDFs werden **nicht** ins Repo committet (siehe oben), sondern bei jedem
main-Release als Anhang am GitHub-Release hochgeladen. Ablauf direkt nach dem
Taggen eines Releases (main gemerged, Tag gesetzt, gepusht):

```bash
# 1. PDFs mit exakt dieser Release-Version bauen
DOCGEN_VERSION=v0.9.891 python tools/docgen/build.py

# 2. GitHub-Release anlegen (falls noch nicht vorhanden) und PDFs anhängen
gh release create v0.9.891 \
  --title "v0.9.891" \
  --notes-file <datei-mit-release-notes>.md \
  "docs/PVE-ZFS-Tool_Administratorhandbuch.pdf" \
  "docs/PVE-ZFS-Tool_Benutzerhandbuch.pdf"

# Existiert der Release bereits (z. B. weil Notes schon gesetzt sind) und nur
# neue PDF-Versionen sollen ran:
gh release upload v0.9.891 \
  "docs/PVE-ZFS-Tool_Administratorhandbuch.pdf" \
  "docs/PVE-ZFS-Tool_Benutzerhandbuch.pdf" \
  --clobber
```

Als Release-Notes eignet sich die kuratierte Merge-Commit-Message des
Release-Merges (`git show -s --format='%B' <merge-commit>`) — sie beschreibt
bereits die Änderungen in Prosa.

`gh` (GitHub CLI) muss installiert und angemeldet sein (`gh auth login`,
Browser-Device-Flow — der Token bleibt bei `gh`, wird nie im Klartext
sichtbar).

## Wann neu bauen?

Immer dann, wenn sich an einem der folgenden Punkte etwas ändert:

- ein neues Feature / eine neue Ansicht kommt hinzu (→ Benutzerhandbuch
  ergänzen, ggf. Kapitel 6 des Administratorhandbuchs um die neuen Befehle)
- ein SSH-/Shell-Befehl in `app/*.py` ändert sich (z. B. neue Flags, neue
  Repo-URL wie beim bashclub-GPG-Key-Umzug) → Kapitel 6 aktualisieren
- eine neue Umgebungsvariable kommt hinzu → Kapitel 8.4 (Admin) ergänzen
- ein neuer API-Endpunkt kommt hinzu → Kapitel 9 (Admin, Anhang) ergänzen

Am einfachsten: beim Vorbereiten eines Release-Bündelns kurz durchsehen, ob
seit dem letzten Guide-Update neue Features dazugekommen sind, den passenden
`CONTENT`-Abschnitt ergänzen, dann `python tools/docgen/build.py` laufen
lassen und die neuen PDFs im `docs/`-Ordner prüfen.

## Hinweise

- Beide Guides sind aktuell **nur auf Deutsch**. Für eine englische Fassung
  am einfachsten `build_admin_guide_en.py` / `build_user_guide_en.py` mit
  übersetztem `CONTENT` anlegen — `pdf_common.py` ist sprachneutral.
- reportlab ist bewusst **nicht** in der Haupt-`requirements.txt` der App
  gelistet (wird zur Laufzeit nicht gebraucht, nur für die Doku-Erzeugung) —
  daher die eigene `requirements.txt` in diesem Ordner.
- Die Skripte rendern mit reportlabs eingebauten Basis-Fonts (Helvetica/
  Courier, WinAnsi-Encoding) — Umlaute und ß funktionieren ohne zusätzliche
  Font-Dateien.
