# Installationsanleitung

**E-Rechnungssystem** — Version 4.0

---

## Überblick

Das E-Rechnungssystem läuft als lokale Webanwendung in Ihrem Browser. Es wird
keine Datenbank und kein externer Server benötigt, alle Daten bleiben auf Ihrem
Rechner.

**Sie müssen nichts vorab installieren.** Python, Java, alle Programmpakete und
der offizielle KoSIT-Validator werden bei der Installation automatisch in den
Programmordner geladen und per SHA-256-Prüfsumme kontrolliert. Administrator­rechte
sind nicht nötig, am übrigen System wird nichts verändert.

Voraussetzung ist lediglich eine Internetverbindung während der Installation
(ca. 250 MB Download, 3–5 Minuten).

---

## Windows

### Schritt 1 — Software herunterladen

```
https://github.com/erechnung-b2b/install/archive/refs/heads/main.zip
```

Die Datei `install-main.zip` mit Rechtsklick → **Alle extrahieren…** entpacken,
z. B. nach `C:\E-Rechnungssystem\`.

### Schritt 2 — Installation

1. Den entpackten Ordner im Explorer öffnen
2. Doppelklick auf **erstinstallation.bat**
3. Erscheint eine Sicherheitswarnung: **Ausführen** bzw. **Weitere Informationen → Trotzdem ausführen**
4. Die Installation läuft automatisch:

```
[1/5] Python 3.12 einrichten...          OK
[2/5] Programmumgebung und Pakete...     OK alle Pakete installiert
[3/5] Java fuer den KoSIT-Validator...   OK Java bereit
[4/5] KoSIT-Validator...                 OK KoSIT-Validator einsatzbereit
[5/5] Abschluss...                       OK Installation abgeschlossen
```

### Schritt 3 — Programm starten

1. Doppelklick auf **starten.bat**
2. Der Browser öffnet sich automatisch mit **http://localhost:5000**

Programm beenden: Konsolenfenster schließen oder Strg+C drücken.

---

## macOS

### Weg A — ein Befehl (empfohlen)

1. **Terminal** öffnen (Programme → Dienstprogramme → Terminal)
2. Diese Zeile einfügen und Enter drücken:

```
curl -fsSL https://raw.githubusercontent.com/erechnung-b2b/install-mac/main/mac-installation.sh | bash
```

Das Programm wird nach `~/E-Rechnungssystem` installiert, auf dem Schreibtisch
erscheint **„E-Rechnung starten.command“**. Am Ende fragt die Installation, ob das
Programm gleich gestartet werden soll.

Derselbe Befehl bringt eine bestehende Installation später auf den neuesten Stand;
Ihre Daten werden vorher gesichert und bleiben erhalten.

### Weg B — Download und Doppelklick

1. Herunterladen und entpacken:
   `https://github.com/erechnung-b2b/install-mac/archive/refs/heads/main.zip`
2. Den Ordner an einen festen Ort verschieben (z. B. in *Dokumente*)
3. **Rechtsklick** auf **Installieren.command** → **Öffnen** → **Öffnen**

   Bei **macOS 15 (Sequoia) und neuer** gibt es diesen Weg nicht mehr. Dort:
   einmal doppelklicken, die Meldung schließen, dann
   **Systemeinstellungen → Datenschutz & Sicherheit → „Dennoch öffnen“**.
4. Die Installation läuft im Terminalfenster und bietet danach den Start an
5. Später starten mit Doppelklick auf **„E-Rechnung starten.command“**
   (beim ersten Mal ebenfalls Rechtsklick → Öffnen)

Der Browser öffnet sich mit **http://localhost:5000**. Beenden: Terminalfenster
schließen oder Strg+C.

---

## Linux

```
bash erstinstallation.sh
./starten.sh
```

Für den Betrieb auf einem Server im Netzwerk (Anmeldung, systemd, HTTPS) siehe
`docs/Server-Installation.md`.

---

## Update

| System | Vorgehen |
|---|---|
| Windows | Doppelklick auf **update.bat** |
| macOS | Installationsbefehl aus Weg A erneut ausführen, oder im Terminal `bash update.sh` |
| Linux | `bash update.sh` |

Vor jedem Update wird der Ordner `data/` nach `backup/backup-<Datum>` gesichert.
Python, Java und der KoSIT-Validator werden nicht erneut geladen.

---

## Testphase und Lizenz

### Kostenlose Testphase (28 Tage)

Nach dem ersten Start läuft die Software 28 Tage im vollen Funktionsumfang. Ein
Hinweis in der Kopfzeile zeigt die verbleibenden Tage an.

### Nach Ablauf der Testphase

Die Software wechselt in den Lesemodus. Vorhandene Rechnungen bleiben einsehbar,
neue Rechnungen erstellen, freigeben oder exportieren ist nicht mehr möglich.

### Lizenz aktivieren

1. Starten Sie das E-Rechnungssystem
2. Klicken Sie auf **Einstellungen** (Zahnrad-Symbol)
3. Im Abschnitt **Lizenz** sehen Sie Ihre **Geräte-ID** (10-stellige Zahl)
4. Teilen Sie diese Geräte-ID Ihrem Anbieter mit
5. Sie erhalten einen Lizenzschlüssel (beginnt mit `ERECH-`)
6. Geben Sie den Schlüssel ein und klicken Sie auf **Aktivieren**

Die Lizenz ist an Ihren Computer gebunden.

---

## Erste Schritte

### Rechnung hochladen

Klicken Sie auf **Posteingang** und ziehen Sie eine XML- oder PDF-Datei in das
Upload-Feld. Unterstützte Formate: XRechnung (XML) und ZUGFeRD / Factur-X (PDF mit
eingebettetem XML).

### Rechnung erstellen

Klicken Sie auf **Rechnung erstellen**, füllen Sie die Pflichtfelder aus und
erzeugen Sie eine **XRechnung** (XML) oder ein **ZUGFeRD-PDF**.

### Rechnung freigeben und exportieren

Öffnen Sie eine Rechnung, klicken Sie auf **Freigeben**, danach auf **DATEV-Export**.

---

## Daten und Backup

Alle Daten liegen im Unterordner `data/`:

```
data/
  invoices.json       Alle Rechnungen
  archiv/             Archivierte Originaldateien
  export/             DATEV- und CSV-Exporte
  license.json        Lizenzstatus
  device_id.txt       Geräte-ID
```

Sichern Sie den Ordner `data/` regelmäßig.

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| Download während der Installation schlägt fehl | Internetverbindung / Firmen-Proxy prüfen, Installation erneut starten – bereits geladene Teile werden übersprungen |
| „Prüfsumme stimmt nicht“ | Download wurde verfälscht oder abgebrochen – erneut starten; tritt es wiederholt auf, Virenscanner/Proxy prüfen |
| Browser öffnet sich nicht | http://localhost:5000 manuell öffnen |
| Port 5000 belegt (Mac: AirPlay-Empfänger) | Windows: `starten.bat 5050` · Mac/Linux: `./starten.sh 5050`, dann http://localhost:5050 |
| Mac: „kann nicht geöffnet werden“ | Rechtsklick → Öffnen, bzw. Systemeinstellungen → Datenschutz & Sicherheit → „Dennoch öffnen“ – oder Weg A verwenden |
| Mac: „Permission denied“ | im Programmordner `chmod +x *.sh *.command` |
| KoSIT-Validator fehlt | Installation erneut starten; ohne KoSIT prüft die Software mit dem eingebauten Prüfer |
| Programm defekt nach Update | Ordner `.venv` löschen und `erstinstallation` erneut ausführen – die Daten bleiben erhalten |

---

## Systemvoraussetzungen

| Komponente | Windows | macOS |
|---|---|---|
| Betriebssystem | Windows 10 (ab 1803) / 11, 64-Bit | macOS 12 (Monterey) oder neuer, Apple Silicon oder Intel |
| Vorinstallierte Software | keine | keine |
| RAM | mindestens 4 GB | mindestens 4 GB |
| Festplatte | ca. 500 MB | ca. 500 MB |
| Browser | Chrome, Edge oder Firefox | Chrome, Safari oder Firefox |

---

## Kontakt

**energieberatung rolf krause**
Dipl. Ing.
E-Mail: beratung@rolfkrause.com

---

*E-Rechnungssystem v4.0 — XRechnung | ZUGFeRD | EN 16931*
