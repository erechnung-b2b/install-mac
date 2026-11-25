# Rechnungen aus einer CSV-Datei einlesen

Für Firmen, die ihre Rechnungen in einem eigenen Programm schreiben. Das Programm exportiert die Rechnungsdaten als
CSV-Datei; e-rechnung pro macht daraus gültige E-Rechnungen (XRechnung und ZUGFeRD), prüft sie und legt sie im Archiv ab.

**Wo:** Menü „Rechnung erstellen" → „Rechnungen aus einer CSV-Datei einlesen".
**Ablauf:** Datei wählen → „1. Prüfen" (es wird nichts angelegt) → Ergebnis ansehen → „2. Fehlerfreie einlesen".
Eingelesene Rechnungen erscheinen in der Rechnungsliste mit dem Status „Neu" und laufen durch die übliche Freigabe.
Eine Muster-Datei gibt es im selben Fenster zum Herunterladen („Muster-CSV herunterladen").

## Aufbau der Datei

- **Eine Zeile je Rechnungsposition.** Zeilen mit derselben Rechnungsnummer bilden zusammen eine Rechnung.
  Die Angaben zur Rechnung und zum Kunden werden aus der ersten Zeile der Rechnung genommen. Je Zeile gelesen werden
  nur die Angaben zur Position (in der Tabelle mit „je Position" gekennzeichnet).
- **Erste Zeile = Spaltennamen.** Die Reihenfolge der Spalten ist frei; Groß- und Kleinschreibung spielt keine Rolle,
  Leerzeichen im Namen gelten als Unterstrich. Spalten, die das Programm nicht kennt, werden übergangen und als Hinweis
  gemeldet.
- **Trennzeichen:** Semikolon (Komma oder Tabulator werden ebenfalls erkannt).
- **Zeichensatz:** UTF-8 (auch mit BOM) oder Windows-1252, so wie Excel die Datei speichert.
- **Zahlen:** mit Komma als Dezimalzeichen, Tausenderpunkt erlaubt: `1.280,00`. Auch `1280.00` wird gelesen.
  Eine Zahl wie `1.280` ohne Komma gilt als 1,28 — Tausenderpunkte also nur zusammen mit einem Komma verwenden.
- **Datum:** `TT.MM.JJJJ` oder `JJJJ-MM-TT`.
- **Preise sind Nettopreise in Euro.** Die Umsatzsteuer rechnet das Programm aus dem Steuersatz.
- Höchstens 300 Rechnungen und 2 MB je Datei.

## Spalten

Pflicht sind neun Spalten; alle anderen sind freiwillig. Die letzte Spalte der Tabellen nennt weitere Spaltennamen, die
das Programm ebenso erkennt.

### Rechnung

| Spalte | Pflicht | Inhalt | Beispiel | Auch erkannt als |
|---|---|---|---|---|
| `rechnungsnummer` | ja | Ihre Rechnungsnummer; sie wird unverändert übernommen und darf im System noch nicht vorkommen | `2026-0815` | `rechnungsnr`, `rechnung`, `nummer`, `belegnummer`, `invoice_number` |
| `rechnungsdatum` | ja | Ausstellungsdatum | `02.11.2026` | `datum`, `belegdatum`, `invoice_date` |
| `leistungsdatum` | nein | Tag oder Ende der Leistung | `31.10.2026` | `lieferdatum`, `delivery_date` |
| `leistung_von` | nein | Beginn des Leistungszeitraums, je Position. Tragen alle Positionen denselben Zeitraum, gilt er für die ganze Rechnung; sonst behält jede Position ihren | `01.10.2026` | `leistungszeitraum_von`, `zeitraum_von` |
| `leistung_bis` | nein | Ende des Leistungszeitraums, je Position | `31.10.2026` | `leistungszeitraum_bis`, `zeitraum_bis` |
| `zahlungsziel_tage` | nein | Zahlungsziel in Tagen ab Rechnungsdatum; `0` = sofort. Ohne Angabe 14 Tage | `14` | `zahlungsziel`, `tage` |
| `faellig_am` | nein | Fälligkeitsdatum statt `zahlungsziel_tage` | `16.11.2026` | `fällig_am`, `faelligkeit`, `fälligkeit`, `due_date` |
| `zahlungsbedingungen` | nein | Eigener Text statt des Standardsatzes | `Zahlbar innerhalb von 30 Tagen ohne Abzug.` | `zahlungsbedingung`, `payment_terms` |
| `skonto_prozent` | nein | Skonto in Prozent | `2` | `skonto` |
| `skonto_tage` | nein | Skontofrist in Tagen | `10` | – |
| `skonto2_prozent` | nein | Zweite Skontostufe in Prozent (längere Frist, kleinerer Satz) | `1` | – |
| `skonto2_tage` | nein | Frist der zweiten Skontostufe in Tagen | `20` | – |
| `verwendungszweck` | nein | Text, den der Kunde bei der Überweisung angeben soll. Ohne Angabe gilt die Rechnungsnummer | `Vertrag 4711 November` | `zahlungsreferenz`, `remittance` |
| `belegart` | nein | `380` Rechnung (Standard), `381` Gutschrift oder `384` Rechnungskorrektur. Auch die Wörter `rechnung`, `gutschrift`, `korrektur` werden gelesen | `380` | `typ`, `type` |
| `bezug_rechnung` | nein | Bei Gutschrift und Rechnungskorrektur: Nummer der Rechnung, auf die sich der Beleg bezieht. Sie muss im System vorhanden sein. Bei `384` Pflicht; bei `381` ergibt ein leeres Feld eine freie Gutschrift ohne Bezug | `2026-0790` | `bezug`, `preceding_invoice` |
| `steuerfall` | nein | `regel`, `kleinunternehmer`, `reverse_charge`, `steuerfrei`, `nullsatz`, `ig_lieferung`, `ausfuhr` oder `nicht_steuerbar`. Ohne Angabe gilt die Vorgabe Ihrer Stammdaten (Kleinunternehmer), sonst `regel`. Außer bei `regel` muss der Steuersatz 0 sein oder leer bleiben | `regel` | `steuerart` |
| `steuerbefreiung_grund` | nein | Grund mit Vorschrift. Bei `steuerfrei` Pflicht, sonst ein freiwilliger Zusatz zum Hinweistext | `Steuerfrei nach § 4 Nr. 21 UStG` | `befreiungsgrund`, `steuergrund` |
| `notiz` | nein | Bemerkung auf der Rechnung | `Vielen Dank für Ihren Auftrag.` | `bemerkung`, `note` |
| `rundung` | nein | Rundungsbetrag, wenn Ihr Programm den Zahlbetrag rundet; kleiner als 1,00. Er geht in den Zahlbetrag ein | `-0,02` | `rundungsbetrag`, `rounding_amount` |

### Kunde

| Spalte | Pflicht | Inhalt | Beispiel | Auch erkannt als |
|---|---|---|---|---|
| `kunde_name` | ja | Firma oder Name des Kunden | `Beispiel Wohnbau AG` | `kunde`, `firma`, `kundenname`, `buyer_name` |
| `kunde_strasse` | empfohlen | Straße ohne Hausnummer | `Beispielallee` | `kunde_straße`, `strasse`, `straße` |
| `kunde_hausnummer` | nein | Hausnummer | `20` | `hausnummer`, `hausnr` |
| `kunde_plz` | ja | Postleitzahl | `54321` | `plz` |
| `kunde_ort` | ja | Ort | `Beispielstadt` | `ort` |
| `kunde_land` | nein | Land des Kunden als zweistelliger Code oder deutscher Name. Ohne Angabe Deutschland | `AT` | `land`, `kunde_laendercode`, `country` |
| `kunde_email` | ja | E-Mail-Adresse des Kunden für den Rechnungseingang. Die E-Rechnung verlangt eine elektronische Adresse des Empfängers | `eingang@kunde.example` | `email`, `e-mail`, `e_mail` |
| `kunde_referenz` | nein | Kundennummer oder Leitweg-ID (bei öffentlichen Auftraggebern Pflicht). Ohne Angabe wird der Kundenname eingesetzt | `K-0001` | `kundennummer`, `leitweg_id`, `leitweg-id`, `referenz`, `buyer_reference` |
| `kunde_ustid` | nein | Umsatzsteuer-Identifikationsnummer des Kunden mit Länderkennzeichen. Pflicht bei `reverse_charge` und `ig_lieferung` | `DE987654321` | `ustid`, `ust_id`, `ust-id`, `ust-idnr` |
| `ansprechpartner` | nein | Ansprechpartner beim Kunden | `Max Beispiel` | `kontakt` |

### Position (je Zeile)

| Spalte | Pflicht | Inhalt | Beispiel | Auch erkannt als |
|---|---|---|---|---|
| `bezeichnung` | ja | Text der Position | `Beratung vor Ort` | `leistung`, `artikel`, `position_text`, `text`, `name` |
| `beschreibung` | nein | Zusatztext zur Position (steht in der E-Rechnung und auf dem PDF) | `Termin am 12.10.` | `zusatztext`, `description` |
| `artikelnummer` | nein | Ihre Artikelnummer | `B-100` | `artikelnr`, `art_nr`, `artnr` |
| `menge` | ja | Menge, größer als 0 | `12` | `anzahl`, `quantity` |
| `einheit` | nein | `Stk`, `Std`, `Tag`, `Monat`, `psch`, `m`, `m2`, `km`, `kg`, `l`. Ohne Angabe Stück | `Std` | `unit` |
| `einzelpreis` | ja | Nettopreis je Einheit, nicht negativ | `95,00` | `preis`, `einzelpreis_netto`, `netto_einzelpreis`, `price` |
| `steuersatz` | nein | Umsatzsteuersatz in Prozent. Ohne Angabe 19, außerhalb der Regelbesteuerung 0 | `19` | `ust`, `mwst`, `ust_satz`, `tax_rate` |
| `rabatt_prozent` | nein | Rabatt auf die Position in Prozent | `10` | `rabatt` |
| `rabatt_betrag` | nein | Rabatt auf die Position als Betrag in Euro, statt `rabatt_prozent` | `25,00` | – |
| `rabatt_grund` | nein | Grund des Rabatts. Ohne Angabe steht „Rabatt" | `Treuerabatt` | `rabattgrund` |

### Bezüge des Kunden

| Spalte | Pflicht | Inhalt | Beispiel | Auch erkannt als |
|---|---|---|---|---|
| `bestellnummer` | nein | Bestellnummer des Kunden | `4500012345` | `bestell_nr`, `bestellnr`, `order_reference` |
| `vertragsnummer` | nein | Vertragsnummer | `V-2026-017` | `vertrag`, `vertragsnr`, `contract_reference` |
| `projektnummer` | nein | Projektnummer | `P-0815` | `projekt`, `projektnr`, `project_reference` |
| `auftragsnummer` | nein | Ihre eigene Auftragsnummer | `AB-2026-0031` | `auftrag`, `auftragsnr`, `sales_order_reference` |
| `versandanzeige` | nein | Nummer des Lieferscheins oder der Versandanzeige | `LS-2026-0102` | `lieferschein`, `lieferscheinnummer`, `despatch_advice_reference` |
| `wareneingang` | nein | Wareneingangsmeldung des Kunden | `WE-4711` | `wareneingangsmeldung`, `receiving_advice_reference` |
| `ausschreibung` | nein | Ausschreibung oder Los | `VG-2026-17 Los 2` | `los`, `vergabenummer`, `tender_reference` |
| `objektkennung` | nein | Kennung des abgerechneten Objekts, z. B. Zählernummer | `Z-00471` | `objekt`, `zaehlernummer`, `zählernummer`, `object_identifier` |
| `kostenstelle` | nein | Kostenstelle oder Buchungsreferenz des Kunden | `4711` | `buchungsreferenz`, `buyer_accounting_reference` |
| `anhang_verweis` | nein | Internetadresse einer Unterlage zur Rechnung. Dateien lassen sich über die CSV-Datei nicht anhängen | `https://portal.kunde.example/aufmass/815` | `anhang_url`, `anlage_url` |

### Lieferadresse, Lastschrift, Zahlungsempfänger

| Spalte | Pflicht | Inhalt | Beispiel | Auch erkannt als |
|---|---|---|---|---|
| `lieferadresse_name` | nein | Name des Empfängers, wenn die Lieferung an eine andere Anschrift ging | `Lager Wien` | `lieferung_name`, `empfaenger_name` |
| `lieferadresse_strasse` | nein | Straße und Hausnummer der Lieferadresse | `Hafenstraße 4` | `lieferadresse_straße`, `lieferung_strasse` |
| `lieferadresse_plz` | nein | Postleitzahl der Lieferadresse | `1100` | `lieferung_plz` |
| `lieferadresse_ort` | nein | Ort der Lieferadresse | `Wien` | `lieferung_ort` |
| `lieferadresse_land` | nein | Land der Lieferadresse. Ohne Angabe wie beim Kunden | `AT` | `lieferung_land`, `lieferland` |
| `zahlungsart` | nein | `58` oder `überweisung` (Standard), `59` oder `lastschrift` für SEPA-Lastschrift | `59` | `zahlart`, `payment_code` |
| `mandatsreferenz` | nein | Bei Lastschrift Pflicht: Mandatsreferenz des Kunden | `M-2026-0042` | `mandat`, `mandate_reference` |
| `kunde_iban` | nein | Bei Lastschrift Pflicht: IBAN des Kunden, von der eingezogen wird | `DE02120300000000202051` | `iban_kunde`, `debited_account` |
| `zahlungsempfaenger_name` | nein | Abweichender Zahlungsempfänger, z. B. bei abgetretener Forderung. Ohne Angabe gilt die Vorgabe der Stammdaten | `Muster Factoring GmbH` | `zahlungsempfänger_name`, `zahlungsempfaenger`, `zahlungsempfänger` |
| `zahlungsempfaenger_kennung` | nein | Kennung des Zahlungsempfängers | `F-1001` | `zahlungsempfänger_kennung` |
| `zahlungsempfaenger_register` | nein | Registernummer des Zahlungsempfängers | `HRB 1234` | `zahlungsempfänger_register` |

## Was Sie wissen sollten

- **Lastschrift** setzt voraus, dass in den Einstellungen bei den Stammdaten Ihre Gläubiger-ID eingetragen ist.
- **Steuerfälle ohne Umsatzsteuer** verlangen dieselben Angaben wie in der Maske, zum Beispiel beide USt-IDs bei
  `reverse_charge` und `ig_lieferung`. Fehlt etwas, nennt die Prüfung den Grund. Welcher Steuerfall gilt, klären Sie im
  Zweifel mit Ihrer Steuerberatung.
- **Rechnungskorrektur (384):** Die Ursprungsrechnung muss beim Prüfen schon im Programm stehen. Stehen Rechnung und
  Korrektur in derselben Datei, lesen Sie zuerst die Rechnung ein und prüfen dann erneut. Die Korrektur verknüpft die
  Ursprungsrechnung nur; sie storniert sie nicht.
- **Nicht über CSV:** Nachlass oder Zuschlag auf die ganze Rechnung, Zuschlag an der Position, Dateien als Anhang und
  Gutschriften im Gutschriftverfahren. Diese Fälle erfassen Sie in der Maske.
- **Nur Euro.** Eine Spalte für die Währung gibt es nicht.

## Beispiel

```
rechnungsnummer;rechnungsdatum;leistungsdatum;zahlungsziel_tage;kunde_name;kunde_strasse;kunde_hausnummer;kunde_plz;kunde_ort;kunde_land;kunde_email;kunde_referenz;bestellnummer;bezeichnung;beschreibung;menge;einheit;einzelpreis;steuersatz;steuerfall;leistung_von;leistung_bis;rabatt_prozent;rabatt_grund;verwendungszweck;notiz
2026-0815;02.11.2026;31.10.2026;14;Beispiel Wohnbau AG;Beispielallee;20;54321;Beispielstadt;DE;eingang@beispiel-wohnbau.example;K-0001;4500012345;Beratung vor Ort;Termine laut Stundennachweis;12;Std;95,00;19;regel;01.10.2026;31.10.2026;;;;Vielen Dank für Ihren Auftrag.
2026-0815;02.11.2026;31.10.2026;14;Beispiel Wohnbau AG;Beispielallee;20;54321;Beispielstadt;DE;eingang@beispiel-wohnbau.example;K-0001;4500012345;Dokumentation und Bericht;;1;Stk;650,00;19;regel;01.10.2026;31.10.2026;10;Treuerabatt;;
2026-0816;03.11.2026;;30;Hausverwaltung Sonnenhof GmbH;Sonnenweg;12;23456;Sonnenstadt;DE;buchhaltung@sonnenhof.example;K-0003;;Wartung Heizungsanlage, pauschal;;1;psch;1.280,00;19;regel;01.11.2026;30.11.2026;;;Vertrag 4711 November;
```

Die ersten beiden Zeilen ergeben eine Rechnung mit zwei Positionen, gemeinsamem Leistungszeitraum und 10 % Rabatt auf
die zweite Position. Die dritte Zeile ist eine zweite Rechnung mit eigenem Verwendungszweck. Die Datei rechnet
mit 19 % Umsatzsteuer; der Steuerfall `regel` steht deshalb ausdrücklich darin. Sind Sie Kleinunternehmer, tragen Sie
stattdessen `kleinunternehmer` und den Steuersatz 0 ein.

Ein zweites Beispiel: eine Leistung an ein Unternehmen in Österreich mit Steuerschuld des Kunden und abweichender
Lieferadresse. Es setzt voraus, dass in Ihren Stammdaten Ihre USt-ID eingetragen ist.

```
rechnungsnummer;rechnungsdatum;leistungsdatum;kunde_name;kunde_strasse;kunde_hausnummer;kunde_plz;kunde_ort;kunde_land;kunde_email;kunde_ustid;bezeichnung;menge;einheit;einzelpreis;steuerfall;lieferadresse_name;lieferadresse_strasse;lieferadresse_plz;lieferadresse_ort
2026-0817;04.11.2026;03.11.2026;Muster Handel GmbH;Ringstraße;7;1010;Wien;AT;rechnung@muster-handel.example;ATU12345678;Schulung vor Ort;2;Tag;900,00;reverse_charge;Muster Handel GmbH, Lager;Hafenstraße 4;4020;Linz
```

## Was beim Prüfen geschieht

Jede Rechnung wird einzeln geprüft: Pflichtangaben, Zahlen und Daten, doppelte Rechnungsnummern und die Regeln der
E-Rechnung (EN 16931 und XRechnung). Das Ergebnis nennt je Rechnung „in Ordnung" oder den Grund der Ablehnung mit
Zeilennummer. Angelegt werden nur fehlerfreie Rechnungen; die übrigen korrigieren Sie in Ihrer Datei und lesen sie
erneut ein — schon angelegte Nummern werden dabei als „existiert bereits" abgewiesen und nicht doppelt angelegt.

Die eigenen Firmenangaben (Name, Anschrift, Steuernummer, Bankverbindung) kommen aus den Stammdaten des Programms,
nicht aus der Datei.
