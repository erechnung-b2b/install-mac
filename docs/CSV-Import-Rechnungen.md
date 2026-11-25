# Rechnungen aus einer CSV-Datei einlesen

Für Firmen, die ihre Rechnungen in einem eigenen Programm schreiben. Das Programm exportiert die Rechnungsdaten als
CSV-Datei; e-rechnung pro macht daraus gültige E-Rechnungen (XRechnung und ZUGFeRD), prüft sie und legt sie im Archiv ab.

**Wo:** Menü „Rechnung erstellen" → „Rechnungen aus einer CSV-Datei einlesen".
**Ablauf:** Datei wählen → „1. Prüfen" (es wird nichts angelegt) → Ergebnis ansehen → „2. Fehlerfreie einlesen".
Eingelesene Rechnungen erscheinen in der Rechnungsliste mit dem Status „Neu" und laufen durch die übliche Freigabe.
Eine Muster-Datei gibt es im selben Fenster zum Herunterladen.

## Aufbau der Datei

- **Eine Zeile je Rechnungsposition.** Zeilen mit derselben Rechnungsnummer bilden zusammen eine Rechnung.
  Die Angaben zur Rechnung und zum Kunden werden aus der ersten Zeile der Rechnung genommen.
- **Erste Zeile = Spaltennamen.** Die Reihenfolge der Spalten ist frei; Groß- und Kleinschreibung spielt keine Rolle.
  Spalten, die das Programm nicht kennt, werden übergangen und als Hinweis gemeldet.
- **Trennzeichen:** Semikolon (Komma oder Tabulator werden ebenfalls erkannt).
- **Zeichensatz:** UTF-8 (auch mit BOM) oder Windows-1252, so wie Excel die Datei speichert.
- **Zahlen:** mit Komma als Dezimalzeichen, Tausenderpunkt erlaubt: `1.280,00`. Auch `1280.00` wird gelesen.
  Eine Zahl wie `1.280` ohne Komma gilt als 1,28 — Tausenderpunkte also nur zusammen mit einem Komma verwenden.
- **Datum:** `TT.MM.JJJJ` oder `JJJJ-MM-TT`.
- **Preise sind Nettopreise.** Die Umsatzsteuer rechnet das Programm aus dem Steuersatz.
- Höchstens 300 Rechnungen und 2 MB je Datei.

## Spalten

| Spalte | Pflicht | Inhalt | Beispiel |
|---|---|---|---|
| `rechnungsnummer` | ja | Ihre Rechnungsnummer; sie wird unverändert übernommen und darf im System noch nicht vorkommen | `2026-0815` |
| `rechnungsdatum` | ja | Ausstellungsdatum | `02.11.2026` |
| `leistungsdatum` | nein | Tag oder Ende der Leistung | `31.10.2026` |
| `zahlungsziel_tage` | nein | Zahlungsziel in Tagen ab Rechnungsdatum; `0` = sofort. Ohne Angabe 14 Tage | `14` |
| `faellig_am` | nein | Fälligkeitsdatum statt `zahlungsziel_tage` | `16.11.2026` |
| `zahlungsbedingungen` | nein | Eigener Text statt des Standardsatzes | `Zahlbar innerhalb von 30 Tagen ohne Abzug.` |
| `belegart` | nein | `380` Rechnung (Standard) oder `381` Gutschrift | `380` |
| `bezug_rechnung` | nein | Nur bei Gutschrift: Nummer der Rechnung, auf die sie sich bezieht. Diese muss im System vorhanden sein | `2026-0790` |
| `kunde_name` | ja | Firma oder Name des Kunden | `Beispiel Wohnbau AG` |
| `kunde_strasse` | empfohlen | Straße ohne Hausnummer | `Beispielallee` |
| `kunde_hausnummer` | nein | Hausnummer | `20` |
| `kunde_plz` | ja | Postleitzahl | `54321` |
| `kunde_ort` | ja | Ort | `Beispielstadt` |
| `kunde_email` | ja | E-Mail-Adresse des Kunden für den Rechnungseingang. Die E-Rechnung verlangt eine elektronische Adresse des Empfängers | `eingang@kunde.example` |
| `kunde_referenz` | nein | Kundennummer oder Leitweg-ID (bei öffentlichen Auftraggebern Pflicht). Ohne Angabe wird der Kundenname eingesetzt | `K-0001` |
| `kunde_ustid` | nein | Umsatzsteuer-Identifikationsnummer des Kunden | `DE987654321` |
| `ansprechpartner` | nein | Ansprechpartner beim Kunden | `Max Beispiel` |
| `bezeichnung` | ja | Text der Position | `Beratung vor Ort` |
| `beschreibung` | nein | Zusatztext zur Position (steht in der E-Rechnung) | `Termin am 12.10.` |
| `artikelnummer` | nein | Ihre Artikelnummer | `B-100` |
| `menge` | ja | Menge, größer als 0 | `12` |
| `einheit` | nein | `Stk`, `Std`, `Tag`, `Monat`, `psch`, `m`, `m2`, `km`, `kg`, `l`. Ohne Angabe Stück | `Std` |
| `einzelpreis` | ja | Nettopreis je Einheit, nicht negativ | `95,00` |
| `steuersatz` | nein | Umsatzsteuersatz in Prozent. Ohne Angabe 19 | `19` |
| `steuerfall` | nein | `regel` (Standard), `kleinunternehmer`, `reverse_charge`, `steuerfrei` oder `nullsatz`. Außer bei `regel` ist der Steuersatz 0 | `regel` |
| `bestellnummer` | nein | Bestellnummer des Kunden | `4500012345` |
| `vertragsnummer` | nein | Vertragsnummer | `V-2026-017` |
| `projektnummer` | nein | Projektnummer | `P-0815` |
| `skonto_prozent` | nein | Skonto in Prozent | `2` |
| `skonto_tage` | nein | Skontofrist in Tagen | `10` |
| `notiz` | nein | Bemerkung auf der Rechnung, z. B. Leistungszeitraum | `Leistungszeitraum Oktober 2026` |

Für viele Spalten werden auch gebräuchliche andere Namen erkannt, zum Beispiel `datum` für `rechnungsdatum`, `kunde` oder
`firma` für `kunde_name`, `plz`, `ort`, `preis`, `anzahl`, `ust` oder `mwst` für `steuersatz`.

## Beispiel

```
rechnungsnummer;rechnungsdatum;leistungsdatum;zahlungsziel_tage;kunde_name;kunde_strasse;kunde_hausnummer;kunde_plz;kunde_ort;kunde_email;kunde_referenz;bezeichnung;menge;einheit;einzelpreis;steuersatz;notiz
2026-0815;02.11.2026;31.10.2026;14;Beispiel Wohnbau AG;Beispielallee;20;54321;Beispielstadt;eingang@beispiel-wohnbau.example;K-0001;Beratung vor Ort;12;Std;95,00;19;Leistungszeitraum Oktober 2026
2026-0815;02.11.2026;31.10.2026;14;Beispiel Wohnbau AG;Beispielallee;20;54321;Beispielstadt;eingang@beispiel-wohnbau.example;K-0001;Dokumentation und Bericht;1;Stk;650,00;19;
2026-0816;03.11.2026;;30;Hausverwaltung Sonnenhof GmbH;Sonnenweg;12;23456;Sonnenstadt;buchhaltung@sonnenhof.example;K-0003;Wartung Heizungsanlage, pauschal;1;psch;1.280,00;19;
```

Die ersten beiden Zeilen ergeben eine Rechnung mit zwei Positionen, die dritte eine zweite Rechnung.

## Was beim Prüfen geschieht

Jede Rechnung wird einzeln geprüft: Pflichtangaben, Zahlen und Daten, doppelte Rechnungsnummern und die Regeln der
E-Rechnung (EN 16931 und XRechnung). Das Ergebnis nennt je Rechnung „in Ordnung" oder den Grund der Ablehnung mit
Zeilennummer. Angelegt werden nur fehlerfreie Rechnungen; die übrigen korrigieren Sie in Ihrer Datei und lesen sie
erneut ein — schon angelegte Nummern werden dabei als „existiert bereits" abgewiesen und nicht doppelt angelegt.

Die eigenen Firmenangaben (Name, Anschrift, Steuernummer, Bankverbindung) kommen aus den Stammdaten des Programms,
nicht aus der Datei.
