# e-rechnung pro – Handbuch

Version 5.0 · Online-Version · Stand November 2026

Dieses Handbuch hat drei Teile: eine Übersicht aller Funktionen, die Anleitung für alle, die täglich mit dem Programm
arbeiten, und die Anleitung für die Person in Ihrer Firma, die den Zugang verwaltet.

---

## Teil A – Alle Funktionen im Überblick

| Bereich | Was das Programm kann |
|---|---|
| **E-Rechnung schreiben** | Rechnung als XRechnung (XML) und als ZUGFeRD-PDF nach EN 16931. Gutschrift und Stornorechnung mit Bezug auf die Ursprungsrechnung, Abschlagsrechnung und Schlussrechnung mit abgezogenen Anzahlungen. Skonto mit Frist. GiroCode auf der Rechnung. |
| **Rechnungen aus Ihrem eigenen Programm** | Einlesen einer CSV-Datei mit Rechnungsdaten: erst prüfen, dann die fehlerfreien Rechnungen anlegen. |
| **Prüfung** | Eingebaute Prüfung der Pflichtangaben und Rechenregeln. Zusätzlich wird jede Ausgangsrechnung von selbst mit dem amtlichen KoSIT-Validator geprüft; das Ergebnis steht an der Rechnung. Eine Rechnung, die dort durchfällt, wird nicht versendet. |
| **Freigabe** | Zweistufige Freigabe (sachlich, kaufmännisch), Zurückweisen mit Begründung, Stellvertretung bei Abwesenheit. |
| **Versand** | Versand per E-Mail mit PDF und XML im Anhang, Protokoll aller versendeten Mails. |
| **Eingangsrechnungen** | Empfang per E-Mail-Abruf oder Hochladen, Anzeige als lesbarer Beleg, Prüfung, Freigabe, Kontierung. PDF-Rechnungen ohne Daten: Die KI-Erkennung liest Lieferant, Nummer, Beträge und Steuersätze aus und stellt sie zur Übernahme bereit – gespeichert wird erst nach Ihrer Bestätigung. |
| **Zahlungen** | Zahlungseingänge kommen per Direktabruf vom Bankkonto oder als Kontoauszug (CAMT.053, CSV). Automatischer Rechnungsabgleich: Eindeutige Zahlungen werden selbst als bezahlt gebucht, unsichere als Vorschlag vorgelegt, Sammelzahlungen erkannt. |
| **Mahnwesen** | Überfällige Rechnungen, drei Mahnstufen mit Fristen und Gebühren, Mahnschreiben als PDF. |
| **Vorgänge** | Auftragsabwicklung in neun Stufen: Angebotsanfrage, Bestellung, Eingangsrechnung, Angebot, Auftragseingang, Auftragsbestätigung, Lieferschein, Rechnung, Mahnung. Positionen wandern von Stufe zu Stufe mit; Anhänge und Checkliste je Vorgang. |
| **Stammdaten** | Kunden mit mehreren Ansprechpartnern, Lieferanten mit Freigabe, Produkte und Leistungen mit Einkaufs- und Verkaufspreis. Einlesen und Ausgeben als CSV. |
| **Belege gestalten** | Logo, Farben, Tabellenkopf, Fußzeile – einmal eingestellt, gilt für alle Belege. Textvorlagen je Belegart. |
| **Archiv** | Ablage jeder Rechnung mit Original-XML, PDF und Prüfsumme; Verlauf jeder Änderung; Aufbewahrungsfristen. |
| **Auswertung und Weitergabe** | DATEV-Export einzeln, monats- oder jahresweise. Steuerberater-Export: alle Ein- und Ausgangsrechnungen eines Zeitraums als PDF-Paket mit Übersicht. |
| **Team** | Eigenes Benutzerkonto für jeden Mitarbeiter, gemeinsamer Bestand, Warnung, wenn zwei denselben Eintrag bearbeiten. |

---

## Teil B – Für alle, die mit dem Programm arbeiten

### 1. Anmelden

Öffnen Sie `https://app.e-rechnung-b2b.de`. Geben Sie die **Kennung Ihrer Firma**, Ihren **Benutzernamen oder Ihre
E-Mail-Adresse** und Ihr **Passwort** ein. Abmelden können Sie sich über „Abmelden" unten im Menü.

Nach fünf falschen Versuchen ist die Anmeldung für 15 Minuten gesperrt.

### 2. Das Menü

| Menüpunkt | Wofür |
|---|---|
| Rechnungen | Liste aller Ein- und Ausgangsrechnungen mit Status, Suche und Filter |
| Gutschriften | Gutschriften und Stornos mit ihrer Bezugsrechnung |
| Freigaben | Rechnungen, die auf Prüfung oder Freigabe warten |
| Rechnung erstellen | Neue Rechnung schreiben oder Rechnungen aus einer CSV-Datei einlesen |
| Archiv | Abgelegte Rechnungen mit Prüfsumme |
| Lieferanten, Kunden, Produkte | Stammdaten |
| Angebots-Vorlagen | Texte und Bausteine für Angebote |
| Vorgänge | Aufträge vom Angebot bis zur Rechnung |
| Mahnungen | Überfällige Rechnungen und Mahnvorschläge |
| Bank | Zahlungseingänge und ihre Zuordnung |
| Dokumente | Unterlagen zu Kunden, Lieferanten und Vorgängen |
| Steuerberater | Belegpaket für einen Zeitraum |
| Mails | Protokoll der versendeten Mails |
| Einstellungen | Benutzer, Gestaltung, Stammdaten der Firma, E-Mail |

### 3. Eine Rechnung schreiben

1. „Rechnung erstellen" öffnen. Ihre Firmenangaben sind vorbelegt.
2. Kunden wählen oder eintragen. Pflicht sind Name, Postleitzahl, Ort und die **E-Mail-Adresse des Kunden** – ohne
   elektronische Adresse ist eine E-Rechnung nicht gültig. Bei öffentlichen Auftraggebern gehört die Leitweg-ID in das
   Feld „Referenz".
3. Positionen eintragen: Bezeichnung, Menge, Einzelpreis netto, Steuersatz. Leistungen aus dem Produktkatalog lassen sich
   übernehmen.
4. Rechnungsdatum, Leistungsdatum und Zahlungsbedingung prüfen; bei Bedarf Skonto eintragen.
5. „Validieren" prüft nur. „XRechnung erzeugen" legt die Rechnung an. Die Rechnungsnummer vergibt das Programm
   fortlaufend, wenn Sie keine eintragen.

Die neue Rechnung steht mit dem Status „Neu" in der Liste.

### 4. Rechnungen aus dem eigenen Programm einlesen

„Rechnung erstellen" → „Rechnungen aus einer CSV-Datei einlesen". Datei wählen, **„1. Prüfen"** – dabei wird nichts
angelegt. Das Ergebnis zeigt je Rechnung „in Ordnung" oder den Fehler mit Zeilennummer. **„2. Fehlerfreie einlesen"**
legt die fehlerfreien Rechnungen an. Eine Rechnungsnummer, die es schon gibt, wird abgewiesen; doppelt wird nichts
angelegt. Das Dateiformat steht in der Unterlage „CSV-Import-Rechnungen", eine Muster-Datei gibt es im selben Fenster.

### 5. Prüfen, freigeben, versenden

- **Rechnung öffnen:** Klick auf die Zeile in der Liste. Rechts steht die Karte „Validierung" mit dem Ergebnis der
  eingebauten Prüfung und darunter „Amtliche Prüfung (KoSIT)". Die amtliche Prüfung läuft von selbst und braucht einige
  Sekunden; solange steht dort „läuft noch".
- **Belegansicht:** zeigt die Rechnung so, wie der Empfänger sie liest.
- **Freigeben:** einmal für die sachliche, einmal für die kaufmännische Freigabe. Danach ist die Rechnung
  „Freigegeben". **Zurückweisen** verlangt eine Begründung.
- **Per E-Mail senden:** Empfänger, Betreff und Text prüfen, senden. Mitgeschickt werden das PDF und die XML-Datei.
  Fällt die Rechnung bei der amtlichen Prüfung durch, wird sie nicht versendet; der Grund steht in der Karte
  „Validierung".
- **ZUGFeRD-PDF:** lädt das PDF mit eingebetteten Rechnungsdaten herunter.

### 6. Gutschrift und Storno

In der geöffneten Rechnung:

- **Gutschrift:** mindert die Rechnung um einen Teilbetrag. Grund und Positionen eintragen. Die Rechnung bleibt gültig,
  der offene Betrag sinkt.
- **Stornieren:** hebt die Rechnung in voller Höhe auf. Die Rechnung bekommt den Status „Storniert".

Beide Belege sind eigene E-Rechnungen und nennen Nummer und Datum der Ursprungsrechnung. Stornieren und Gutschreiben geht
nur bei freigegebenen Rechnungen.

### 7. Zahlungen

- **Bank:** Kontoauszug hochladen oder die Umsätze direkt abrufen. Das Programm ordnet die Zahlungseingänge den offenen
  Rechnungen zu. Eindeutige Zahlungen sind danach „Bezahlt"; bei unsicheren schlägt es eine Rechnung vor, die Sie
  bestätigen oder ändern.
- **Von Hand:** in der Rechnung „Als bezahlt markieren".
- **Skonto:** Zieht der Kunde Skonto ab, zeigt die Rechnung, ob die Zahlung in der Frist lag. Einen Abzug nach der Frist
  können Sie anerkennen oder offen lassen.

### 8. Mahnungen

„Mahnungen" zeigt überfällige Rechnungen mit dem Vorschlag der nächsten Mahnstufe. Das Mahnschreiben entsteht per Klick
als PDF.

### 9. Vorgänge

Ein Vorgang führt einen Auftrag durch bis zu neun Stufen. Sie legen ihn mit Betreff, Kunde und – wenn eingekauft wird –
Lieferant an. In jeder Stufe tragen Sie Positionen ein oder übernehmen sie aus der vorigen Stufe, erzeugen das Dokument
als PDF, geben frei und versenden. Stufen, die Sie nicht brauchen, überspringen Sie. Die Rechnung der Stufe „Rechnung"
ist dieselbe E-Rechnung wie unter „Rechnung erstellen".

**Achtung beim Versenden aus dem Vorgang:** Der farbige Knopf versendet an den Kunden; „Nur freigeben" versendet nicht.
Vor dem Senden zeigt das Programm Vorgang, Kunde, Beleg und Empfängeradresse zur Kontrolle.

### 10. Eingangsrechnungen

Eingangsrechnungen kommen per E-Mail-Abruf oder durch Hochladen in „Rechnungen". E-Rechnungen (XRechnung, ZUGFeRD) werden
gelesen und geprüft. Bei einer reinen PDF-Rechnung liest die KI-Erkennung die Angaben aus und zeigt sie in einem Fenster –
Sie prüfen, ändern bei Bedarf und übernehmen. Danach folgen Freigabe und Kontierung wie bei Ausgangsrechnungen.

### 11. Wenn zwei gleichzeitig arbeiten

Alle Mitarbeiter sehen denselben Bestand. Öffnet jemand einen Vorgang, den ein Kollege in den letzten Minuten ebenfalls
offen hatte, erscheint ein Hinweis mit dessen Namen. Speichern Sie einen Eintrag, den ein anderer nach Ihrem Laden
geändert hat, fragt das Programm nach: „Trotzdem speichern" überschreibt die Änderung des Kollegen; „Abbrechen" speichert
nicht – laden Sie dann neu und tragen Sie Ihre Änderung noch einmal ein.

### 12. Passwort ändern

Einstellungen → Karte „Benutzer" → in Ihrer Zeile „Passwort setzen". Das Passwort braucht mindestens zwölf Zeichen.

---

## Teil C – Für die Person, die den Zugang verwaltet

Das erste Benutzerkonto Ihrer Firma ist Verwalter. Verwalter legen Benutzer an, stellen die Belege ein und pflegen die
Angaben der Firma.

### 1. Erste Schritte

1. **Stammdaten der Firma** (Einstellungen → „Stammdaten"): Name, Anschrift, Umsatzsteuer-Identifikationsnummer oder
   Steuernummer, Ansprechpartner, Bankverbindung. Diese Angaben stehen auf jeder Rechnung.
2. **Logo** hochladen (Einstellungen → „Firmenlogo").
3. **Gestaltung der Belege** einstellen (siehe 3).
4. **E-Mail-Versand** eintragen (siehe 4), damit Rechnungen von Ihrer Adresse hinausgehen.
5. **Benutzer** anlegen (siehe 2).
6. Kunden, Lieferanten und Produkte anlegen oder als CSV einlesen.

### 2. Benutzer verwalten

Einstellungen → Karte „Benutzer".

- **Anlegen:** Benutzername, E-Mail-Adresse und ein Passwort mit mindestens zwölf Zeichen. Das Häkchen „darf Benutzer
  verwalten" macht das Konto zum Verwalter.
- **Ändern:** Passwort setzen, E-Mail-Adresse ändern, Verwaltungsrecht geben oder entziehen.
- **Löschen:** Der Benutzer kann sich danach nicht mehr anmelden; eine laufende Sitzung endet sofort. Rechnungen und
  Verlauf bleiben erhalten. Das eigene Konto und der letzte Verwalter lassen sich nicht löschen.

**Abrechnung:** Jedes Benutzerkonto kostet 9,50 € zzgl. MwSt. im Monat. Gezählt werden die Konten in dem Moment, in dem
die Rechnung für den nächsten Nutzungsmonat entsteht. Legen Sie mitten im Monat einen Benutzer an, kostet er ab dem
nächsten Nutzungsmonat; Sie erhalten dann eine neue Dauerrechnung. Nicht mehr gebrauchte Konten löschen Sie, bevor die
nächste Rechnung entsteht.

### 3. Belege gestalten

Einstellungen → „Gestaltung der Belege". Links stellen Sie ein, rechts sehen Sie sofort einen Musterbeleg mit Ihren
Firmenangaben und Ihrem Logo:

- Hauptfarbe (Titel, Tabellenkopf) und Akzentfarbe (Linie unter dem Briefkopf)
- Logo links oder rechts, Größe des Logos
- Firmenangaben neben dem Logo zeigen oder nicht
- Tabellenkopf farbig gefüllt oder nur mit Linie, jede zweite Zeile hinterlegt
- Größe des Titels, zweite Fußzeile für Geschäftsführung und Registergericht

Die Vorschau lässt sich auf Rechnung, Angebot, Auftragsbestätigung, Lieferschein, Abschlagsrechnung, Gutschrift, Storno
und Zahlungserinnerung umstellen. Erst **„Gestaltung speichern"** wirkt – und zwar auf alle neuen Belege. Bereits
erzeugte Belege bleiben, wie sie sind. „Standard wiederherstellen" setzt alles zurück.

Die **Textvorlagen** (Einleitung und Schlusstext je Belegart) pflegen Sie ebenfalls in den Einstellungen.

### 4. E-Mail einrichten

- **Versand (SMTP):** Server, Benutzer, Passwort und Absenderadresse Ihres Mailkontos. Mit „SMTP-Verbindung testen"
  prüfen Sie die Angaben.
- **Empfang (IMAP):** das Postfach, an das Ihre Lieferanten Rechnungen schicken. „Automatischer E-Mail-Abruf" holt neue
  Rechnungen regelmäßig ab; „Postfach jetzt prüfen" sofort.

Die Protokolle für Empfang und Versand stehen darunter.

### 5. Bank anbinden

„Bank" → „Direktabruf": Bank und Zugangsdaten eintragen; die Freigabe erfolgt mit dem Verfahren Ihrer Bank, zum Beispiel
in der Banking-App. Das Programm liest nur Umsätze und Kontostand, Überweisungen sind nicht möglich. Ohne Direktabruf
laden Sie den Kontoauszug als Datei hoch.

### 6. Nummernkreise

Rechnungen laufen als `RE-Jahr-Nummer`, Gutschriften als `GS-Jahr-Nummer`; Stornos tragen `ST-` vor der Nummer der
stornierten Rechnung. Eigene Nummern aus einem anderen Programm werden beim Einlesen unverändert übernommen.

### 7. Sicherheit und Daten

- Der Zugang ist verschlüsselt. Ihre Firma arbeitet in einem eigenen, gesicherten Bereich.
- Die Daten werden täglich gesichert.
- **Export:** Jede Rechnung als PDF und XML, alle Rechnungen eines Zeitraums über „Steuerberater", die Buchungsdaten
  über den DATEV-Export, Stammdaten als CSV.
- **Aufbewahrung:** Die Fristen stellen Sie in den Einstellungen ein. Für eine ordnungsgemäße Aufbewahrung nach den GoBD
  gehört dazu eine Beschreibung Ihres Ablaufs im Betrieb; die liefert das Programm nicht mit.

### 8. Wenn etwas nicht geht

| Erscheinung | Ursache und Abhilfe |
|---|---|
| „Rechnungsnummer existiert bereits" | Die Nummer ist vergeben. Andere Nummer eintragen oder das Feld leer lassen. |
| Rechnung lässt sich nicht senden | Die amtliche Prüfung meldet Fehler. In der Karte „Validierung" nachlesen, Rechnung stornieren und richtig neu schreiben. |
| „Buyer Electronic Address fehlt" | Die E-Mail-Adresse des Kunden fehlt. |
| Hinweis „wurde geändert, nachdem Sie ihn geladen haben" | Ein Kollege hat denselben Eintrag gespeichert. Neu laden und die eigene Änderung erneut eintragen. |
| Nur Lesen möglich | Der Zugang ist pausiert, weil die Zahlung fehlt. Der Stand und die Zahlungsdaten stehen unter `/zugang`. |
| Anmeldung gesperrt | Nach fünf Fehlversuchen 15 Minuten warten. |

Bei Fragen wenden Sie sich an die Adresse, von der Sie Ihre Zugangsdaten erhalten haben.
