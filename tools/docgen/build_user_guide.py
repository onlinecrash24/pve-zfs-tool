# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from pdf_common import build_guide, repo_version, DEFAULT_OUT_DIR

TITLE = "PVE ZFS Tool"
SUBTITLE = "Benutzerhandbuch"
META = [
    f"Version: entspricht Release {repo_version()}",
    "Zielgruppe: Anwender, die Proxmox-/ZFS-Umgebungen über die Weboberfläche verwalten",
]

CONTENT = [

# =====================================================================
("h1", "1. Einführung"),
("p", "Das PVE ZFS Tool ist eine Weboberfläche zur zentralen Verwaltung von ZFS-Speicher und "
      "Snapshots auf einem oder mehreren Proxmox-VE-Hosts. Es läuft als eigener Docker-Container "
      "und verbindet sich per SSH mit den Proxmox-Hosts — es muss auf den Hosts selbst nichts "
      "installiert werden, außer optional ein paar Zusatzwerkzeuge für einzelne Funktionen, die "
      "das Tool bei Bedarf mit einem Klick nachinstalliert."),
("p", "Mit dem Tool lassen sich Pools, Datasets und Snapshots verwalten, Replikationen zwischen "
      "Hosts einrichten und überwachen, Konfigurations-Backups erstellen und im Ernstfall "
      "wiederherstellen, sowie der Gesundheitszustand von Platten und Pools laufend beobachtet "
      "werden — inklusive Benachrichtigungen bei Problemen."),
("note", "Dieses Handbuch beschreibt die Bedienung aus Anwendersicht. Technische Details zur "
         "Architektur und den im Hintergrund ausgeführten Befehlen finden sich im separaten "
         "Administratorhandbuch."),

# =====================================================================
("h1", "2. Erste Schritte"),
("h2", "2.1 Anmelden"),
("p", "Nach dem Start des Containers ist die Oberfläche unter http://<Server-IP>:5000 "
      "erreichbar. Melden Sie sich mit den bei der Einrichtung vergebenen Zugangsdaten an."),
("h2", "2.2 SSH-Schlüssel auf die Proxmox-Hosts bringen"),
("p", "Auf der Startseite wird der öffentliche SSH-Schlüssel des Tools angezeigt. Dieser muss "
      "einmalig auf jedem Proxmox-Host, den Sie verwalten möchten, in die Datei "
      "~/.ssh/authorized_keys des Benutzers root eingetragen werden. Über den Button „Kopieren“ "
      "lässt sich der Schlüssel direkt in die Zwischenablage übernehmen."),
("h2", "2.3 Host hinzufügen"),
("numbered", [
    "Zum Menüpunkt „Hosts“ wechseln.",
    "Name, IP-Adresse, SSH-Port (Standard 22) und Benutzer (Standard root) eintragen.",
    "Über „Verbindung testen“ prüfen, ob der Host erreichbar ist.",
    "Host speichern — er erscheint danach in der Host-Auswahl in der Seitenleiste.",
]),
("h2", "2.4 Aktiven Host wählen"),
("p", "Die meisten Ansichten beziehen sich auf genau einen Host, den Sie oben in der "
      "Seitenleiste über das Dropdown „Aktiver Host“ auswählen. Ansichten wie „Disaster "
      "Recovery“ oder „PVE Config Restore“, die mehrere Hosts gleichzeitig betreffen, wählen "
      "Quelle und Ziel innerhalb der jeweiligen Ansicht separat aus."),

# =====================================================================
("h1", "3. Oberfläche im Überblick"),
("p", "Die linke Seitenleiste gliedert sich in vier Bereiche:"),
("bullets", [
    "Übersicht — Startseite und Hosts-Verwaltung",
    "ZFS — Pools, Datasets, Snapshots, Snapshot-Prüfung, Replikation, Disaster Recovery",
    "Proxmox — VMs & CTs, PVE Config Restore",
    "System — Zustand, Metriken, Audit-Log, Benachrichtigungen, KI-Berichte",
]),
("p", "Oben rechts in der Seitenleiste lässt sich die Sprache (Deutsch/Englisch) umschalten und "
      "man kann sich abmelden."),

# =====================================================================
("h1", "4. Startseite"),
("p", "Die Startseite zeigt den öffentlichen SSH-Schlüssel des Tools (mit Rotations-Funktion, "
      "falls der Schlüssel erneuert werden soll), ein Live-Dashboard mit dem Zustand aller "
      "registrierten Hosts und Pools sowie eine Übersicht über die verfügbaren Funktionsbereiche."),
("p", "Das Dashboard zeigt je Host, ob er erreichbar ist, wie viele Pools OK/mit Warnung/mit "
      "kritischem Zustand sind, ob eine Kapazitätswarnung vorliegt (Standard-Schwelle 90 %) und "
      "ob Auto-Snapshots veraltet sind. Ein Pool eines gerade nicht erreichbaren Hosts wird "
      "als „veraltet“ statt fälschlich als grün/ONLINE dargestellt."),

# =====================================================================
("h1", "5. Hosts"),
("p", "Verwaltung aller registrierten Proxmox-Hosts: Hinzufügen, Löschen, Verbindungstest. Für "
      "jeden Host gibt es außerdem:"),
("bullets", [
    "Backup — öffnet die Host-Config-Backup-Funktionen für genau diesen Host (siehe Kapitel 14)",
    "Wake-on-LAN — weckt einen offline erkannten Host per Magic Packet, sofern zuvor dessen "
    "MAC-Adresse automatisch erfasst wurde (während der Host online war)",
    "Standby — markiert einen Host als „erwartet offline“ (z. B. ein Backup-Server, der meist "
    "ausgeschaltet ist und per WOL geweckt wird): keine Offline-Benachrichtigungen mehr für "
    "seine An-/Aus-Zyklen, neutrales graues Standby-Badge statt rot, und die Hosts-Kachel im "
    "Dashboard bleibt grün. Solange der Host wach ist, wird er ganz normal überwacht.",
]),
("note", "Beim Löschen eines Hosts wird sein kompletter Überwachungs-Zustand mit entfernt "
         "(Offline-Status, Pool-Zustände, Snapshot-Warnzähler, Replikations-Status), sodass "
         "keine veralteten Einträge im Dashboard zurückbleiben."),

# =====================================================================
("h1", "6. Pools"),
("p", "Zeigt alle ZFS-Pools des aktiven Hosts mit Größe, Belegung, Fragmentierung, Health-"
      "Status und Dedup-Ratio."),
("bullets", [
    "Status/Details — vollständige zpool status-Ausgabe inkl. Vdev-Baum",
    "I/O-Statistik — aktuelle Lese-/Schreib-Auslastung",
    "Scrub starten — löst eine Datenintegritätsprüfung aus; das Tool überwacht den Fortschritt "
    "im Hintergrund und benachrichtigt bei Abschluss",
    "Autotrim / Autoexpand — Pool-Eigenschaften per Schalter ein-/ausschalten",
    "Historie — Verlauf der auf dem Pool durchgeführten Operationen",
    "Feature-Upgrade — zeigt an, ob neue ZFS-Pool-Features verfügbar sind, und erlaubt das "
    "Upgrade (nicht rückgängig zu machen — ältere ZFS-Versionen können den Pool danach nicht "
    "mehr importieren)",
]),

# =====================================================================
("h1", "7. Datasets"),
("p", "Listet alle Dateisysteme und Volumes (Zvols) des aktiven Hosts bzw. eines gewählten "
      "Pools mit Belegung, Kompressionsrate und Mountpoint."),
("bullets", [
    "Anlegen — neues Dataset erstellen, optional mit Properties (z. B. com.sun:auto-snapshot=false)",
    "Properties — alle ZFS-Eigenschaften einsehen und einzelne bearbeiten",
    "Löschen — Dataset entfernen (optional rekursiv mit allen Kindern)",
    "Auto-Snapshot-Spalte — zeigt und steuert com.sun:auto-snapshot je Dataset direkt in der "
    "Tabelle: An / Aus / Erben (vom übergeordneten Dataset übernehmen). Ist der Wert vom "
    "Elternobjekt vererbt, wird das Umschalten je nach gewählter Option bestätigt oder — falls "
    "sinnlos — verhindert.",
]),

# =====================================================================
("h1", "8. Snapshots"),
("p", "Interaktive Zeitleiste und Tabellenansicht aller Snapshots des aktiven Hosts."),
("bullets", [
    "Erstellen — manueller Snapshot eines Datasets, optional rekursiv",
    "Klonen — erzeugt ein beschreibbares Dataset aus einem Snapshot; bei Ziel auf einem anderen "
    "Pool wird automatisch über ein Sende-/Empfangsverfahren geklont",
    "Rollback — setzt ein Dataset auf den Stand eines Snapshots zurück. Bei VM-/CT-Snapshots "
    "kann automatisch die betroffene VM/CT vor dem Rollback gestoppt und danach wieder "
    "gestartet werden.",
    "Diff — zeigt geänderte Dateien seit dem Snapshot (Dateisysteme) bzw. eine "
    "Änderungsschätzung (Zvols/VM-Disks)",
    "Löschen — entfernt einen Snapshot; hängen noch Restore-Klone daran, werden diese "
    "automatisch mit entfernt",
]),
("h2", "8.1 Datei-Wiederherstellung aus LXC-Snapshots"),
("p", "Für Container-Snapshots (Dateisysteme) lässt sich der Snapshot schreibgeschützt mounten "
      "und einzelne Dateien oder ganze Verzeichnisse per Datei-Browser ansehen, als Vorschau "
      "laden (Textdateien bis 100 KB) und gezielt in den laufenden Container zurückkopieren. "
      "Beim Schließen des Browsers wird der temporäre Restore-Mount automatisch entfernt."),
("h2", "8.2 Datei-Wiederherstellung aus VM-Snapshots"),
("p", "Für VM-Disk-Snapshots (Zvols) werden die enthaltenen Partitionen automatisch erkannt "
      "(ext4, xfs, btrfs, NTFS, vfat) und einzeln gemountet. BitLocker-/LUKS-verschlüsselte "
      "Partitionen werden erkannt, aber als nicht mountbar markiert. Auch hier stehen Vorschau "
      "und Download einzelner Dateien zur Verfügung."),
("note", "Für die VM-Datei-Wiederherstellung muss auf dem Proxmox-Host das Paket kpartx "
         "installiert sein (für NTFS-Partitionen zusätzlich ntfs-3g). Fehlt es, weist das Tool "
         "mit dem passenden Installationshinweis darauf hin."),

# =====================================================================
("h1", "9. Snapshot-Prüfung"),
("p", "Analysiert automatisch, ob die konfigurierte Snapshot-Strategie tatsächlich eingehalten "
      "wird — getrennt nach Ebene (frequent, hourly, daily, weekly, monthly)."),
("h2", "9.1 Retention-Editor"),
("p", "Zeigt den aktuell konfigurierten „Behalten“-Wert (--keep=N) je Ebene an und erlaubt das "
      "direkte Bearbeiten sowie das Aktivieren/Deaktivieren einzelner Ebenen — die Änderung wird "
      "unmittelbar in die zugrunde liegenden Cron-Dateien geschrieben, mit automatischer "
      "Sicherungskopie. Ist zfs-auto-snapshot auf dem Host noch nicht installiert, bietet die "
      "Karte einen Ein-Klick-Installations-Button."),
("h2", "9.2 Analyse je Ebene"),
("bullets", [
    "Gesamtzahl der Snapshots, Anzahl betroffener Datasets, Durchschnitt, Alter des neuesten "
    "Snapshots",
    "Lücken-Erkennung — findet Löcher in der Snapshot-Kette, die größer sind als der übliche "
    "Rhythmus dieses Datasets (kein starrer Wert, sondern aus den tatsächlichen Abständen "
    "berechnet)",
    "Veraltete Datasets — warnt, wenn der neueste Snapshot ein Alterslimit überschreitet",
    "Soll/Ist-Vergleich — vergleicht die tatsächliche Snapshot-Anzahl mit dem konfigurierten "
    "Behalten-Wert. Replikations-Ziele (deren Snapshot-Kette der Quelle folgt) werden hier "
    "ausdrücklich nicht mitgezählt, sondern als eigene Kennzahl separat ausgewiesen.",
    "Fehlende Labels — Datasets, denen eine erwartete Ebene komplett fehlt",
    "Manuelle Snapshots — Snapshots, die keinem bekannten Auto-Snapshot-Muster entsprechen",
]),
("h2", "9.3 Snapshot-Tags auswählen"),
("p", "Falls mehrere Replikationen mit unterschiedlichen Snapshot-Namensmustern auf demselben "
      "Host existieren, lassen sich hier die tatsächlich relevanten Tags per Checkliste "
      "auswählen — die Prüfung berücksichtigt danach nur noch diese."),

# =====================================================================
("h1", "10. Replikation (bashclub-zsync)"),
("p", "Richtet Ein-Weg-Replikationen zwischen zwei Hosts ein und überwacht deren Gesundheit."),
("h2", "10.1 Einrichtungs-Assistent"),
("p", "Der Assistent führt in klar getrennten Schritten durch die Einrichtung eines "
      "Replikations-Paares: Quell-/Ziel-Host wählen → Einrichtung → Datasets → Konfiguration → "
      "Log. Vor jedem Schritt zeigt eine Grün-/Rot-Ampel, was auf beiden Hosts bereits vorhanden "
      "ist (PVE erkannt, Paket installiert, SSH-Vertrauen eingerichtet) — der Button "
      "„Installieren & SSH einrichten“ führt danach nur noch die tatsächlich fehlenden Schritte "
      "aus."),
("bullets", [
    "Datasets/Zvols der Quelle werden per Checkliste für die Replikation markiert",
    "Für das Ziel kann ein vorhandenes Dataset gewählt oder neu angelegt werden",
    "Das Konfigurationsformular deckt alle bashclub-zsync-Einstellungen ab (SSH-Port, Tag, "
    "Snapshot-Filter, Behalten-Anzahl, checkzfs-Parameter); leere Felder übernehmen automatisch "
    "sinnvolle Standardwerte",
    "Ein Zeitplan-Manager bietet gängige Cron-Vorlagen (alle 15/30 Min., stündlich, alle 2/6 "
    "Std., täglich) sowie eine freie Eingabe",
]),
("h2", "10.2 Übersicht & Gesundheit"),
("p", "Alle konfigurierten Paare eines Hosts werden mit Status (OK/Warnung/Kritisch/wartet), "
      "letztem Sync-Zeitpunkt und Verzögerung (Lag) angezeigt. Der Status wird automatisch alle "
      "15 Minuten neu berechnet; bei einem Wechsel auf Warnung oder Kritisch (und bei Erholung) "
      "wird eine Benachrichtigung verschickt. Über den checkzfs-Bereich lässt sich zusätzlich "
      "eine ausführliche OK/WARN/CRIT-Tabelle des upstream-Prüfwerkzeugs einsehen."),
("h2", "10.3 Löschen"),
("p", "Entfernt Cron-Eintrag und Konfigurationsdatei (mit Sicherungskopie). Optional lassen "
      "sich zusätzlich alle vom zsync erzeugten Snapshots unterhalb des Ziels löschen — die "
      "Datasets selbst bleiben dabei erhalten."),

# =====================================================================
("h1", "11. Disaster Recovery"),
("p", "Sendet ein Replikat zurück auf einen (wiederhergestellten) Quell-Host — für den Fall, "
      "dass die ursprüngliche Quelle verloren ging und neu aufgesetzt wurde."),
("numbered", [
    "Replikations-Paar auswählen (alle bekannten Paare über alle Hosts hinweg)",
    "Das genaue Replikat-Dataset wählen, das zurückgesendet werden soll",
    "Ziel-Host bestimmen — ein registrierter Host oder eine freie Adresse/Port/Benutzer (falls "
    "der wiederhergestellte Host eine neue IP hat)",
    "Snapshot wählen (Standard: der neueste) und optional „Überschreiben erzwingen“ aktivieren",
    "„Reverse-Sync starten“ — läuft im Hintergrund mit Live-Fortschrittsanzeige",
]),
("note", "Bevor der Sync startet, prüft das Tool automatisch, ob das Zieldataset auf der Quelle "
         "noch existiert und eigene Snapshots hat. Ist das der Fall (die Quelle ist also noch "
         "intakt und hat eigene Live-Daten), erscheint eine deutliche Warnung — ein "
         "Zurücksenden ist dann in der Regel gar nicht nötig und würde von ZFS ohnehin "
         "verweigert werden, um die Live-Daten zu schützen."),
("h2", "11.1 Nach dem Reverse-Sync: Guest-Konfiguration wiederherstellen"),
("p", "Der Reverse-Sync bringt ausschließlich die Festplatten-Daten zurück. Proxmox zeigt die "
      "VM oder den Container erst wieder an, wenn auch dessen Konfigurationsdatei existiert. "
      "Dafür gibt es direkt im Anschluss an den Reverse-Sync einen eigenen Bereich: VMID und Typ "
      "werden automatisch aus dem gewählten Dataset abgeleitet, ein passendes Host-Config-"
      "Backup wird ausgewählt, die enthaltene Konfiguration lässt sich vor dem Zurückspielen "
      "einsehen. Eine vorhandene Konfiguration wird nur überschrieben, wenn dies ausdrücklich "
      "bestätigt wird."),

# =====================================================================
("h1", "12. PVE Config Restore"),
("p", "Baut einen frisch installierten Proxmox-Host wieder auf den Konfigurationsstand eines "
      "früheren Hosts auf — ohne dass ein komplettes System-Backup zurückgespielt werden muss. "
      "Zu finden unter Proxmox → PVE Config Restore."),
("h2", "12.1 Ziel und Backup wählen"),
("p", "Es gibt zwei Möglichkeiten, den Ziel-Host anzusprechen:"),
("bullets", [
    "Registrierter Host — ein bereits im Tool angelegter Host",
    "Anderer Host (IP + Zugangsdaten) — für ein frisch installiertes PVE, das noch gar nicht "
    "registriert ist. Es genügen IP-Adresse, Benutzer und Passwort; diese werden ausschließlich "
    "für diese eine Verbindung verwendet und nirgends gespeichert. Der neue SSH-Schlüssel eines "
    "neu installierten Hosts wird dabei automatisch akzeptiert. Über „Verbindung testen“ lässt "
    "sich die Erreichbarkeit vorab prüfen, über „Tool-SSH-Key einrichten“ der normale, "
    "passwortlose Zugang für die Zukunft einrichten — danach ist der ursprünglich registrierte "
    "Host (gleiche Adresse) auch außerhalb dieser Ansicht wieder erreichbar.",
]),
("p", "Als Quelle wird eines der gespeicherten Host-Config-Backups gewählt (siehe Kapitel 14) — "
      "auch Backups eines anderen, ggf. nicht mehr existierenden Hosts."),
("h2", "12.2 Backup durchsuchen & gezielt wiederherstellen"),
("p", "Die im Backup enthaltenen Dateien werden übersichtlich kategorisiert dargestellt:"),
("bullets", [
    "Gäste (VM/CT-Konfigurationen)",
    "Netzwerk (Interfaces, NIC-Zuordnung)",
    "Storage (storage.cfg)",
    "Paketquellen (APT-Repositories und Signing-Keys)",
    "User & Datacenter",
    "SSH-Zugang (authorized_keys)",
    "Firewall",
    "Jobs & Cron (u. a. Snapshot-Retention, Replikations-Konfiguration)",
    "Sonstiges /etc/pve",
    "System-Infos (nur zur Ansicht, nicht wiederherstellbar — z. B. die erfasste Paketliste)",
]),
("p", "Jede Datei lässt sich vor dem Zurückspielen ansehen. Eine bereits vorhandene Datei wird "
      "nur überschrieben, wenn „Vorhandene Dateien überschreiben“ aktiviert ist."),
("h2", "12.3 Alle Gast-Konfigurationen auf einmal"),
("p", "Über einen einzelnen Button lassen sich alle im Backup enthaltenen VM-/CT-"
      "Konfigurationen gebündelt wiederherstellen — bereits vorhandene werden dabei "
      "übersprungen, sofern nicht ausdrücklich das Überschreiben aktiviert wurde."),
("h2", "12.4 Pakete nachinstallieren"),
("p", "Wendet die im Backup gesicherte Paketliste an (nur Pakete, die installiert bzw. "
      "festgehalten waren — es werden nie Pakete entfernt) und installiert die fehlenden nach. "
      "Läuft als Hintergrund-Vorgang, da dies je nach Anzahl der Pakete etwas dauern kann."),
("warn", "Vor dem Nachinstallieren der Pakete müssen zuerst die Paketquellen (APT) "
         "wiederhergestellt werden — sonst kennt der frische Host die benötigten Repositories "
         "noch nicht."),
("h2", "12.5 Empfohlener Ablauf nach einer Neuinstallation"),
("numbered", [
    "Proxmox VE frisch installieren (möglichst gleiche Version)",
    "PVE Config Restore öffnen, Ad-hoc-Ziel mit IP + Passwort des frischen Hosts wählen",
    "Netzwerk-Konfiguration wiederherstellen (danach ggf. Neustart/Reload nötig)",
    "Paketquellen (APT) wiederherstellen, dann Pakete nachinstallieren",
    "Storage-Konfiguration, alle Gast-Konfigurationen und SSH-Zugang wiederherstellen",
    "In Disaster Recovery die VM-/CT-Festplatten per Reverse-Sync zurückholen",
    "Gäste starten",
]),

# =====================================================================
("h1", "13. VMs & Container"),
("p", "Übersicht aller VMs und LXC-Container des aktiven Hosts mit Status und "
      "Lebenszyklus-Steuerung (Start, sauberes Herunterfahren, Neustart, harter Stopp — "
      "eingreifende Aktionen erfordern eine Bestätigung)."),
("h2", "13.1 Replikations-Status"),
("p", "Eine eigene Spalte zeigt für jeden Gast auf einen Blick, ob er repliziert wird:"),
("bullets", [
    "Grün (Haken) — alle Festplatten des Gastes sind für die Replikation markiert, und die "
    "Replikation ist nicht im Rückstand",
    "Gelb (Ausrufezeichen) — entweder sind nur einzelne Festplatten markiert (eine Disk würde "
    "im Replikat fehlen), oder die Replikation der Quelle hängt hinterher",
    "Rot (Kreuz) — keine Festplatte des Gastes ist für die Replikation markiert",
]),
("h2", "13.2 Snapshots je Gast"),
("p", "Direkt aus der Gäste-Liste lassen sich die zu einer VM oder einem Container gehörenden "
      "ZFS-Snapshots einsehen und neue Snapshots anlegen."),

# =====================================================================
("h1", "14. Host-Config-Backup"),
("p", "Sichert die Konfiguration eines Proxmox-Hosts — ausdrücklich NICHT die VM-/CT-"
      "Festplatten selbst. Zu finden über den Button „Backup“ bei einem Host in der "
      "Hosts-Ansicht."),
("h2", "14.1 Inhalt"),
("p", "Ein Backup enthält alles, was für eine vollständige Wiederherstellung der Host-"
      "Konfiguration nötig ist: die Proxmox-Cluster-Konfiguration (/etc/pve, u. a. Storage, "
      "Firewall, User, Gäste-Konfigurationen), Netzwerk-Einstellungen samt der Zuordnung von "
      "Netzwerkkarten-Namen zu physischen Geräten, Paketquellen (APT), die öffentlichen "
      "SSH-Schlüssel unter authorized_keys, die Snapshot-Retention-Konfiguration, die "
      "Replikations-Konfiguration und das ARC-Limit."),
("h2", "14.2 Erstellen & Zeitplan"),
("bullets", [
    "Jetzt erstellen — löst sofort ein Backup aus",
    "Zeitplan — täglich, wöchentlich oder monatlich mit einer „Behalten“-Anzahl (ältere Backups "
    "werden automatisch entfernt); ein fehlgeschlagenes geplantes Backup löst eine "
    "Benachrichtigung aus",
    "Geheimnisse einschließen — standardmäßig deaktiviert. Ein optionaler Schalter schließt "
    "zusätzlich /etc/pve/priv (u. a. den privaten Cluster-CA-Schlüssel) ein; dieser Bereich ist "
    "hochsensibel und sollte nur bewusst aktiviert werden.",
]),
("h2", "14.3 Gespeicherte Backups"),
("p", "Alle Backups eines Hosts lassen sich einsehen, herunterladen (nur nach Login möglich) "
      "oder löschen. Für die Wiederherstellung siehe Kapitel 12 (PVE Config Restore)."),

# =====================================================================
("h1", "15. Zustand"),
("p", "Sammelt Gesundheits- und Diagnose-Informationen des aktiven Hosts an einem Ort."),
("bullets", [
    "ARC-Statistik — Trefferquote (mit Ampel: ab 90 % grün, ab 80 % orange, darunter rot), "
    "rohe Treffer/Fehlversuche als Kontext",
    "ARC-Limit-Editor — aktuelle Größe, Laufzeit- und persistentes Limit; ein neues Limit lässt "
    "sich über drei Richtwert-Buttons setzen (Minimum, Empfohlen, Maximum) oder frei eingeben",
    "ZFS-Ereignisse — jüngste Kernel-Events",
    "SMART-Status — Plattengesundheit je Pool; fehlt smartmontools, bietet die Ansicht die "
    "Installation an",
    "Restore-Bereinigung — übrig gebliebene Restore-Klone (LXC) bzw. Zvol-Mounts/kpartx-"
    "Zuordnungen (VM) anzeigen und mit einem Klick vollständig bereinigen",
    "ZDB-Tiefendiagnose — wird automatisch für Pools angeboten, die nicht im Zustand ONLINE "
    "sind",
]),

# =====================================================================
("h1", "16. Metriken"),
("p", "Zeitlicher Verlauf der wichtigsten Kennzahlen, alle 15 Minuten im Hintergrund erfasst "
      "(Zeitraum wählbar: 6 Std. / 24 Std. / 7 Tage / 30 Tage / 90 Tage)."),
("h2", "16.1 Pool-Verlauf"),
("p", "Belegung, Fragmentierung und belegter Speicherplatz je Pool als Diagramm, mit dem "
      "aktuellen Wert prominent und einer Ampel bei der Belegung."),
("h2", "16.2 Datenträger (SMART)"),
("p", "Für jede physische Platte werden Temperatur (mit Ampel: bei Festplatten ab 45 °C "
      "orange/ab 55 °C rot, bei SSD/NVMe ab 60 °C orange/ab 70 °C rot), SMART-Gesundheit, "
      "Verschleiß, reallocated/pending Sektoren und Betriebsstunden angezeigt, inklusive "
      "Temperatur-Trendlinie. Fehlt smartmontools auf dem Host, bietet die Karte eine "
      "Ein-Klick-Installation an."),

# =====================================================================
("h1", "17. Audit-Log"),
("p", "Protokolliert jede sicherheitsrelevante oder verändernde Aktion: Anmeldungen, "
      "gelöschte/zurückgerollte Snapshots, Replikations-Änderungen, Wiederherstellungen usw. — "
      "mit Zeitstempel, Benutzer, IP-Adresse, betroffenem Host und Erfolg/Fehlschlag. Filterbar "
      "nach Aktion, Host, Benutzer, Zeitraum sowie „nur Fehlschläge“."),

# =====================================================================
("h1", "18. Benachrichtigungen"),
("p", "Konfiguriert, über welche Kanäle das Tool bei relevanten Ereignissen (Pool-Probleme, "
      "Kapazitätswarnung, veraltete Snapshots, Replikations-Verzögerung, fehlgeschlagene "
      "Backups, Scrub-Abschluss …) informiert. Unterstützt werden Telegram, Gotify, Matrix und "
      "E-Mail — jeder Kanal einzeln aktivierbar, mit einer Testnachricht-Funktion. Bereits "
      "gespeicherte Token/Passwörter werden in der Oberfläche maskiert angezeigt."),

# =====================================================================
("h1", "19. KI-Berichte"),
("p", "Erstellt auf Wunsch oder nach Zeitplan einen von einer KI (OpenAI, Anthropic/Claude oder "
      "eine selbst gehostete Ollama-Instanz) verfassten Statusbericht über die ZFS-Umgebung "
      "eines Hosts oder aller Hosts zusammen — mit einer klaren OK-/Warnung-/Kritisch-"
      "Einschätzung je Abschnitt, basierend auf den tatsächlich erfassten Daten. Berichte lassen "
      "sich als PDF herunterladen oder direkt an die konfigurierten Benachrichtigungskanäle "
      "senden. Über den Chat-Bereich lassen sich Rückfragen zum zuletzt erstellten Bericht "
      "stellen."),

# =====================================================================
("h1", "20. Tipps & häufige Fragen"),
("h3", "Ein Host wird als offline angezeigt, ist aber erreichbar."),
("p", "Über „Verbindung testen“ in der Hosts-Ansicht lässt sich die SSH-Verbindung gezielt "
      "prüfen. Häufigste Ursache: ein geänderter SSH-Schlüssel auf dem Host (z. B. nach "
      "Neuinstallation) oder ein blockierter Port."),
("h3", "Warum wird bei der Replikation gewarnt, obwohl doch alles wie gewünscht läuft?"),
("p", "Die Vorabprüfung beim Disaster-Recovery-Reverse-Sync warnt bewusst, wenn die Quelle noch "
      "eigene, aktuelle Daten hat — das ist der Normalzustand bei einer intakten, laufenden "
      "Replikation und kein Fehler."),
("h3", "Wie viel Speicherplatz belegen die historischen Daten des Tools selbst?"),
("p", "Die Aufbewahrungsdauer für Metriken (Standard 90 Tage) und Audit-Log (Standard 365 Tage) "
      "lässt sich über Umgebungsvariablen anpassen; alte Daten werden automatisch entfernt."),
("h3", "Kann ich das Tool auf Deutsch als Standardsprache ausliefern?"),
("p", "Ja — über die Umgebungsvariable DEFAULT_LANG lässt sich beim Deployment de oder en als "
      "Standardsprache für neue Besucher festlegen; jeder Benutzer kann trotzdem individuell "
      "umschalten."),

# =====================================================================
("h1", "21. Glossar"),
("table",
    ["Begriff", "Bedeutung"],
    [
        ["Pool", "Ein ZFS-Speicherverbund aus einer oder mehreren Festplatten"],
        ["Dataset", "Ein Dateisystem oder Volume innerhalb eines Pools"],
        ["Zvol", "Ein ZFS-Volume — wird meist als virtuelle Festplatte für VMs verwendet"],
        ["Snapshot", "Ein unveränderlicher Zeitpunkt-Zustand eines Datasets"],
        ["Klon", "Ein beschreibbares Dataset, das aus einem Snapshot erzeugt wurde"],
        ["Rollback", "Zurücksetzen eines Datasets auf den Stand eines Snapshots"],
        ["Replikation", "Regelmäßiges Übertragen von Snapshots von einer Quelle zu einem Ziel-Host"],
        ["Reverse-Sync", "Zurücksenden eines Replikats an eine (wiederhergestellte) Quelle"],
        ["ARC", "Adaptive Replacement Cache — der Arbeitsspeicher-Cache von ZFS"],
        ["SMART", "Selbstdiagnose-Funktion moderner Festplatten/SSDs (Temperatur, Gesundheit, Verschleiß)"],
        ["VMID", "Eindeutige numerische ID einer VM oder eines Containers in Proxmox"],
    ],
    [], [42*72/25.4, None],
),
]

if __name__ == "__main__":
    os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)
    out = os.path.join(DEFAULT_OUT_DIR, "PVE-ZFS-Tool_Benutzerhandbuch.pdf")
    build_guide(out, TITLE, SUBTITLE, META, CONTENT)
    print("OK:", out)
