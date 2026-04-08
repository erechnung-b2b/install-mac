# KoSIT-Validator einrichten

Der **KoSIT-Validator** ist das offizielle Prüftool für XRechnung-Konformität,
herausgegeben von der Koordinierungsstelle für IT-Standards. Die E-Rechnungs-
Software kann ihn optional aufrufen, um erzeugte XRechnungs-XML-Dateien
gegen die tagesaktuellen Geschäftsregeln zu prüfen (Pflichtenheft P-04,
FR-210, BMF-Empfehlung).

Ohne installierten KoSIT-Validator arbeitet die Software mit dem eingebauten
`validator.py` — der ist schnell, deckt aber nur einen Kernsatz an Regeln ab.
Für produktive Ausgangsrechnungen empfehlen wir, den KoSIT-Validator zusätzlich
einzurichten.

## 1. Java installieren

Der Validator ist eine Java-Anwendung und benötigt **Java 11 oder neuer**.

- **Windows:** [Eclipse Temurin JRE 17](https://adoptium.net/de/temurin/releases/)
  herunterladen und installieren. Wichtig: Während der Installation
  *"Set JAVA_HOME variable"* und *"Add to PATH"* aktivieren.
- **macOS:** `brew install temurin` (erfordert Homebrew)
- **Linux:** `sudo apt install default-jre` oder entsprechend der Distribution

Prüfen mit:

    java -version

sollte etwas wie `openjdk version "17.0.x"` ausgeben.

## 2. Validator herunterladen

Zwei Artefakte werden gebraucht:

1. **Validator (JAR):** <https://github.com/itplr-kosit/validator/releases>
   Die Datei heißt etwa `validator-<version>-standalone.jar`.
2. **Szenario-Konfiguration XRechnung:**
   <https://github.com/itplr-kosit/validator-configuration-xrechnung/releases>
   Die Datei heißt etwa `validator-configuration-xrechnung_3.0.x_2024-xx-xx.zip`.

## 3. Dateistruktur anlegen

Entpacke die Szenario-ZIP in einen Ordner und lege den Validator-JAR dazu.
Empfohlener Pfad unter Windows:

    C:\Users\Rolf\Claude\erechnung-komplett\erechnung\tools\kosit\
    ├── validator-<version>-standalone.jar
    ├── scenarios.xml
    ├── resources\
    └── ... (weitere Dateien aus dem entpackten ZIP)

Die E-Rechnungs-Software sucht automatisch in folgenden Pfaden:

1. Pfad aus `mandant_settings.json` (Schlüssel `kosit_validator_path`)
2. Umgebungsvariable `KOSIT_VALIDATOR_PATH`
3. `./tools/kosit/` (relativ zum Arbeitsverzeichnis)
4. `./kosit/`
5. `/opt/kosit/`

## 4. Pfad konfigurieren (optional)

Wenn der Validator nicht unter einem der Standardpfade liegt, trage den
expliziten Pfad in die Mandanten-Einstellungen ein. Im Admin-Bereich der
Software oder direkt in `data/mandant_settings.json`:

```json
{
  "company_name": "Energieberatung Rolf Krause",
  "kosit_validator_path": "C:/Users/Rolf/tools/kosit/validator-1.5.0-standalone.jar"
}
```

Der Pfad darf auf das JAR selbst oder auf den Ordner zeigen, der JAR und
`scenarios.xml` enthält.

## 5. Test

Starte die Software, öffne eine beliebige Rechnung, klicke **KoSIT prüfen**.

Erwartete Ausgabe:

- **Verfügbar + gültig:** `KoSIT gültig (validator-X.Y.Z, 0 Warnungen)`
- **Verfügbar + Fehler:** `KoSIT: 2 Fehler, 1 Warnungen` — Details in der
  Browser-Konsole
- **Nicht verfügbar:** `KoSIT nicht verfügbar: Java nicht im PATH gefunden`
  oder `KoSIT-Validator-JAR nicht gefunden`

## 6. Updates

Der Validator wird regelmäßig aktualisiert (XRechnung-Releases zum 1. Februar
und 1. August, ggf. Bugfix-Zwischenreleases). Um zu aktualisieren:

1. Neuen Validator-JAR und neue Szenario-ZIP von den oben verlinkten
   Release-Seiten herunterladen.
2. Alten JAR aus `tools/kosit/` löschen.
3. Neue Dateien in denselben Ordner entpacken.
4. E-Rechnungs-Software neu starten.

Die Software erkennt den neuen JAR automatisch.

## Hinweise

- Der erste Aufruf pro Prozess-Session dauert einige Sekunden (Java-Startup).
- Der Validator läuft als lokaler Subprozess — es werden **keine Rechnungsdaten
  an externe Server geschickt**.
- Bei sehr großen Rechnungen (> 50 Positionen) kann der Timeout von 60
  Sekunden knapp werden. Er lässt sich in `kosit_validator.py` im Aufruf
  von `validate_with_kosit(..., timeout_sec=...)` anpassen.
