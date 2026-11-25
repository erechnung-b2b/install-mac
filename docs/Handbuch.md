# e-rechnung pro – Handbuch

Version 5.0 · Online-Version · Stand 25. November 2026

Dieses Handbuch hat drei Teile: eine Übersicht aller Funktionen, die Anleitung für alle, die täglich mit dem Programm
arbeiten, und die Anleitung für die Person in Ihrer Firma, die den Zugang verwaltet.

---

## Teil A – Alle Funktionen im Überblick

| Bereich | Was das Programm kann |
|---|---|
| **E-Rechnung schreiben** | Rechnung als XRechnung (XML) und als ZUGFeRD-PDF nach EN 16931. Gutschrift und Stornorechnung mit Bezug auf die Ursprungsrechnung, Abschlagsrechnung und Schlussrechnung mit abgezogenen Anzahlungen. Skonto mit Frist, auch in zwei Stufen. GiroCode auf der Rechnung. Rechnungen werden in Euro ausgestellt. |
| **Steuerfälle** | Regelbesteuerung, Kleinunternehmer, Steuerschuld des Kunden (§ 13b UStG), steuerfreier Umsatz, Nullsteuersatz, innergemeinschaftliche Lieferung, Ausfuhr, nicht steuerbare Leistung – mit Hinweistext und Prüfung der Pflichtangaben. |
| **Kunden im Ausland, Lieferadresse** | Land des Kunden, Hinweis zur USt-ID, abweichende Lieferadresse. |
| **Rabatt, Einheit, Leistungszeitraum** | Je Position Einheit, Beschreibung, Zeitraum, Rabatt oder Zuschlag; Nachlass oder Zuschlag auf die ganze Rechnung. |
| **Bezüge des Kunden** | Leitweg-ID mit Prüfung, Bestell-, Vertrags- und Projektnummer, weitere Bezüge wie Lieferschein oder Kostenstelle; eigener Verwendungszweck. |
| **SEPA-Lastschrift, Zahlungsempfänger** | Rechnung mit Einzugshinweis, Mandatsreferenz und Gläubiger-ID; abweichender Zahlungsempfänger. |
| **Anhänge zur E-Rechnung** | Aufmaß, Stundennachweis oder Lieferschein werden in die E-Rechnung eingebettet. |
| **Rechnungskorrektur** | Berichtigte Rechnung (Belegart 384) mit Bezug auf die Ursprungsrechnung. |
| **Gutschriftverfahren** | Selbst über die Leistung eines Lieferanten abrechnen (Belegart 389): ausstellen, senden, stornieren. Gutschriften Ihrer Kunden über Ihre Leistung führt das Programm als Forderung. |
| **Rechnungen aus Ihrem eigenen Programm** | Einlesen einer CSV-Datei mit Rechnungsdaten: erst prüfen, dann die fehlerfreien Rechnungen anlegen. |
| **Prüfung** | Eingebaute Prüfung der Pflichtangaben und Rechenregeln; bei Fehlern wird keine Rechnung angelegt. Zusätzlich wird jede Ausgangsrechnung von selbst mit dem amtlichen KoSIT-Validator geprüft; das Ergebnis steht an der Rechnung. Eine Rechnung, die dort durchfällt, wird nicht versendet. |
| **Freigabe** | Zweistufige Freigabe (sachlich, kaufmännisch), Zurückweisen mit Begründung, Stellvertretung bei Abwesenheit. |
| **Versand** | Versand per E-Mail mit PDF und XML im Anhang, Protokoll aller versendeten Mails. |
| **Eingangsrechnungen** | Empfang per E-Mail-Abruf oder Hochladen, Anzeige als lesbarer Beleg, Prüfung, Freigabe, Kontierung. PDF-Rechnungen ohne Daten: Die KI-Erkennung liest Lieferant, Nummer, Beträge und Steuersätze aus und stellt sie zur Übernahme bereit – gespeichert wird erst nach Ihrer Bestätigung. |
| **Zahlungen** | Zahlungseingänge kommen per Direktabruf vom Bankkonto oder als Kontoauszug (CAMT.053, CSV). Automatischer Rechnungsabgleich: Eindeutige Zahlungen werden selbst als bezahlt gebucht, unsichere als Vorschlag vorgelegt, Sammelzahlungen erkannt. |
| **Mahnwesen** | Überfällige Rechnungen, drei Mahnstufen mit Fristen und Gebühren, Mahnschreiben als PDF. |
| **Vorgänge** | Auftragsabwicklung in neun Stufen: Angebotsanfrage, Bestellung, Eingangsrechnung, Angebot, Auftragseingang, Auftragsbestätigung, Lieferschein, Rechnung, Mahnung. Positionen wandern von Stufe zu Stufe mit; Anhänge und Checkliste je Vorgang. Steuerfall an der Rechnungsstufe. |
| **Stammdaten** | Kunden mit mehreren Ansprechpartnern, Lieferanten mit Freigabe, Produkte und Leistungen mit Einkaufs- und Verkaufspreis. Einlesen und Ausgeben als CSV. |
| **Belege gestalten** | Logo, Farben, Tabellenkopf, Fußzeile – einmal eingestellt, gilt für alle Belege. Textvorlagen je Belegart. |
| **Archiv** | Ablage jeder Rechnung mit Original-XML, PDF und Prüfsumme; Verlauf jeder Änderung; Aufbewahrungsfristen. |
| **Auswertung und Weitergabe** | Export der Buchungsdaten als CSV (Knopf „DATEV-Export"), einzeln, monats- oder jahresweise. Steuerberater-Export: alle Ein- und Ausgangsrechnungen eines Zeitraums als PDF-Paket mit Übersicht. |
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
| Rechnung erstellen | Neue Rechnung schreiben, Rechnungen aus einer CSV-Datei einlesen, Gutschrift im Gutschriftverfahren ausstellen |
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
   elektronische Adresse ist eine E-Rechnung nicht gültig. Fehlt sie, wird keine Rechnung angelegt.
3. Belegart und Steuerfall prüfen. Der Normalfall ist „Rechnung (380)" mit „Regelbesteuerung (19 % / 7 %)".
4. Positionen eintragen: Bezeichnung, Menge, Einzelpreis netto, Steuersatz. Leistungen aus dem Produktkatalog lassen sich
   übernehmen.
5. Rechnungsdatum, Leistungsdatum und Zahlungsziel prüfen; bei Bedarf Skonto eintragen.
6. „Validieren" prüft nur. „XRechnung erzeugen & herunterladen" legt die Rechnung an. Die Rechnungsnummer vergibt das
   Programm fortlaufend.

Meldet die Prüfung Fehler, legt das Programm nichts an und vergibt keine Nummer. Die Fehler stehen als Liste unter den
Knöpfen. Die neue Rechnung steht mit dem Status „Neu" in der Liste.

**Steuerfall.** Über den Positionen wählen Sie den Steuerfall. Unter der Auswahl steht jeweils, was er bewirkt und
welche Angaben Pflicht sind.

| Steuerfall | Wann | Was Sie brauchen |
|---|---|---|
| Regelbesteuerung (19 % / 7 %) | Normalfall | Steuersatz je Position |
| Kleinunternehmer (§ 19 UStG) | Sie sind Kleinunternehmer | Ihre Steuernummer genügt, eine USt-ID ist nicht nötig |
| Steuerschuld des Kunden (§ 13b UStG) | Der Kunde schuldet die Umsatzsteuer, zum Beispiel bei Bauleistungen an Bauunternehmen oder Leistungen an Unternehmen im EU-Ausland | Ihre USt-ID und die des Kunden |
| Steuerfreier Umsatz | Befreiung nach einer Vorschrift | Den Grund, etwa „Steuerfrei nach § 4 Nr. 21 UStG" |
| Nullsteuersatz (0 %) | Zum Beispiel bestimmte Photovoltaikanlagen | – |
| Innergemeinschaftliche Lieferung (EU) | Ware an ein Unternehmen in einem anderen EU-Land | Beide USt-IDs, Leistungsdatum; Kunde und Lieferort im EU-Ausland |
| Ausfuhrlieferung (Drittland) | Ware in ein Land außerhalb der EU | Ihre USt-ID |
| Nicht steuerbar (Leistung an Kunden im Drittland) | Leistung an ein Unternehmen außerhalb der EU | Ihre Steuernummer in den Stammdaten |

Außer bei der Regelbesteuerung stehen die Steuersätze der Positionen fest auf 0 %, und die Rechnung trägt den passenden
Hinweis, beim Kleinunternehmer „Gemäß § 19 UStG wird keine Umsatzsteuer berechnet." Fehlt eine Pflichtangabe, nennt das
Programm sie und legt die Rechnung nicht an. Sind Sie Kleinunternehmer, setzen Sie in den Stammdaten das Häkchen
„Kleinunternehmer nach § 19 UStG"; der Steuerfall ist dann vorgewählt. Das Programm prüft die Steuerfälle technisch.
Welcher für Ihren Umsatz gilt, klären Sie im Zweifel mit Ihrer Steuerberatung.

**Kunden im Ausland.** Sitzt Ihr Kunde nicht in Deutschland, wählen Sie in der Karte „Käufer" das „Land". Es steht in
der E-Rechnung und in der Anschrift auf dem PDF. Tragen Sie die USt-ID mit Länderkennzeichen ein, zum Beispiel
`ATU12345678`; passt es nicht zum Land, weist das Programm Sie darauf hin. „Ändern" speichert das Land am Kunden.

**Leitweg-ID, Bestellnummer, Vertragsnummer, Projektnummer.** Bei Behörden gehört die Leitweg-ID in das Feld
„Leitweg-ID / Käuferreferenz"; das Programm prüft Aufbau und Prüfziffer und warnt bei einem Tippfehler. Bei anderen
Kunden tragen Sie Ihre Kundennummer oder das Zeichen des Kunden ein. Bleibt das Feld leer, setzt das Programm die
Kundennummer aus dem Kundenstamm ein, sonst die Rechnungsnummer. Verlangt Ihr Kunde seine Bestellnummer, eine Vertrags-
oder Projektnummer, tragen Sie sie in die drei Felder unter dem Steuerfall ein. Sie stehen in der E-Rechnung an der
vorgesehenen Stelle und auf dem PDF.

**Einheit, Beschreibung, Leistungszeitraum.** Unter jeder Position wählen Sie die „Einheit" der Menge (Stk, Std, Tag,
Monat, psch, m, m², km, kg, l) und ergänzen bei Bedarf eine Beschreibung. Rechnen Sie einen Zeitraum ab, etwa eine
Wartung, tragen Sie über den Positionen „Leistungszeitraum von" und „Leistungszeitraum bis" ein. Gilt für eine Position
ein anderer Zeitraum, tragen Sie ihn in ihrer Zeile bei „Zeitraum" ein – zum Beispiel, wenn eine Rechnung mehrere
Monate abrechnet.

**Rabatt, Nachlass und Zuschlag.** Einen Rabatt auf eine Position tragen Sie in ihrer Zeile ein: „Rabatt" oder
„Zuschlag" wählen, Wert eingeben, Prozent oder Euro wählen, bei Bedarf den Grund nennen. Einen Nachlass oder Zuschlag
auf die ganze Rechnung, etwa Mengenrabatt oder Versandkosten, fügen Sie unter den Positionen mit „+ Nachlass oder
Zuschlag" hinzu. Hat die Rechnung Positionen mit 19 % und 7 %, verteilt „alle Sätze" ihn auf beide; mit „nur 19 %" oder
„nur 7 %" ordnen Sie ihn einem Satz zu. Summen und Umsatzsteuer rechnet das Programm.

**Skonto und Verwendungszweck.** Bei „Skonto" tragen Sie Prozentsatz und Frist ein, bei „Skonto, zweite Stufe" eine
längere Frist mit kleinerem Satz, zum Beispiel 3 % binnen 8 Tagen und 2 % binnen 14 Tagen. Soll Ihr Kunde bei der
Überweisung etwas anderes als die Rechnungsnummer angeben, tragen Sie es bei „Verwendungszweck" ein; der Text steht
auch im GiroCode.

**Adresszusatz und Registernummer des Kunden.** Die Felder „Adresszusatz des Kunden" (etwa Gebäude oder c/o) und
„Registernummer des Kunden" stehen über den Positionen. Bei einem Kunden aus dem Kundenstamm werden sie dort gespeichert.

**Weitere Angaben.** Unter den Positionen finden Sie vier aufklappbare Bereiche. Füllen Sie nur aus, was Ihr Kunde
braucht.

- **„Lieferadresse":** nur, wenn die Ware oder Leistung an eine andere Anschrift ging als die Rechnung. Bei einer
  innergemeinschaftlichen Lieferung oder Ausfuhr gilt ohne Angabe die Anschrift des Kunden als Lieferort.
- **„Lastschrift und Zahlungsempfänger":** Für den Einzug wählen Sie bei „Zahlungsart" den Eintrag „SEPA-Lastschrift"
  und tragen „Mandatsreferenz" und „IBAN des Kunden" ein. In den Stammdaten muss Ihre Gläubiger-ID stehen, sonst wird
  die Rechnung nicht angelegt. Auf der Rechnung steht dann der Einzugshinweis; Überweisungskonto und GiroCode
  entfallen, ebenso ein Skonto. Das Zahlungsziel bestimmt den Tag des Einzugs. „Ändern" speichert Mandat und IBAN am Kunden. Das Programm
  zieht nicht selbst ein – den Einzug veranlassen Sie bei Ihrer Bank. Prüfen Sie beim Versand den Text der E-Mail; er
  nennt die Lastschrift nicht von selbst. Haben Sie Ihre Forderungen abgetreten, etwa an ein Factoring-Unternehmen,
  tragen Sie hier den Zahlungsempfänger ein. Eine Vorgabe aus den Stammdaten ist vorbelegt; leeren Sie das Feld, gilt
  sie für diese Rechnung nicht.
- **„Anhänge zur E-Rechnung":** Unterlagen, die Ihre Rechnung begründen – Aufmaß, Stundennachweis, Lieferschein. Erlaubt
  sind PDF, PNG, JPEG, CSV, XLSX und ODS, zusammen höchstens 5 MB. Die Dateien werden in die E-Rechnung eingebettet.
  Für größere Unterlagen tragen Sie bei „Verweis auf eine Unterlage (Internetadresse)" eine Adresse ein.
- **„Weitere Bezüge":** Ihre Auftragsnummer, Lieferschein, Wareneingangsmeldung, Ausschreibung oder Los, Objektkennung
  (zum Beispiel die Zählernummer) und Kostenstelle des Kunden.

**Rechnungskorrektur.** Haben Sie eine Rechnung mit falschen Angaben verschickt, wählen Sie als Belegart
„Rechnungskorrektur (384)" und tragen die Nummer der Ursprungsrechnung ein. Erfassen Sie die Positionen mit den
richtigen Werten in voller Höhe, nicht nur die Differenz. Die Korrektur bekommt eine neue Rechnungsnummer. **Die
Ursprungsrechnung bleibt bestehen und offen** – das Programm verknüpft beide nur. Ersetzt die Korrektur die Rechnung
vollständig, stornieren Sie die Ursprungsrechnung, sonst wird der Betrag zweimal gefordert (siehe auch 6).

**Währung.** Rechnungen stellen Sie nur in Euro aus. Eingegangene Rechnungen in anderer Währung liest das Programm.

### 4. Rechnungen aus dem eigenen Programm einlesen

„Rechnung erstellen" → „Rechnungen aus einer CSV-Datei einlesen". Datei wählen, **„1. Prüfen"** – dabei wird nichts
angelegt. Das Ergebnis zeigt je Rechnung „in Ordnung" oder den Fehler mit Zeilennummer. **„2. Fehlerfreie einlesen"**
legt die fehlerfreien Rechnungen an. Eine Rechnungsnummer, die es schon gibt, wird abgewiesen; doppelt wird nichts
angelegt. Die Datei kann auch Steuerfall, Leistungszeitraum, Rabatt, Land des Kunden, Lieferadresse, Lastschrift und
Rechnungskorrekturen enthalten. Das Dateiformat steht in der Unterlage „CSV-Import-Rechnungen", eine Muster-Datei gibt
es im selben Fenster.

### 5. Prüfen, freigeben, versenden

- **Rechnung öffnen:** Klick auf die Zeile in der Liste. Rechts steht die Karte „Validierung" mit dem Ergebnis der
  eingebauten Prüfung und darunter „Amtliche Prüfung (KoSIT)". Die amtliche Prüfung läuft von selbst und braucht einige
  Sekunden; solange steht dort „läuft noch".
- **Weitere Angaben:** Die gleichnamige Karte zeigt Steuerfall, Lieferadresse, Lastschrift, Bezüge und Anhänge der
  Rechnung. Vor der Freigabe können Sie dort Anhänge ergänzen („Anhängen") oder entfernen.
- **Belegansicht:** zeigt die Rechnung so, wie der Empfänger sie liest.
- **Freigeben:** einmal für die sachliche, einmal für die kaufmännische Freigabe. Danach ist die Rechnung
  „Freigegeben". **Zurückweisen** verlangt eine Begründung.
- **Per E-Mail senden:** Empfänger, Betreff und Text prüfen, senden. Mitgeschickt werden das PDF und die XML-Datei.
  Fällt die Rechnung bei der amtlichen Prüfung durch, wird sie nicht versendet; der Grund steht in der Karte
  „Validierung".
- **ZUGFeRD-PDF:** lädt das PDF mit eingebetteten Rechnungsdaten herunter.

### 6. Gutschrift, Storno und Rechnungskorrektur

In der geöffneten Rechnung:

- **Gutschrift:** mindert die Rechnung um einen Teilbetrag. Grund und Positionen eintragen. Die Rechnung bleibt gültig,
  der offene Betrag sinkt.
- **Stornieren:** hebt die Rechnung in voller Höhe auf. Die Rechnung bekommt den Status „Storniert".

Beide Belege sind eigene E-Rechnungen und nennen Nummer und Datum der Ursprungsrechnung. Stornieren und Gutschreiben geht
nur bei freigegebenen Rechnungen. Den Steuerfall übernehmen sie von der Rechnung.

| Sie wollen … | Beleg | Wirkung auf die Ursprungsrechnung |
|---|---|---|
| einen Teilbetrag erstatten | Gutschrift (381) | bleibt gültig, der offene Betrag sinkt |
| die Rechnung ganz aufheben | Storno | Status „Storniert" |
| falsche Angaben berichtigen | Rechnungskorrektur (384), unter „Rechnung erstellen" | keine – sie wird nur verknüpft; bei vollem Ersatz selbst stornieren |

**Gutschrift im Gutschriftverfahren.** Das ist etwas anderes als die Gutschrift oben: Mit manchen Lieferanten ist
vereinbart, dass nicht der Lieferant eine Rechnung schreibt, sondern Sie selbst über seine Leistung abrechnen – zum
Beispiel bei Provisionen, Honoraren oder Lizenzen. Schalten Sie die Funktion in den Einstellungen mit dem Häkchen
„Gutschriftverfahren nutzen" ein. Danach finden Sie unter „Rechnung erstellen" die Karte „Gutschrift im
Gutschriftverfahren ausstellen". Wählen Sie den Lieferanten aus dem Lieferantenstamm oder tragen Sie ihn ein (Name,
Anschrift, USt-ID oder Steuernummer, E-Mail-Adresse, Bankverbindung), ergänzen Sie Datum und Positionen und klicken Sie
auf „1. Prüfen". Ist alles in Ordnung, bestätigen Sie mit dem Häkchen, dass das Verfahren mit dem Lieferanten vereinbart
ist, und klicken auf „2. Ausstellen". Die Gutschrift erhält eine Nummer, die mit „GV" beginnt, und steht in der
Rechnungsliste beim Eingang – Sie müssen sie bezahlen, und zwar auf das Konto des Lieferanten. Ist der Lieferant
Kleinunternehmer, wählen Sie bei „Steuerfall des Lieferanten" den Eintrag „Kleinunternehmer (§ 19 UStG) – keine
Umsatzsteuer"; die Gutschrift weist dann keine Umsatzsteuer aus. Wählen Sie das nur, wenn der Lieferant es bestätigt
hat. Andere Steuerfälle, Skonto, Nachlässe und Lastschrift gibt es im Gutschriftverfahren nicht.

**Senden und stornieren.** In der Ansicht der Gutschrift finden Sie „An Lieferanten senden". Das Programm schlägt
Adresse, Betreff und Text vor; mit „Senden" gehen E-Rechnung und PDF an den Lieferanten. Der Versand steht im Verlauf
des Belegs und unter „Mails". Ist kein Postausgang eingerichtet, wird die Nachricht nur abgelegt – das Programm sagt
Ihnen das. War die Gutschrift falsch oder widerspricht der Lieferant, klicken Sie auf „Gutschrift stornieren" und geben
den Grund an. Der Stornobeleg trägt „ST-" vor der Gutschriftnummer; senden Sie ihn dem Lieferanten auf dieselbe Weise.
Storniert wird immer der ganze Beleg, und das lässt sich nicht zurücknehmen. War die Gutschrift schon bezahlt, weist das
Programm Sie darauf hin: Den Betrag fordern Sie vom Lieferanten zurück oder verrechnen ihn.

**Gutschriften Ihrer Kunden.** Rechnet ein Kunde im Gutschriftverfahren über Ihre Leistung ab, lesen Sie seine
Gutschrift wie jede E-Rechnung ein (siehe 10). Das Programm erkennt an Ihrer USt-ID, Steuernummer oder Ihrem
Firmennamen, dass Sie der leistende Unternehmer sind, und führt den Beleg als Ausgang: Er ist Ihre Forderung an den
Kunden. Prüfen Sie die Gutschrift und klicken Sie auf „Anerkennen". Stimmt sie nicht, klicken Sie auf „Widersprechen"
und geben den Grund an; sie gilt dann nicht mehr als Rechnung. Das Programm vermerkt den Widerspruch nur – teilen Sie
ihn dem Kunden selbst mit und stellen Sie eine eigene Rechnung. Den Zahlungseingang verbuchen Sie wie bei einer eigenen
Rechnung. **Gemahnt wird eine solche Gutschrift vom Programm nicht.** Versenden, stornieren oder gutschreiben lässt sie
sich nicht; Korrekturen kommen vom Kunden. Ist das Programm nicht sicher, ob der Beleg von einem Kunden stammt, bleibt
er Eingangsrechnung, und Sie entscheiden in der Ansicht des Belegs: „Ist eine Gutschrift meines Kunden" oder „Ist eine
Eingangsrechnung".

### 7. Zahlungen

- **Bank:** Kontoauszug hochladen oder die Umsätze direkt abrufen. Das Programm ordnet die Zahlungseingänge den offenen
  Rechnungen zu. Eindeutige Zahlungen sind danach „Bezahlt"; bei unsicheren schlägt es eine Rechnung vor, die Sie
  bestätigen oder ändern.
- **Von Hand:** in der Rechnung „Als bezahlt markieren".
- **Skonto:** Zieht der Kunde Skonto ab, zeigt die Rechnung, ob die Zahlung in der Frist lag. Einen Abzug nach der Frist
  können Sie anerkennen oder offen lassen. Der Abgleich kennt nur die erste Skontostufe.

### 8. Mahnungen

„Mahnungen" zeigt überfällige Rechnungen mit dem Vorschlag der nächsten Mahnstufe. Das Mahnschreiben entsteht per Klick
als PDF. Bei Rechnungen mit Lastschrift prüfen Sie vorher, ob der Einzug gelaufen ist.

### 9. Vorgänge

Ein Vorgang führt einen Auftrag durch bis zu neun Stufen. Sie legen ihn mit Betreff, Kunde und – wenn eingekauft wird –
Lieferant an. In jeder Stufe tragen Sie Positionen ein oder übernehmen sie aus der vorigen Stufe, erzeugen das Dokument
als PDF, geben frei und versenden. Stufen, die Sie nicht brauchen, überspringen Sie. Die Rechnung der Stufe „Rechnung"
ist eine E-Rechnung wie unter „Rechnung erstellen".

**Steuerfall im Vorgang.** An der Rechnungsstufe wählen Sie bei „Steuerfall der Rechnung" zwischen Regelbesteuerung,
Kleinunternehmer, Steuerschuld des Kunden, steuerfreiem Umsatz und Nullsteuersatz. Abschlagsrechnungen des Vorgangs
folgen demselben Steuerfall.

**Was im Vorgang nicht geht:** Die Rechnung aus einem Vorgang trägt das Land des Kunden, aber keine Lieferadresse, keine
Lastschrift, keinen abweichenden Zahlungsempfänger und keine weiteren Bezüge. Die Steuerfälle für das Ausland
(innergemeinschaftliche Lieferung, Ausfuhr, nicht steuerbar) gibt es dort nicht. Brauchen Sie das, schreiben Sie die
Rechnung unter „Rechnung erstellen". Anhänge ergänzen Sie vor der Freigabe in der Rechnungsansicht.

**Achtung beim Versenden aus dem Vorgang:** Der farbige Knopf versendet an den Kunden; „Nur freigeben" versendet nicht.
Vor dem Senden zeigt das Programm Vorgang, Kunde, Beleg und Empfängeradresse zur Kontrolle.

### 10. Eingangsrechnungen

Eingangsrechnungen kommen per E-Mail-Abruf oder durch Hochladen in „Rechnungen". E-Rechnungen (XRechnung, ZUGFeRD) werden
gelesen und geprüft. Bei einer reinen PDF-Rechnung liest die KI-Erkennung die Angaben aus und zeigt sie in einem Fenster –
Sie prüfen, ändern bei Bedarf und übernehmen. Danach folgen Freigabe und Kontierung wie bei Ausgangsrechnungen.

Die Karte „Weitere Angaben" zeigt, was der Lieferant zusätzlich mitgeschickt hat, zum Beispiel Lieferadresse,
Lastschriftdaten und Bezüge; seine Anhänge laden Sie dort herunter. Eine Gutschrift, die ein Kunde über Ihre Leistung
ausgestellt hat, führt das Programm als Forderung (siehe 6).

### 11. Wenn zwei gleichzeitig arbeiten

Alle Mitarbeiter sehen denselben Bestand. Öffnet jemand einen Vorgang, den ein Kollege in den letzten Minuten ebenfalls
offen hatte, erscheint ein Hinweis mit dessen Namen. Speichern Sie einen Eintrag, den ein anderer nach Ihrem Laden
geändert hat, fragt das Programm nach: „OK" speichert trotzdem und überschreibt die Änderung des Kollegen; „Abbrechen"
speichert nicht – laden Sie dann neu und tragen Sie Ihre Änderung noch einmal ein.

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

**Weitere Felder der Stammdaten.** Füllen Sie aus, was auf Sie zutrifft, und klicken Sie auf „Stammdaten speichern".

| Feld | Wofür |
|---|---|
| „Kleinunternehmer nach § 19 UStG" | Wählt bei neuen Rechnungen den Steuerfall Kleinunternehmer vor. Je Rechnung änderbar. |
| „Handelsregisternummer", „Rechtsangaben", „Adresszusatz" | Stehen in jeder E-Rechnung, zum Beispiel `HRB 4711`, Geschäftsführer und Registergericht, Gebäude. |
| „Verkäuferkennung" | Ohne USt-ID und ohne Handelsregisternummer verlangt die E-Rechnung eine Kennung; bleibt das Feld leer, nimmt das Programm Ihre Steuernummer. |
| „Gläubiger-ID (SEPA)" | Pflicht, wenn Sie Rechnungen mit Lastschrift ausstellen. |
| „Zahlungsempfänger – Name" | Nur wenn ein anderer das Geld erhält, etwa ein Factoring-Unternehmen. Ist dann bei jeder neuen Rechnung vorbelegt. |
| „Gutschriftverfahren nutzen" | Schaltet die Karte „Gutschrift im Gutschriftverfahren ausstellen" ein. |

**Kunden.** Unter „Kunden" tragen Sie je Kunde das „Land" und für die Lastschrift „IBAN (für Lastschrift)" und
„Mandatsreferenz" ein. Die Angaben sind dann beim Erstellen der Rechnung vorbelegt.

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
Gutschriften im Gutschriftverfahren haben ein eigenes, schlichtes PDF ohne Logo und Farben.

Die **Textvorlagen** (Einleitung und Schlusstext je Belegart) pflegen Sie ebenfalls in den Einstellungen.

### 4. E-Mail einrichten

- **Versand (SMTP):** Server, Benutzer, Passwort und Absenderadresse Ihres Mailkontos. Mit „SMTP-Verbindung testen"
  prüfen Sie die Angaben.
- **Empfang (IMAP):** das Postfach, an das Ihre Lieferanten Rechnungen schicken. „Automatischer E-Mail-Abruf" holt neue
  Rechnungen regelmäßig ab; „Postfach jetzt prüfen" sofort.

Die Protokolle für Empfang und Versand stehen darunter.

### 5. Bank anbinden

„Bank" → „Direktabruf": Bank und Zugangsdaten eintragen; die Freigabe erfolgt mit dem Verfahren Ihrer Bank, zum Beispiel
in der Banking-App. Das Programm liest nur Umsätze und Kontostand. Überweisungen und Lastschrifteinzüge sind nicht
möglich. Ohne Direktabruf laden Sie den Kontoauszug als Datei hoch.

### 6. Nummernkreise

Rechnungen laufen als `RE-Jahr-Nummer`, Gutschriften als `GS-Jahr-Nummer`; Stornos tragen `ST-` vor der Nummer der
stornierten Rechnung. Rechnungskorrekturen zählen im Kreis der Rechnungen, Gutschriften im Gutschriftverfahren im eigenen
Kreis `GV-Jahr-Nummer`. Eigene Nummern aus einem anderen Programm werden beim Einlesen unverändert übernommen.

### 7. Sicherheit und Daten

- Der Zugang ist verschlüsselt. Ihre Firma arbeitet in einem eigenen, gesicherten Bereich.
- Die Daten werden täglich gesichert.
- **Export:** Jede Rechnung als PDF und XML, alle Rechnungen eines Zeitraums über „Steuerberater", die Buchungsdaten
  als CSV über den Knopf „DATEV-Export", Stammdaten als CSV.
- **Aufbewahrung:** Rechnungen sind gesetzlich acht Jahre aufzubewahren. Die Fristen stellen Sie in den Einstellungen
  ein. Für eine ordnungsgemäße Aufbewahrung nach den GoBD gehört dazu eine Beschreibung Ihres Ablaufs im Betrieb; die
  liefert das Programm nicht mit.

### 8. Wenn etwas nicht geht

| Erscheinung | Ursache und Abhilfe |
|---|---|
| „Rechnungsnummer existiert bereits" | Die Nummer ist vergeben. Andere Nummer eintragen oder das Feld leer lassen. |
| „Die Rechnung wurde nicht angelegt." | Die eingebaute Prüfung meldet Fehler; sie stehen als Liste darunter. Beheben und erneut erzeugen. |
| Rechnung lässt sich nicht senden | Die amtliche Prüfung meldet Fehler. In der Karte „Validierung" nachlesen, Rechnung stornieren und richtig neu schreiben. |
| „Buyer Electronic Address fehlt" | Die E-Mail-Adresse des Kunden fehlt. |
| Steuersatz der Position lässt sich nicht ändern | Der gewählte Steuerfall rechnet ohne Umsatzsteuer. Für 19 % oder 7 % den Steuerfall auf Regelbesteuerung stellen. |
| Lastschrift: Rechnung wird nicht angelegt | Gläubiger-ID in den Stammdaten, Mandatsreferenz oder IBAN des Kunden fehlt. |
| Rechnungskorrektur wird abgelehnt | Die Nummer der Ursprungsrechnung fehlt, oder die Rechnung steht nicht im Programm. |
| Hinweis „wurde geändert, nachdem Sie ihn geladen haben" | Ein Kollege hat denselben Eintrag gespeichert. Neu laden und die eigene Änderung erneut eintragen. |
| Nur Lesen möglich | Der Zugang ist pausiert, weil die Zahlung fehlt. Der Stand und die Zahlungsdaten stehen unter `/zugang`. |
| Anmeldung gesperrt | Nach fünf Fehlversuchen 15 Minuten warten. |

Bei Fragen wenden Sie sich an die Adresse, von der Sie Ihre Zugangsdaten erhalten haben.
