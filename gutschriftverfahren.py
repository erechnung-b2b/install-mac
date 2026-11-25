#!/usr/bin/env python3
"""
E-Rechnungssystem – Gutschriftverfahren (Selbstfakturierung, § 14 Abs. 2 Satz 2 UStG)

Beim Gutschriftverfahren stellt der LEISTUNGSEMPFÄNGER die Abrechnung aus. In der
E-Rechnung (EN 16931 / XRechnung) heißt das:

    Belegart (BT-3)      389  „Self-billed invoice“
    Verkäufer (BG-4)     der Geschäftspartner / Lieferant (leistender Unternehmer)
    Käufer    (BG-7)     der Mandant (Aussteller des Belegs, Leistungsempfänger)
    Zahlung   (BG-17)    Bankverbindung des LIEFERANTEN

Nicht zu verwechseln mit der kaufmännischen Gutschrift / Rechnungskorrektur
(Belegart 381, Nummernkreis GS-…), die das Programm an anderer Stelle hat.

Das Modul ist abschaltbar (Standard: AUS). Es wird am Ende von webapp.py mit
wenigen Zeilen eingehängt:

    import gutschriftverfahren as _gv
    _gv.einrichten(app, globals())

und benutzt aus webapp.py nur, was in `_BRAUCHT` / `_OPTIONAL` unten steht.
Fehlt ein optionaler Teil (KoSIT, Java, Workflow, Schriften), läuft es weiter.
"""
from __future__ import annotations

import io
import json
import re
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from models import (Invoice, Seller, Buyer, Address, Contact, PaymentInfo, InvoiceLine)
try:                                             # Steuerfall (Kern ab 25.11.2026); fehlt er, gibt es nur Regelbesteuerung
    from models import steuerfall_setzen as _steuerfall_setzen
except ImportError:
    _steuerfall_setzen = None
from validator import validate_invoice
from xrechnung_generator import generate_and_serialize

VERSION = "1.2.0"
BELEGART = "389"
PRAEFIX = "GV"
EREIGNIS = "GUTSCHRIFTVERFAHREN_ERSTELLT"
# 1.2: Storno, Versand, vom Kunden ausgestellte Gutschriften
EREIGNIS_STORNO = "GUTSCHRIFTVERFAHREN_STORNO_ERSTELLT"      # am Stornobeleg (ST-GV-…, Belegart 381)
EREIGNIS_STORNIERT = "GUTSCHRIFTVERFAHREN_STORNIERT"          # am ursprünglichen Beleg
EREIGNIS_VERSENDET = "GUTSCHRIFTVERFAHREN_VERSENDET"
EREIGNIS_KUNDE = "GUTSCHRIFTVERFAHREN_VOM_KUNDEN"             # eingegangene 389, Verkäufer = wir → als Ausgang geführt
EREIGNIS_UNSICHER = "GUTSCHRIFTVERFAHREN_EINGANG_UNSICHER"    # eingegangene 389, Verkäufer vielleicht wir → nur Hinweis
EREIGNIS_BLEIBT = "GUTSCHRIFTVERFAHREN_BLEIBT_EINGANG"        # von Hand entschieden: ist eine Eingangsrechnung
EREIGNIS_ANERKANNT = "GUTSCHRIFTVERFAHREN_ANERKANNT"
EREIGNIS_WIDERSPROCHEN = "GUTSCHRIFTVERFAHREN_WIDERSPROCHEN"
STORNO_BELEGART = "381"      # XRechnung-tauglich (BR-DE-17); 261 „Self billed credit note“ wäre nur mit Warnung gültig
STORNO_BEDINGUNG = "Ein bereits gezahlter Betrag ist zu erstatten oder wird verrechnet."
SCHLUESSEL = "gutschriftverfahren"            # Abschnitt in mandant_settings.json
HINWEIS_BT22 = ("Gutschrift im Gutschriftverfahren gemäß § 14 Abs. 2 Satz 2 UStG "
                "(Abrechnung durch den Leistungsempfänger).")
TEXT_19 = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."
# Steuerfall des LIEFERANTEN (er ist der leistende Unternehmer). Mehr Fälle bietet das Modul bewusst nicht an.
STEUERFAELLE = {"regel": "Regelbesteuerung", "kleinunternehmer": "Kleinunternehmer (§ 19 UStG) – keine Umsatzsteuer"}
_KENNUNG_IM_KERN = hasattr(Seller, "kennung")     # BT-29: Steuernummer genügt als Verkäuferkennung (BR-CO-26)
STANDARD_BEDINGUNG = "Der Gutschriftsbetrag wird auf das angegebene Konto des leistenden Unternehmers überwiesen."

# Namen aus webapp.py (globals()), die das Modul benutzt
_BRAUCHT = ("invoices", "_load_mandant_settings", "_save_mandant_settings", "archive", "auto_save")
_OPTIONAL = ("inbox", "dash", "wf_engine", "_wer", "_kosit_pruefen", "_KOSIT_SPERRE", "_ERZEUGEN_SPERRE",
             "_pruefmeldungen", "_build_invoice_pdf", "generate_zugferd_pdf", "_log_sent_mail",
             "_is_invoice_sent", "_is_invoice_overdue")
# Wird bei jedem Aufruf frisch aus webapp.py gelesen (dort tauscht load_data() das Objekt aus): email_sender

_BASIS = Path(__file__).resolve().parent
_SPERRE = threading.RLock()                   # eine Ausstellung zur Zeit (Nummernvergabe)
_KOSIT_LAEUFT: set = set()
_G: dict = {}                                 # globals() von webapp, gesetzt in einrichten()

# Einheiten: Anzeige → UN/ECE-Code (Rec. 20), so wie invoice_pdf._EINHEITEN sie zurückübersetzt
EINHEITEN = {
    "C62": "Stk", "H87": "Stk", "LS": "Pauschal", "HUR": "Std", "DAY": "Tag", "MON": "Monat",
    "MTR": "m", "MTK": "m²", "MTQ": "m³", "KGM": "kg", "TNE": "t", "LTR": "l", "KWH": "kWh",
    "KMT": "km", "SET": "Satz", "P1": "%",
}
_EINHEIT_WORT = {
    "stk": "C62", "stk.": "C62", "stück": "C62", "stueck": "C62", "st": "C62", "": "C62",
    "pauschal": "LS", "psch": "LS", "std": "HUR", "std.": "HUR", "stunde": "HUR", "stunden": "HUR", "h": "HUR",
    "tag": "DAY", "tage": "DAY", "monat": "MON", "m": "MTR", "m2": "MTK", "m²": "MTK", "qm": "MTK",
    "m3": "MTQ", "m³": "MTQ", "cbm": "MTQ", "kg": "KGM", "t": "TNE", "to": "TNE", "l": "LTR", "liter": "LTR",
    "kwh": "KWH", "km": "KMT", "satz": "SET", "%": "P1",
}


# ══════════════════════════════════════════════════════════════════════
# Kleine Helfer
# ══════════════════════════════════════════════════════════════════════

class Eingabefehler(ValueError):
    """Die Anfrage ist so nicht auswertbar (kein Prüfbefund, sondern z. B. ein unlesbares Datum)."""


def _s(wert) -> str:
    return " ".join(str(wert if wert is not None else "").split())


def _erste(d: dict, *namen) -> str:
    for n in namen:
        v = d.get(n)
        if v not in (None, ""):
            return _s(v)
    return ""


def _dezimal(wert, feld: str) -> Decimal:
    """Zahl aus JSON – auch deutsche Schreibweise „1.234,50“."""
    if isinstance(wert, bool) or wert is None or wert == "":
        raise Eingabefehler(f"{feld}: Zahl fehlt.")
    if isinstance(wert, (int, Decimal)):
        return Decimal(wert)
    if isinstance(wert, float):
        return Decimal(str(wert))
    t = str(wert).strip().replace(" ", "").replace("€", "").replace("%", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise Eingabefehler(f"{feld}: „{wert}“ ist keine Zahl.")
    if not d.is_finite():
        raise Eingabefehler(f"{feld}: „{wert}“ ist keine Zahl.")
    return d


def _datum(wert, feld: str, pflicht=False):
    if wert in (None, ""):
        if pflicht:
            raise Eingabefehler(f"{feld} fehlt.")
        return None
    if isinstance(wert, date):
        return wert
    t = str(wert).strip()
    try:
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", t):
            tt, mm, jj = t.split(".")
            return date(int(jj), int(mm), int(tt))
        return date.fromisoformat(t[:10])
    except ValueError:
        raise Eingabefehler(f"{feld}: „{wert}“ ist kein Datum (JJJJ-MM-TT oder TT.MM.JJJJ).")


def _de_datum(d) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _geld(v) -> str:
    q = Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ganz, _, nach = f"{abs(q):.2f}".partition(".")
    gruppen = []
    while len(ganz) > 3:
        gruppen.insert(0, ganz[-3:])
        ganz = ganz[:-3]
    gruppen.insert(0, ganz)
    return ("-" if q < 0 else "") + ".".join(gruppen) + "," + nach


def _zahl(v) -> str:
    d = Decimal(str(v)).normalize()
    t = format(d, "f")
    return t.replace(".", ",")


def iban_ok(iban: str) -> bool:
    """Prüfziffer nach ISO 13616 (Modulo 97)."""
    t = re.sub(r"\s+", "", iban or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", t):
        return False
    if t.startswith("DE") and len(t) != 22:
        return False
    umgestellt = t[4:] + t[:4]
    ziffern = "".join(str(int(c, 36)) for c in umgestellt)
    return int(ziffern) % 97 == 1


def _iban_gruppiert(iban: str) -> str:
    t = re.sub(r"\s+", "", iban or "").upper()
    return " ".join(t[i:i + 4] for i in range(0, len(t), 4))


def einheit_code(wert) -> str:
    t = _s(wert)
    if t.upper() in EINHEITEN:
        return t.upper()
    return _EINHEIT_WORT.get(t.lower(), t.upper() if re.fullmatch(r"[A-Za-z0-9]{2,3}", t) else "C62")


def einheit_text(code) -> str:
    return EINHEITEN.get(str(code or "").upper(), str(code or ""))


def steuerfaelle_verfuegbar() -> dict:
    """Welche Steuerfälle dieser Programmstand ausstellen kann."""
    return dict(STEUERFAELLE) if _steuerfall_setzen else {"regel": STEUERFAELLE["regel"]}


def steuerfall_lesen(daten: dict) -> str:
    """„regel“ oder „kleinunternehmer“ aus der Anfrage (Feld steuerfall, auch am Lieferanten; oder kleinunternehmer: true)."""
    lf = daten.get("lieferant") if isinstance(daten.get("lieferant"), dict) else {}
    roh = daten.get("steuerfall") or lf.get("steuerfall") or ""
    t = "".join(str(roh).lower().split())
    if not t:
        return "kleinunternehmer" if (daten.get("kleinunternehmer") is True or lf.get("kleinunternehmer") is True) else "regel"
    if t in ("regel", "regelbesteuerung", "standard", "s"):
        return "regel"
    if t in ("kleinunternehmer", "klein", "§19", "§19ustg", "19ustg"):
        return "kleinunternehmer"
    raise Eingabefehler(f"Steuerfall „{roh}“ ist im Gutschriftverfahren nicht vorgesehen. Möglich sind: regel, kleinunternehmer.")


def ist_ohne_ust(inv) -> bool:
    return (getattr(inv, "steuerfall", "regel") or "regel") == "kleinunternehmer"


def steuerhinweis(inv) -> str:
    """Hinweissatz für das PDF, wenn keine Umsatzsteuer berechnet wird."""
    if not ist_ohne_ust(inv):
        return ""
    try:
        return inv.steuerhinweis() or TEXT_19
    except Exception:
        return TEXT_19


def _hat(inv, *ereignisse) -> bool:
    return any(getattr(e, "event_type", "") in ereignisse for e in (getattr(inv, "audit_trail", None) or []))


def ist_gv(inv) -> bool:
    """Ist das ein von UNS im Gutschriftverfahren ausgestellter Beleg? (Belegart 389 + eigener Verlaufseintrag —
    eine EINGEHENDE 389 eines Kunden hat den Eintrag nicht.)"""
    if str(getattr(inv, "invoice_type_code", "") or "") != BELEGART:
        return False
    return _hat(inv, EREIGNIS)


def ist_gv_storno(inv) -> bool:
    """Stornobeleg zu einer selbst ausgestellten Gutschrift (Belegart 381, Nummer ST-GV-…)."""
    return str(getattr(inv, "invoice_type_code", "") or "") == STORNO_BELEGART and _hat(inv, EREIGNIS_STORNO)


def ist_gv_kunde(inv) -> bool:
    """Vom KUNDEN ausgestellte Gutschrift über unsere Leistung (389, Verkäufer = wir), als Ausgang geführt."""
    return str(getattr(inv, "invoice_type_code", "") or "") == BELEGART and _hat(inv, EREIGNIS_KUNDE)


def art(inv) -> str:
    """'eigen' | 'storno' | 'kunde' | 'unsicher' | '' (kein Beleg dieses Moduls)."""
    if inv is None:
        return ""
    if ist_gv(inv):
        return "eigen"
    if ist_gv_storno(inv):
        return "storno"
    if ist_gv_kunde(inv):
        return "kunde"
    if (str(getattr(inv, "invoice_type_code", "") or "") == BELEGART and _hat(inv, EREIGNIS_UNSICHER)
            and not _hat(inv, EREIGNIS_BLEIBT)):
        return "unsicher"
    return ""


def _webapp(name):
    """Name frisch aus webapp.py (für Objekte, die dort zur Laufzeit ausgetauscht werden)."""
    g = _G.get("__webapp__")
    return g.get(name) if isinstance(g, dict) else _G.get(name)


# ══════════════════════════════════════════════════════════════════════
# Einstellungen und Nummernkreis (mandant_settings.json → "gutschriftverfahren")
# ══════════════════════════════════════════════════════════════════════

def _ms() -> dict:
    return _G["_load_mandant_settings"]() or {}


def _abschnitt(ms=None) -> dict:
    a = (ms if ms is not None else _ms()).get(SCHLUESSEL)
    return dict(a) if isinstance(a, dict) else {}


def ist_aktiv(ms=None) -> bool:
    return bool(_abschnitt(ms).get("aktiv"))


def aktiv_setzen(an: bool) -> bool:
    with _SPERRE:
        ms = _ms()
        a = _abschnitt(ms)
        a["aktiv"] = bool(an)
        ms[SCHLUESSEL] = a
        _G["_save_mandant_settings"](ms)
    return bool(an)


def naechste_nummer(advance: bool = False) -> str:
    """Nächste Nummer GV-JJJJ-NNNNN. advance=False: nur Vorschau. Jahreswechsel setzt den Zähler zurück;
    vergebene Nummern werden übersprungen (wie _next_invoice_number in webapp.py)."""
    with _SPERRE:
        ms = _ms()
        a = _abschnitt(ms)
        jahr = date.today().year
        try:
            zaehler = int(a.get("zaehler") or 0) if int(a.get("jahr") or jahr) == jahr else 0
        except (TypeError, ValueError):
            zaehler = 0
        vergeben = {getattr(i, "invoice_number", "") for i in list(_G["invoices"].values())}
        n = zaehler + 1
        while f"{PRAEFIX}-{jahr}-{n:05d}" in vergeben:
            n += 1
        if advance:
            a["jahr"], a["zaehler"] = jahr, n
            ms[SCHLUESSEL] = a
            _G["_save_mandant_settings"](ms)
        return f"{PRAEFIX}-{jahr}-{n:05d}"


# ══════════════════════════════════════════════════════════════════════
# Beleg bauen und prüfen
# ══════════════════════════════════════════════════════════════════════

def _lieferant_lesen(d: dict) -> dict:
    d = d if isinstance(d, dict) else {}
    name = _erste(d, "name", "firma")
    email = _erste(d, "email", "e_mail")
    return {
        "name": name,
        "strasse": _erste(d, "strasse", "street"),
        "hausnummer": _erste(d, "hausnummer", "house_number"),
        "plz": _erste(d, "plz", "post_code"),
        "ort": _erste(d, "ort", "city"),
        "land": (_erste(d, "land", "country", "country_code") or "DE").upper()[:2],
        "ust_id": _erste(d, "ust_id", "vat_id").replace(" ", "").upper(),
        "steuernummer": _erste(d, "steuernummer", "tax_registration_id"),
        "email": email,
        # BR-DE-2/5/6/7: die XRechnung verlangt beim Verkäufer Ansprechpartner, Telefon und E-Mail.
        # Ohne eigene Angabe gilt der Firmenname bzw. die allgemeine E-Mail-Adresse; das Telefon muss angegeben sein.
        "ansprechpartner": _erste(d, "ansprechpartner", "contact_name") or name,
        "telefon": _erste(d, "telefon", "phone", "contact_phone"),
        "kontakt_email": _erste(d, "kontakt_email", "contact_email") or email,
        "iban": re.sub(r"\s+", "", _erste(d, "iban")).upper(),
        "bic": _erste(d, "bic").replace(" ", "").upper(),
        "lieferant_id": _erste(d, "lieferant_id", "id"),
    }


def _mandant_lesen(ms: dict) -> dict:
    return {
        "name": _s(ms.get("name")),
        "strasse": _s(ms.get("street")),
        "hausnummer": _s(ms.get("house_number")),
        "plz": _s(ms.get("post_code")),
        "ort": _s(ms.get("city")),
        "land": (_s(ms.get("country")) or "DE").upper()[:2],
        "ust_id": _s(ms.get("vat_id")).replace(" ", "").upper(),
        "steuernummer": _s(ms.get("tax_registration_id")),
        "email": _s(ms.get("email")),
        "ansprechpartner": _s(ms.get("contact_name")),
        "telefon": _s(ms.get("contact_phone")),
    }


def beleg_bauen(daten: dict, ms: dict, nummer: str):
    """Baut aus der Anfrage das Fachobjekt. Rückgabe (Invoice, eigene_befunde).
    eigene_befunde: Liste (regel, schwere, meldung, feld) – Prüfungen, die die allgemeine Prüfung nicht kennt."""
    daten = daten if isinstance(daten, dict) else {}
    lf = _lieferant_lesen(daten.get("lieferant") or {})
    md = _mandant_lesen(ms)
    befunde = []

    def fehler(regel, meldung, feld=""):
        befunde.append((regel, "ERROR", meldung, feld))

    def warnung(regel, meldung, feld=""):
        befunde.append((regel, "WARNING", meldung, feld))

    belegdatum = _datum(daten.get("datum"), "Datum") or date.today()
    leistung = _datum(daten.get("leistungsdatum"), "Leistungsdatum")
    leistung_von = _datum(daten.get("leistung_von"), "Leistungszeitraum von")
    leistung_bis = _datum(daten.get("leistung_bis"), "Leistungszeitraum bis")
    if not leistung and not leistung_bis:
        fehler("GV-LEISTUNG", "Leistungsdatum fehlt (Zeitpunkt der Lieferung oder Leistung, § 14 Abs. 4 Nr. 6 UStG).", "BT-72")
    if leistung_von and leistung_bis and leistung_von > leistung_bis:
        fehler("GV-LEISTUNG", "Leistungszeitraum: „von“ liegt nach „bis“.", "BT-73/BT-74")

    # Zahlung: Ziel in Tagen und/oder Text
    faellig = None
    tage = daten.get("zahlungsziel_tage")
    if tage not in (None, ""):
        try:
            tage = int(str(tage).strip())
            if tage < 0 or tage > 365:
                raise ValueError
            faellig = belegdatum + timedelta(days=tage)
        except ValueError:
            raise Eingabefehler("Zahlungsziel: bitte eine Zahl von 0 bis 365 Tagen angeben.")
    else:
        tage = None
    bedingung = _s(daten.get("zahlungsbedingung"))
    if not bedingung:
        bedingung = (f"Zahlung innerhalb von {tage} Tagen auf das angegebene Konto des leistenden Unternehmers."
                     if tage else STANDARD_BEDINGUNG)

    notiz = str(daten.get("notiz") or "").strip()
    referenz = _s(daten.get("kaeuferreferenz")) or _s(_abschnitt(ms).get("kaeuferreferenz")) or nummer

    inv = Invoice(
        invoice_number=nummer,
        invoice_date=belegdatum,
        invoice_type_code=BELEGART,
        currency_code="EUR",
        buyer_reference=referenz,
        tax_point_date=leistung,
        period_start=leistung_von,
        period_end=leistung_bis or leistung,
        note=HINWEIS_BT22 + ("\n" + notiz if notiz else ""),
        order_reference=_s(daten.get("bestellnummer")),
        contract_reference=_s(daten.get("vertragsnummer")),
        seller=Seller(
            name=lf["name"],
            address=Address(street=lf["strasse"], house_number=lf["hausnummer"], city=lf["ort"],
                            post_code=lf["plz"], country_code=lf["land"]),
            electronic_address=lf["email"], electronic_address_scheme="EM",
            contact=Contact(lf["ansprechpartner"], lf["telefon"], lf["kontakt_email"]),
            vat_id=lf["ust_id"], tax_registration_id=lf["steuernummer"],
        ),
        buyer=Buyer(
            name=md["name"],
            address=Address(street=md["strasse"], house_number=md["hausnummer"], city=md["ort"],
                            post_code=md["plz"], country_code=md["land"]),
            electronic_address=md["email"], electronic_address_scheme="EM",
            buyer_reference=referenz, vat_id=md["ust_id"],
        ),
        payment=PaymentInfo(means_code="58", iban=lf["iban"], bic=lf["bic"],
                            payment_terms=bedingung, due_date=faellig),
    )

    fall = steuerfall_lesen(daten)
    klein = fall == "kleinunternehmer"
    if klein and not _steuerfall_setzen:
        fehler("GV-STEUER", "Kleinunternehmer (§ 19 UStG): Dieser Programmstand kann Belege ohne Umsatzsteuer noch nicht "
                            "normgerecht ausstellen (der Steuerfall fehlt im Kern).", "BT-151")
    positionen = daten.get("positionen")
    if not isinstance(positionen, list):
        positionen = []
    for nr, p in enumerate(positionen, 1):
        p = p if isinstance(p, dict) else {}
        wo = f"Position {nr}"
        menge = _dezimal(p.get("menge", 1), f"{wo}, Menge")
        preis = _dezimal(p.get("einzelpreis", p.get("preis")), f"{wo}, Einzelpreis")
        satz = _dezimal(p.get("steuersatz") if p.get("steuersatz") not in (None, "") else (0 if klein else 19),
                        f"{wo}, Steuersatz")
        if preis != preis.quantize(Decimal("0.01")):
            fehler("GV-PREIS", f"{wo}: Einzelpreis mit mehr als zwei Nachkommastellen ({_zahl(preis)}) kann die E-Rechnung "
                               f"dieses Programms nicht ausweisen. Bitte auf eine größere Einheit umrechnen (z. B. je Tonne statt je kg).",
                   "BT-146")
        if klein:
            # Nichts stillschweigend ändern: ein angegebener Satz über 0 widerspricht dem Steuerfall. Weist die Gutschrift
            # Umsatzsteuer aus, schuldet der Kleinunternehmer sie (§ 14c UStG).
            if satz != 0:
                fehler("GV-STEUER", f"{wo}: Steuersatz {_zahl(satz)} %, der Lieferant ist aber als Kleinunternehmer (§ 19 UStG) "
                                    f"angegeben – dann wird keine Umsatzsteuer berechnet (Steuersatz 0).", "BT-152")
        elif satz <= 0:
            fehler("GV-STEUER", f"{wo}: Steuersatz {_zahl(satz)} % bei Regelbesteuerung. Ist der Lieferant Kleinunternehmer, "
                                f"wählen Sie bitte den Steuerfall „Kleinunternehmer (§ 19 UStG)“; andere steuerfreie Umsätze "
                                f"stellt dieses Modul nicht aus.", "BT-152")
        elif satz > 100:
            fehler("GV-STEUER", f"{wo}: Steuersatz {_zahl(satz)} % ist nicht plausibel.", "BT-152")
        if menge != menge.quantize(Decimal("0.0001")):
            fehler("GV-MENGE", f"{wo}: Menge mit mehr als vier Nachkommastellen.", "BT-129")
        linie = InvoiceLine(
            line_id=str(nr), quantity=menge, unit_code=einheit_code(p.get("einheit")),
            item_name=_s(p.get("bezeichnung", p.get("name"))),
            item_description=str(p.get("beschreibung") or "").strip(),
            item_id=_s(p.get("artikelnummer"))[:60],
            unit_price=preis, tax_category="S", tax_rate=(Decimal("0") if klein else satz),
        )
        linie.line_net_amount = linie.compute_net()
        inv.lines.append(linie)
    if klein and _steuerfall_setzen:
        # Kategorie E, Satz 0 und Befreiungsgrund (BT-120) in Beleg und Positionen – der eine Aufruf des Kerns
        _steuerfall_setzen(inv, "kleinunternehmer")

    # Eigene Prüfungen zum Verfahren
    if not lf["strasse"]:
        fehler("GV-ANSCHRIFT", "Lieferant: Straße fehlt (vollständige Anschrift des leistenden Unternehmers, § 14 Abs. 4 Nr. 1 UStG).", "BT-35")
    if not md["strasse"]:
        fehler("GV-ANSCHRIFT", "Ihre eigene Anschrift ist unvollständig (Straße fehlt) – bitte unter Einstellungen → Stammdaten ergänzen.", "BT-50")
    if not lf["ust_id"] and not lf["steuernummer"]:
        fehler("GV-STEUERNR", "Lieferant: Steuernummer oder USt-IdNr. fehlt (§ 14 Abs. 4 Nr. 2 UStG).", "BT-31/BT-32")
    if not lf["ust_id"] and lf["steuernummer"] and not _KENNUNG_IM_KERN:
        # Ab Kern 25.11.2026 schreibt Seller.kennung() die Steuernummer zusätzlich als Verkäuferkennung (BT-29); damit ist
        # BR-CO-26 der amtlichen Prüfung erfüllt. Nur ältere Programmstände können das nicht – dort bleibt es bei der Ablehnung.
        fehler("GV-KENNUNG", "Lieferant: USt-IdNr. fehlt. Die Steuernummer allein genügt zwar dem Umsatzsteuergesetz, aber nicht "
                             "der XRechnung (Regel BR-CO-26 verlangt eine USt-IdNr. oder eine andere Kennung des Verkäufers, "
                             "die dieses Programm noch nicht führt).", "BT-31")
    if lf["ust_id"] and not re.fullmatch(r"[A-Z]{2}[A-Z0-9+*]{2,14}", lf["ust_id"]):
        fehler("GV-USTID", f"Lieferant: USt-IdNr. „{lf['ust_id']}“ hat nicht die Form Länderkennzeichen + Nummer (z. B. DE123456789).", "BT-31")
    if lf["iban"] and not iban_ok(lf["iban"]):
        fehler("GV-IBAN", f"Lieferant: IBAN „{_iban_gruppiert(lf['iban'])}“ ist nicht gültig (Prüfziffer oder Länge). "
                          f"Die Zahlung ginge an ein falsches Konto.", "BT-84")
    if lf["ust_id"] and md["ust_id"] and lf["ust_id"] == md["ust_id"]:
        fehler("GV-PARTEI", "Lieferant und Aussteller haben dieselbe USt-IdNr. – im Gutschriftverfahren ist der Lieferant "
                            "der leistende Unternehmer, nicht Ihr eigenes Unternehmen.", "BT-31")
    if not md["ust_id"] and not md["steuernummer"]:
        warnung("GV-EIGEN", "In Ihren Stammdaten steht weder USt-IdNr. noch Steuernummer.", "BT-48")
    if inv.lines and inv.amount_due() <= 0:
        fehler("GV-BETRAG", "Der Gutschriftsbetrag muss größer als 0 sein.", "BT-115")
    if belegdatum > date.today() + timedelta(days=31):
        warnung("GV-DATUM", "Das Belegdatum liegt mehr als einen Monat in der Zukunft.", "BT-2")
    return inv, befunde


_EIGENE_FELDER = ("BT-44", "BT-49", "BT-50", "BT-52", "BT-53", "BT-48")


def pruefen(inv, eigene_befunde=()):
    """Eingebaute Prüfung + die Befunde des Moduls. Rückgabe (bericht, fehler, warnungen) – die Befunde des
    Moduls stehen danach auch im Bericht (und damit im Archiv)."""
    from validator import ValidationIssue, Severity
    bericht = validate_invoice(inv)
    for regel, schwere, meldung, feld in eigene_befunde:
        bericht.issues.append(ValidationIssue(regel, Severity.ERROR if schwere == "ERROR" else Severity.WARNING, meldung, feld))
    fehler, warnungen = [], []
    for i in bericht.issues:
        sev = getattr(i.severity, "value", i.severity)
        if sev not in ("ERROR", "WARNING"):
            continue
        eintrag = {"regel": i.rule_id, "meldung": i.message, "feld": i.field or ""}
        if any(f in (i.field or "") for f in _EIGENE_FELDER) and not i.rule_id.startswith("GV-"):
            eintrag["meldung"] += " (Käufer ist hier Ihr eigenes Unternehmen – bitte unter Einstellungen → Stammdaten ergänzen.)"
        (fehler if sev == "ERROR" else warnungen).append(eintrag)
    return bericht, fehler, warnungen


# ══════════════════════════════════════════════════════════════════════
# KoSIT (amtliche Prüfung) – optional
# ══════════════════════════════════════════════════════════════════════

def _kosit_pfad(ms=None):
    pfad = ((ms if ms is not None else _ms()) or {}).get("kosit_validator_path")
    if pfad:
        return pfad
    lokal = _BASIS / "tools" / "kosit"          # unabhängig vom Arbeitsverzeichnis
    return str(lokal) if lokal.is_dir() else None


def kosit_pruefen_xml(xml_bytes: bytes, ms=None) -> dict:
    """Prüft sofort (einige Sekunden). Gleiches Ergebnisformat wie webapp._kosit_pruefen (inv._kosit)."""
    leer = {"verfuegbar": False, "gueltig": None, "fehler": 0, "warnungen": 0, "details": [], "version": "",
            "grund": "", "am": datetime.now().isoformat(timespec="seconds")}
    try:
        from kosit_validator import validate_with_kosit
    except Exception as ex:                      # Modul fehlt
        return dict(leer, grund=f"KoSIT-Anbindung nicht vorhanden: {ex}")
    try:
        sperre = _G.get("_KOSIT_SPERRE") or _SPERRE
        with sperre:
            r = validate_with_kosit(xml_bytes, validator_path=_kosit_pfad(ms))

        def text(x):
            return (x if isinstance(x, str) else (x.get("message") or str(x)) if isinstance(x, dict) else str(x))[:300]
        return {"verfuegbar": bool(r.available), "gueltig": (bool(r.valid) if r.available else None),
                "fehler": int(r.error_count or 0), "warnungen": int(r.warning_count or 0),
                "details": [text(x) for x in (r.errors or [])[:10]], "version": r.validator_version or "",
                "grund": r.unavailable_reason or "", "am": datetime.now().isoformat(timespec="seconds")}
    except Exception as ex:
        return dict(leer, grund=f"Prüfung nicht möglich: {ex}")


def _kosit_hintergrund(inv, xml_bytes=None):
    """Hängt den Beleg in den Hintergrundlauf der Anwendung (webapp._kosit_pruefen); fehlt der, prüft das Modul selbst."""
    if inv._id in _KOSIT_LAEUFT:
        return

    def lauf():
        try:
            fn = _G.get("_kosit_pruefen")
            if callable(fn):
                fn(inv, xml_bytes)
            else:
                inv._kosit = kosit_pruefen_xml(xml_bytes or generate_and_serialize(inv))
                _G["auto_save"]()
        except Exception as ex:
            print(f"[gutschriftverfahren] KoSIT-Prüfung {inv.invoice_number} nicht möglich: {ex}", flush=True)
        finally:
            _KOSIT_LAEUFT.discard(inv._id)

    _KOSIT_LAEUFT.add(inv._id)
    threading.Thread(target=lauf, daemon=True, name="gv-kosit").start()


# ══════════════════════════════════════════════════════════════════════
# PDF: Briefkopf = Aussteller (Mandant), Anschriftfeld = Lieferant
# ══════════════════════════════════════════════════════════════════════

def _partei_aus_beleg(inv):
    """Lieferant und Mandant so, wie sie IM BELEG stehen (nicht aus den heutigen Stammdaten) – das PDF bleibt
    damit auch nach einer späteren Änderung der Stammdaten inhaltsgleich zur XML."""
    s, b = inv.seller, inv.buyer
    lf = {"name": s.name, "strasse": s.address.street_full(), "plz": s.address.post_code, "ort": s.address.city,
          "land": s.address.country_code, "ust_id": s.vat_id, "steuernummer": s.tax_registration_id,
          "email": s.electronic_address, "telefon": s.contact.telephone}
    md = {"name": b.name, "strasse": b.address.street_full(), "plz": b.address.post_code, "ort": b.address.city,
          "land": b.address.country_code, "ust_id": b.vat_id, "email": b.electronic_address}
    return lf, md


def _steuer_angabe(p: dict) -> str:
    teile = []
    if p.get("ust_id"):
        teile.append(f"USt-IdNr. {p['ust_id']}")
    if p.get("steuernummer"):
        teile.append(f"Steuernummer {p['steuernummer']}")
    return " · ".join(teile)


def pdf_modus(inv) -> str:
    """'gutschrift' (selbst ausgestellt), 'storno' (Stornobeleg dazu) oder 'kunde' (Lesefassung einer erhaltenen)."""
    if str(getattr(inv, "invoice_type_code", "") or "") == STORNO_BELEGART:
        return "storno"
    return "kunde" if ist_gv_kunde(inv) else "gutschrift"


def _pdf_sichtbar(inv, ms: dict, modus: str = "gutschrift") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from xml.sax.saxutils import escape as x

    lf, md = _partei_aus_beleg(inv)
    ms = ms or {}
    if not md.get("steuernummer"):
        md["steuernummer"] = _s(ms.get("tax_registration_id")) if _s(ms.get("name")) == _s(md["name"]) else ""
    md_tel = _s(ms.get("contact_phone")) if _s(ms.get("name")) == _s(md["name"]) else ""

    grau = colors.HexColor("#555555")
    dunkel = colors.HexColor("#1F2937")
    linie = colors.HexColor("#C9CED6")
    n = ParagraphStyle("n", fontName="Helvetica", fontSize=9.5, leading=12.5, textColor=dunkel)
    k = ParagraphStyle("k", parent=n, fontSize=8, leading=10.5, textColor=grau)
    f = ParagraphStyle("f", parent=n, fontName="Helvetica-Bold")
    r = ParagraphStyle("r", parent=n, alignment=TA_RIGHT)
    rf = ParagraphStyle("rf", parent=f, alignment=TA_RIGHT)
    kopf = ParagraphStyle("kopf", parent=f, fontSize=13, leading=16)
    titel = ParagraphStyle("titel", parent=f, fontSize=17, leading=21)
    th = ParagraphStyle("th", parent=f, fontSize=8.5, leading=11)
    thr = ParagraphStyle("thr", parent=th, alignment=TA_RIGHT)

    breite = 170 * mm
    story = []

    # Briefkopf: Aussteller
    kopf_rechts = "<br/>".join(x(z) for z in (md["strasse"], f"{md['plz']} {md['ort']}".strip(),
                                              ("Telefon " + md_tel) if md_tel else "", md.get("email") or "") if z)
    story.append(Table([[Paragraph(x(md["name"]), kopf), Paragraph(kopf_rechts, ParagraphStyle("kr", parent=k, alignment=TA_RIGHT))]],
                       colWidths=[100 * mm, 70 * mm],
                       style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, dunkel),
                                         ("BOTTOMPADDING", (0, 0), (-1, 0), 6), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                         ("RIGHTPADDING", (0, 0), (-1, -1), 0)])))
    story.append(Spacer(1, 11 * mm))

    # Anschriftfeld (Lieferant) + Belegdaten
    absender = " · ".join(z for z in (md["name"], md["strasse"], f"{md['plz']} {md['ort']}".strip()) if z)
    anschrift = [Paragraph(x(absender), ParagraphStyle("abs", parent=k, fontSize=6.5, leading=8)), Spacer(1, 2 * mm),
                 Paragraph(x(lf["name"]), n), Paragraph(x(lf["strasse"]), n),
                 Paragraph(x(f"{lf['plz']} {lf['ort']}".strip()), n)]
    if (lf.get("land") or "DE") != "DE":
        anschrift.append(Paragraph(x(lf["land"]), n))
    leistung = inv.tax_point_date or inv.period_end
    if inv.period_start and inv.period_end:
        leistung_text = f"{_de_datum(inv.period_start)} – {_de_datum(inv.period_end)}"
    else:
        leistung_text = _de_datum(leistung)
    storno, kunde = modus == "storno", modus == "kunde"
    vor = getattr(inv, "preceding_invoice", "") or ""
    vor_datum = _de_datum(getattr(inv, "preceding_invoice_date", None))
    daten = [("Storno-Nr." if storno else "Gutschrift-Nr.", inv.invoice_number), ("Datum", _de_datum(inv.invoice_date))]
    if storno:
        daten.append(("zur Gutschrift", vor))
    daten.append(("Leistungszeitraum" if (inv.period_start and inv.period_end) else "Leistungsdatum", leistung_text))
    if inv.payment.due_date and not storno:
        daten.append(("Zahlung bis", _de_datum(inv.payment.due_date)))
    ref = inv.buyer_reference or inv.buyer.buyer_reference
    if ref and ref not in (inv.invoice_number, vor):
        daten.append(("Zeichen" if kunde else "Unser Zeichen", ref))
    if inv.order_reference:
        daten.append(("Bestellung", inv.order_reference))
    if inv.contract_reference:
        daten.append(("Vertrag", inv.contract_reference))
    daten_tab = Table([[Paragraph(x(a), k), Paragraph(x(b), r)] for a, b in daten if b], colWidths=[28 * mm, 42 * mm],
                      style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                        ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(Table([[anschrift, daten_tab]], colWidths=[100 * mm, 70 * mm],
                       style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                         ("RIGHTPADDING", (0, 0), (-1, -1), 0)])))
    story.append(Spacer(1, 12 * mm))

    # Titel – das Wort „Gutschrift“ ist Pflichtangabe (§ 14 Abs. 4 Nr. 10 UStG)
    if storno:
        story.append(Paragraph(f"Stornierung der Gutschrift {x(vor)}", titel))
        story.append(Paragraph("Gutschriftverfahren – der Aussteller hebt seine Abrechnung auf"
                               + (f" (Gutschrift vom {vor_datum})" if vor_datum else ""), k))
    elif kunde:
        story.append(Paragraph("Gutschrift", titel))
        story.append(Paragraph(f"im Gutschriftverfahren (§ 14 Abs. 2 Satz 2 UStG) – ausgestellt von {x(md['name'])}. "
                               "Lesefassung der erhaltenen elektronischen Rechnung.", k))
    else:
        story.append(Paragraph("Gutschrift", titel))
        story.append(Paragraph("im Gutschriftverfahren – Abrechnung durch den Empfänger der Leistung "
                               "(§ 14 Abs. 2 Satz 2 UStG)", k))
    story.append(Spacer(1, 5 * mm))

    # Wer ist wer
    def partei(p):
        zeilen = [f"<b>{x(p['name'])}</b>",
                  x(", ".join(z for z in (p["strasse"], f"{p['plz']} {p['ort']}".strip()) if z))]
        st = _steuer_angabe(p)
        if st:
            zeilen.append(x(st))
        return Paragraph("<br/>".join(zeilen), n)

    story.append(Table(
        [[Paragraph("Leistender Unternehmer<br/><font size='7.5'>(Empfänger der Gutschrift)</font>", th), partei(lf)],
         [Paragraph("Leistungsempfänger<br/><font size='7.5'>(Aussteller der Gutschrift)</font>", th), partei(md)]],
        colWidths=[45 * mm, 125 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, linie),
                          ("LINEBELOW", (0, 0), (-1, 0), 0.5, linie), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F4F6")),
                          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)])))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "Wir heben unsere Abrechnung über die folgenden Lieferungen und Leistungen vollständig auf:" if storno else
        "Abgerechnet werden die folgenden Lieferungen und Leistungen des leistenden Unternehmers:" if kunde else
        "Wir rechnen die folgenden Lieferungen und Leistungen des leistenden Unternehmers ab:", n))
    story.append(Spacer(1, 3 * mm))

    # Positionen
    # Kleinunternehmer (§ 19 UStG): KEIN Steuerausweis – weder Spalte noch Zeile. Ein ausgewiesener Betrag würde vom
    # Lieferanten geschuldet (§ 14c UStG).
    ohne_ust = ist_ohne_ust(inv)
    zeilen = [[Paragraph("Pos.", th), Paragraph("Bezeichnung", th), Paragraph("Menge", thr), Paragraph("Einheit", th),
               Paragraph("Einzelpreis", thr)] + ([] if ohne_ust else [Paragraph("USt", thr)])
              + [Paragraph("Betrag EUR" if ohne_ust else "Netto EUR", thr)]]
    for nr, l in enumerate(inv.lines, 1):
        bez = x(l.item_name)
        if l.item_description:
            bez += "<br/><font size='8' color='#555555'>" + x(l.item_description).replace("\n", "<br/>") + "</font>"
        zeilen.append([Paragraph(str(nr), n), Paragraph(bez, n), Paragraph(_zahl(l.quantity), r),
                       Paragraph(x(einheit_text(l.unit_code)), n), Paragraph(_geld(l.unit_price), r)]
                      + ([] if ohne_ust else [Paragraph(_zahl(l.tax_rate) + " %", r)])
                      + [Paragraph(_geld(l.line_net_amount), r)])
    spalten = ([11 * mm, 82 * mm, 19 * mm, 17 * mm, 22 * mm, 19 * mm] if ohne_ust
               else [11 * mm, 69 * mm, 19 * mm, 17 * mm, 22 * mm, 13 * mm, 19 * mm])
    story.append(Table(zeilen, colWidths=spalten, repeatRows=1,
                       style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, dunkel),
                                         ("LINEBELOW", (0, 1), (-1, -1), 0.3, linie),
                                         ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                                         ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])))
    story.append(Spacer(1, 4 * mm))

    # Summen
    if ohne_ust:
        summen = [[Paragraph("Stornobetrag" if storno else "Gutschriftsbetrag", f),
                   Paragraph(_geld(inv.tax_inclusive_amount()) + " EUR", rf)]]
    else:
        summen = [[Paragraph("Summe netto", n), Paragraph(_geld(inv.tax_exclusive_amount()) + " EUR", r)]]
        for st in inv.compute_tax_subtotals():
            summen.append([Paragraph(f"Umsatzsteuer {_zahl(st.rate)} % auf {_geld(st.taxable_amount)} EUR", n),
                           Paragraph(_geld(st.tax_amount) + " EUR", r)])
        summen.append([Paragraph("Stornobetrag (brutto)" if storno else "Gutschriftsbetrag (brutto)", f),
                       Paragraph(_geld(inv.tax_inclusive_amount()) + " EUR", rf)])
    letzte = len(summen) - 1
    summen_tab = Table(summen, colWidths=[62 * mm, 33 * mm], hAlign="RIGHT",
                       style=TableStyle([("LINEABOVE", (0, letzte), (-1, letzte), 0.8 if letzte else 0, dunkel),
                                         ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                                         ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    schluss = [summen_tab, Spacer(1, 6 * mm)]
    if ohne_ust:
        schluss += [Paragraph(x(steuerhinweis(inv)), f), Spacer(1, 3 * mm)]

    # Zahlung an den Lieferanten
    konto = "IBAN " + _iban_gruppiert(inv.payment.iban) + (f" · BIC {inv.payment.bic}" if inv.payment.bic else "")
    if storno:
        zahl = (f"Die Gutschrift <b>{x(vor)}</b>" + (f" vom {vor_datum}" if vor_datum else "") + " verliert mit dieser "
                "Stornierung ihre Wirkung als Rechnung. Der Betrag wird nicht überwiesen; ein bereits gezahlter Betrag "
                "ist zu erstatten oder wird verrechnet.")
    elif kunde:
        zahl = (f"Der Aussteller überweist den Gutschriftsbetrag von <b>{_geld(inv.amount_due())} EUR</b>"
                + (f" auf das Konto des leistenden Unternehmers: <b>{x(konto)}</b>" if inv.payment.iban else ""))
        zahl += (f", spätestens am {_de_datum(inv.payment.due_date)}." if inv.payment.due_date else ".")
    else:
        zahl = (f"Wir überweisen den Gutschriftsbetrag von <b>{_geld(inv.amount_due())} EUR</b> auf das Konto des "
                f"leistenden Unternehmers: <b>{x(konto)}</b>")
        zahl += (f", spätestens am {_de_datum(inv.payment.due_date)}." if inv.payment.due_date else ".")
    schluss.append(Paragraph(zahl, n))
    bed = (inv.payment.payment_terms or "").strip()
    if (bed and bed not in (STANDARD_BEDINGUNG, STORNO_BEDINGUNG) and not bed.startswith("Zahlung innerhalb von")
            and not storno):
        schluss += [Spacer(1, 2 * mm), Paragraph(x(bed), n)]
    notiz = (inv.note or "")
    if notiz.startswith(HINWEIS_BT22):
        notiz = notiz[len(HINWEIS_BT22):]
    notiz = notiz.strip()
    if notiz:
        schluss += [Spacer(1, 3 * mm), Paragraph(x(notiz).replace("\n", "<br/>"), n)]
    if storno:
        fussnote = ("Maßgeblich ist die elektronische Rechnung (XRechnung, Belegart 381 mit Bezug auf die stornierte Gutschrift); "
                    "dieses Dokument gibt ihren Inhalt lesbar wieder. Über die Lieferungen und Leistungen ist neu abzurechnen, "
                    "soweit sie erbracht wurden.")
    elif kunde:
        fussnote = (f"Lesefassung, erzeugt am {_de_datum(date.today())} aus der erhaltenen elektronischen Rechnung (Belegart 389); "
                    "maßgeblich ist die erhaltene Datei. Die Gutschrift gilt als Rechnung des leistenden Unternehmers, solange "
                    "er ihr nicht widerspricht (§ 14 Abs. 2 Satz 3 UStG).")
    else:
        fussnote = ("Diese Gutschrift gilt als Rechnung des leistenden Unternehmers. Sie verliert diese Wirkung, sobald "
                    "der Empfänger der Gutschrift ihr widerspricht (§ 14 Abs. 2 Satz 3 UStG). Maßgeblich ist die "
                    "elektronische Rechnung (XRechnung, Belegart 389); dieses Dokument gibt ihren Inhalt lesbar wieder.")
    schluss += [Spacer(1, 5 * mm), Paragraph(fussnote, k)]
    story.append(KeepTogether(schluss))

    fuss = " · ".join(z for z in (md["name"], md["strasse"], f"{md['plz']} {md['ort']}".strip(), _steuer_angabe(md)) if z)

    def seite(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(grau)
        canvas.setStrokeColor(linie)
        canvas.setLineWidth(0.4)
        canvas.line(20 * mm, 17 * mm, 190 * mm, 17 * mm)
        canvas.drawString(20 * mm, 13 * mm, fuss[:150])
        canvas.drawRightString(190 * mm, 13 * mm, f"{'Storno' if storno else 'Gutschrift'} {inv.invoice_number} · Seite {doc.page}")
        canvas.restoreState()

    puffer = io.BytesIO()
    doc = SimpleDocTemplate(puffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=24 * mm, author=md["name"],
                            title=(f"Stornierung der Gutschrift {vor}" if storno else f"Gutschrift {inv.invoice_number}"),
                            subject="Gutschrift im Gutschriftverfahren (§ 14 Abs. 2 Satz 2 UStG)")
    doc.build(story, onFirstPage=seite, onLaterPages=seite)
    return puffer.getvalue()


def pdf_bauen(inv, ms=None, zugferd=True, modus=None):
    """Erzeugt das PDF des Belegs. modus: 'gutschrift' | 'storno' | 'kunde' (Vorgabe: aus dem Beleg). Die Lesefassung
    einer vom Kunden erhaltenen Gutschrift bekommt nie einen ZUGFeRD-Anhang – sie ist kein eigener Beleg.
    Rückgabe (dateiname, bytes, art) mit art =
    'zugferd'  PDF/A-3 mit eingebetteter E-Rechnung (CII, factur-x.xml),
    'pdfa-schrift'  normales PDF mit eingebetteten Schriften (ZUGFeRD-Teil nicht verfügbar),
    'einfach'  normales PDF mit Standardschrift (Schriftdateien fehlen)."""
    ms = ms if ms is not None else (_ms() if _G else {})
    name = f"{inv.invoice_number.replace('/', '_')}.pdf"
    modus = modus or pdf_modus(inv)
    if modus == "kunde":
        zugferd = False
        name = f"{inv.invoice_number.replace('/', '_')}-Lesefassung.pdf"
    try:
        from pdfa3 import schriften_eingebettet, zu_pdfa3_zugferd
        with schriften_eingebettet():
            sichtbar = _pdf_sichtbar(inv, ms, modus)
    except Exception as ex:
        print(f"[gutschriftverfahren] PDF ohne eingebettete Schriften ({ex})", flush=True)
        return name, _pdf_sichtbar(inv, ms, modus), "einfach"
    if not zugferd:
        return name, sichtbar, "pdfa-schrift"
    try:
        from cii_generator import generate_cii_bytes, ZUGFERD_PROFIL
        cii = generate_cii_bytes(inv)
        hybrid = zu_pdfa3_zugferd(sichtbar, cii, dateiname="factur-x.xml", profil=ZUGFERD_PROFIL,
                                  titel=(f"Stornierung der Gutschrift {inv.preceding_invoice}" if modus == "storno"
                                         else f"Gutschrift {inv.invoice_number}"), autor=inv.buyer.name or "")
        return name, hybrid, "zugferd"
    except Exception as ex:
        print(f"[gutschriftverfahren] PDF ohne ZUGFeRD-Anhang ({ex})", flush=True)
        return name, sichtbar, "pdfa-schrift"


# ══════════════════════════════════════════════════════════════════════
# Archiv
# ══════════════════════════════════════════════════════════════════════

def _archiv_ordner(inv):
    try:
        return Path(_G["archive"].root) / inv._id
    except Exception:
        return None


def archiv_xml(inv):
    o = _archiv_ordner(inv)
    p = (o / f"{inv.invoice_number.replace('/', '_')}.xml") if o else None
    return p.read_bytes() if p and p.exists() else None


def archiv_pdf(inv):
    o = _archiv_ordner(inv)
    p = (o / "original.pdf") if o else None
    return p.read_bytes() if p and p.exists() else None


def beleg_pdf(inv):
    """(dateiname, bytes): das archivierte PDF, sonst neu erzeugt."""
    name = f"{inv.invoice_number.replace('/', '_')}.pdf"
    alt = archiv_pdf(inv)
    if alt:          # bei einer erhaltenen Gutschrift ist das die PDF des Kunden (z. B. ZUGFeRD) – sie geht vor
        return name, alt
    n, b, _art = pdf_bauen(inv)
    return n, b


# ══════════════════════════════════════════════════════════════════════
# Ausstellen
# ══════════════════════════════════════════════════════════════════════

def _signatur(inv):
    try:
        return (str(inv.invoice_date), _s(inv.seller.name).lower(), ist_ohne_ust(inv),
                tuple((_s(l.item_name), str(l.quantity), str(l.unit_price), str(l.tax_rate)) for l in inv.lines),
                str(inv.tax_inclusive_amount()))
    except Exception:
        return None


def _zusammenfassung(inv) -> dict:
    return {"netto": float(inv.tax_exclusive_amount()), "steuer": float(inv.tax_amount()),
            "brutto": float(inv.tax_inclusive_amount()), "positionen": len(inv.lines),
            "steuerfall": "kleinunternehmer" if ist_ohne_ust(inv) else "regel", "steuerhinweis": steuerhinweis(inv),
            "lieferant": inv.seller.name, "datum": inv.invoice_date.isoformat()}


def ausstellen(daten: dict, benutzer: str = "web-user"):
    """Kern von POST /api/gutschriftverfahren. Rückgabe (antwort_dict, http_status).
    Bei Prüffehlern wird nichts angelegt und keine Nummer verbraucht; nur_pruefen legt nie etwas an."""
    daten = daten if isinstance(daten, dict) else {}
    nur_pruefen = bool(daten.get("nur_pruefen"))
    # Dieselbe Sperre wie „Rechnung erstellen“ (webapp._ERZEUGEN_SPERRE), wenn es sie gibt: Nummern ohne Wettlauf.
    with (_G.get("_ERZEUGEN_SPERRE") or _SPERRE), _SPERRE:
        ms = _ms()
        if not ist_aktiv(ms):
            return {"error": "Das Gutschriftverfahren ist abgeschaltet (Einstellungen → Stammdaten).", "abgeschaltet": True}, 404
        try:
            inv, eigene = beleg_bauen(daten, ms, naechste_nummer(advance=False))
        except Eingabefehler as ex:
            return {"error": str(ex), "angelegt": False}, 400
        vereinbart = daten.get("vereinbart") is True
        if not vereinbart:
            # Pflicht zum Ausstellen; beim bloßen Prüfen nur ein Hinweis, damit die übrige Prüfung nicht daran hängt
            eigene = list(eigene) + [("GV-VEREINBART", "WARNING" if nur_pruefen else "ERROR",
                                      "Bitte bestätigen: „Das Gutschriftverfahren ist mit dem Lieferanten vereinbart“ – ohne "
                                      "vorherige Vereinbarung gilt die Gutschrift nicht als Rechnung (§ 14 Abs. 2 Satz 2 UStG).", "")]
        bericht, fehler, warnungen = pruefen(inv, eigene)
        xml_bytes = None
        if not fehler:
            try:
                xml_bytes = generate_and_serialize(inv)
            except Exception as ex:
                fehler.append({"regel": "GV-XML", "meldung": f"Die XRechnung konnte nicht erzeugt werden: {ex}", "feld": ""})
        antwort = dict(_zusammenfassung(inv), gueltig=not fehler, fehler=fehler, warnungen=warnungen, angelegt=False)
        try:                                     # Hinweise der eingebauten Prüfung als Texte (wie „Rechnung erstellen“)
            fn = _G.get("_pruefmeldungen")
            antwort["hinweise"] = (list(fn(bericht).get("hinweise") or []) if callable(fn) else
                                   [f"[{i.rule_id}] {i.message}" for i in bericht.issues
                                    if getattr(i.severity, "value", "") == "INFO"])
        except Exception:
            antwort["hinweise"] = []

        if nur_pruefen:
            antwort.update(geprueft=True, nummer_vorschau=inv.invoice_number, kosit=None)
            if xml_bytes and daten.get("kosit"):
                antwort["kosit"] = kosit_pruefen_xml(xml_bytes, ms)
                if antwort["kosit"].get("gueltig") is False:
                    antwort["gueltig"] = False
            return antwort, 200

        if fehler:
            antwort["error"] = f"Nicht ausgestellt: {len(fehler)} Fehler. Es wurde nichts angelegt und keine Nummer vergeben."
            return antwort, 422

        # Derselbe Beleg liegt schon unbezahlt vor? (Doppelklick, zweiter Reiter)
        if not daten.get("trotzdem_anlegen"):
            sig = _signatur(inv)
            gleich = next((v for v in list(_G["invoices"].values())
                           if ist_gv(v) and getattr(v, "status", "") in ("NEU", "IN_PRUEFUNG", "IN_FREIGABE")
                           and sig is not None and _signatur(v) == sig), None)
            if gleich is not None:
                antwort.update(error=f"Derselbe Beleg liegt bereits als {gleich.invoice_number} vor (gleicher Lieferant, gleiches "
                                     f"Datum, gleiche Positionen). Es wurde kein weiterer angelegt.",
                               dublette_von=gleich.invoice_number, id=gleich._id)
                return antwort, 409

        # Ab hier wird angelegt: erst jetzt zählt der Nummernkreis.
        try:
            dateiname, pdf_bytes, pdf_art = pdf_bauen(inv, ms)
        except Exception as ex:
            antwort["error"] = f"Nicht ausgestellt: Das PDF konnte nicht erzeugt werden ({ex}). Es wurde nichts angelegt."
            return antwort, 500
        vorschau = inv.invoice_number
        nummer = naechste_nummer(advance=True)
        if nummer != vorschau:                   # kann nur bei fremden Schreibern vorkommen – dann alles mit der echten Nummer
            inv.invoice_number = nummer
            if inv.buyer_reference == vorschau:
                inv.buyer_reference = inv.buyer.buyer_reference = nummer
            xml_bytes = generate_and_serialize(inv)
            dateiname, pdf_bytes, pdf_art = pdf_bauen(inv, ms)

        inv._direction = "EINGANG"               # Ausgabe des Mandanten, die er bezahlt
        _G["invoices"][inv._id] = inv
        try:
            wf = _G.get("wf_engine")
            if wf is not None:
                wf.start_workflow(inv, benutzer)     # Status wie eine neue Eingangsrechnung (IN_PRUEFUNG)
        except Exception as ex:
            print(f"[gutschriftverfahren] Freigabelauf nicht gestartet: {ex}", flush=True)
        inv.add_audit(EREIGNIS, benutzer,
                      f"Gutschrift im Gutschriftverfahren (Belegart 389) an {inv.seller.name}, "
                      f"{_geld(inv.tax_inclusive_amount())} EUR"
                      + (" ohne Umsatzsteuer (Kleinunternehmer § 19 UStG)" if ist_ohne_ust(inv) else " brutto")
                      + f". Bestätigt von {benutzer}: Das Gutschriftverfahren ist mit dem Lieferanten vereinbart.")
        for name, aufruf in (("inbox", lambda o: o.duplicates.register(inv, inv._id)),
                             ("dash", lambda o: o.add_invoice(inv))):
            try:
                if _G.get(name) is not None:
                    aufruf(_G[name])
            except Exception as ex:
                print(f"[gutschriftverfahren] {name}: {ex}", flush=True)
        try:
            _G["archive"].archive_invoice(inv, xml_bytes, bericht, direction="EINGANG", pdf_bytes=pdf_bytes)
        except Exception as ex:
            print(f"[gutschriftverfahren] Archiv {inv.invoice_number}: {ex}", flush=True)
        _G["auto_save"]()
        _kosit_hintergrund(inv, xml_bytes)

        antwort.update(angelegt=True, id=inv._id, nummer=inv.invoice_number, status=inv.status, pdf_art=pdf_art,
                       xml_url=f"/api/gutschriftverfahren/{inv._id}/xml", pdf_url=f"/api/gutschriftverfahren/{inv._id}/pdf",
                       kosit={"ausstehend": True})
        return antwort, 201


# ══════════════════════════════════════════════════════════════════════
# 1.2 – Storno einer selbst ausgestellten Gutschrift
# ══════════════════════════════════════════════════════════════════════
# Belegart 381 (UBL CreditNote) mit Bezug BT-25/BT-26 auf die Gutschrift, Rollen wie im Original (Verkäufer = Lieferant,
# Käufer = Mandant). Die EN 16931 kennt zwar 261 „Self billed credit note“; die XRechnung sieht den Code aber nicht vor
# (BR-DE-17, amtliche Prüfung: gültig mit Warnung), und das Programm rechnet Stornos überall über 381 + Bezug ab.

def storno_nummer(inv) -> str:
    return f"ST-{inv.invoice_number}"            # Muster des Programms: Storno = ST-<Nummer>


def finde_storno(inv):
    nr = (inv.invoice_number or "").upper()
    for v in list(_G["invoices"].values()):
        if ist_gv_storno(v) and (getattr(v, "preceding_invoice", "") or "").upper() == nr:
            return v
    return None


def ist_bezahlt(inv) -> bool:
    return (getattr(inv, "status", "") == "BEZAHLT" or bool(getattr(inv, "_paid_at", None))
            or bool(getattr(inv, "_teilzahlungen", None)))


def storno_bauen(inv, grund: str):
    import copy
    st = Invoice(
        invoice_number=storno_nummer(inv), invoice_date=date.today(), invoice_type_code=STORNO_BELEGART,
        currency_code=inv.currency_code, buyer_reference=inv.buyer_reference or inv.buyer.buyer_reference,
        tax_point_date=inv.tax_point_date, period_start=inv.period_start, period_end=inv.period_end,
        preceding_invoice=inv.invoice_number, preceding_invoice_date=inv.invoice_date,      # BT-25 / BT-26
        note=(f"Stornierung der Gutschrift im Gutschriftverfahren {inv.invoice_number} vom {_de_datum(inv.invoice_date)}. "
              f"Grund: {grund}"),
        order_reference=inv.order_reference, contract_reference=inv.contract_reference,
        seller=copy.deepcopy(inv.seller), buyer=copy.deepcopy(inv.buyer), payment=copy.deepcopy(inv.payment),
    )
    st.payment.due_date = None
    st.payment.payment_terms = STORNO_BEDINGUNG
    st.payment.skonto_percent, st.payment.skonto_days = 0.0, 0
    st.lines = [copy.deepcopy(l) for l in inv.lines]
    st.allowances_charges = [copy.deepcopy(a) for a in inv.allowances_charges]
    for feld in ("steuerfall", "steuerbefreiung_grund", "steuerbefreiung_code"):      # Kleinunternehmer bleibt Kleinunternehmer
        if hasattr(inv, feld):
            setattr(st, feld, getattr(inv, feld))
    return st


def stornieren(inv_id: str, daten: dict, benutzer: str = "web-user"):
    """Kern von POST /api/gutschriftverfahren/<id>/storno. Rückgabe (antwort, http_status)."""
    daten = daten if isinstance(daten, dict) else {}
    with (_G.get("_ERZEUGEN_SPERRE") or _SPERRE), _SPERRE:
        inv = _G["invoices"].get(inv_id)
        if inv is None or not ist_gv(inv):
            return {"error": "Hier lassen sich nur selbst ausgestellte Gutschriften im Gutschriftverfahren stornieren."}, 404
        nr = inv.invoice_number
        schon = finde_storno(inv)
        if inv.status == "STORNIERT" or _hat(inv, EREIGNIS_STORNIERT) or schon is not None:
            return {"error": f"Die Gutschrift {nr} ist bereits storniert" + (f" (Stornobeleg {schon.invoice_number})." if schon else "."),
                    "storniert": False, "storno_id": schon._id if schon else None,
                    "storno_nummer": schon.invoice_number if schon else None}, 409
        nummer = storno_nummer(inv)
        if any(getattr(v, "invoice_number", "") == nummer for v in list(_G["invoices"].values())):
            return {"error": f"Die Nummer {nummer} ist bereits vergeben.", "storniert": False}, 409
        grund = _s(daten.get("grund"))
        if len(grund) < 3:
            return {"error": "Bitte den Grund der Stornierung angeben.", "storniert": False}, 400
        bezahlt = ist_bezahlt(inv)
        if bezahlt and daten.get("trotz_zahlung") is not True:
            return {"error": f"Die Gutschrift {nr} ist bereits als bezahlt geführt. Mit dem Storno heben Sie die Abrechnung auf – "
                             f"den gezahlten Betrag ({_geld(inv.tax_inclusive_amount())} EUR) müssen Sie dann vom Lieferanten "
                             f"zurückfordern oder verrechnen. Bitte bestätigen Sie das ausdrücklich.",
                    "storniert": False, "bezahlt": True, "bestaetigung_noetig": "trotz_zahlung"}, 409

        st = storno_bauen(inv, grund)
        bericht, fehler, warnungen = pruefen(st)
        if fehler:
            return {"error": f"Nicht storniert: Die Prüfung des Stornobelegs meldet {len(fehler)} Fehler. Es wurde nichts angelegt.",
                    "storniert": False, "fehler": fehler, "warnungen": warnungen}, 422
        try:
            xml_bytes = generate_and_serialize(st)
            _name, pdf_bytes, pdf_art = pdf_bauen(st, _ms(), modus="storno")
        except Exception as ex:
            return {"error": f"Nicht storniert: Der Stornobeleg konnte nicht erzeugt werden ({ex}). Es wurde nichts angelegt.",
                    "storniert": False}, 500

        zusatz = " Die Gutschrift war als bezahlt geführt – der Betrag ist zurückzufordern oder zu verrechnen." if bezahlt else ""
        st._direction = "EINGANG"
        st.status = "FREIGEGEBEN"
        st.add_audit(EREIGNIS_STORNO, benutzer,
                     f"Stornierung der Gutschrift {nr} im Gutschriftverfahren ({_geld(st.tax_inclusive_amount())} EUR). "
                     f"Grund: {grund}.{zusatz}")
        _G["invoices"][st._id] = st
        alt = inv.status
        inv.status = "STORNIERT"
        inv.add_audit(EREIGNIS_STORNIERT, benutzer, f"Storniert durch {nummer}. Grund: {grund}.{zusatz}",
                      old_value=alt, new_value="STORNIERT")
        for name, aufruf in (("inbox", lambda o: o.duplicates.register(st, st._id)), ("dash", lambda o: o.add_invoice(st))):
            try:
                if _G.get(name) is not None:
                    aufruf(_G[name])
            except Exception as ex:
                print(f"[gutschriftverfahren] {name}: {ex}", flush=True)
        try:
            _G["archive"].archive_invoice(st, xml_bytes, bericht, direction="EINGANG", pdf_bytes=pdf_bytes)
        except Exception as ex:
            print(f"[gutschriftverfahren] Archiv {nummer}: {ex}", flush=True)
        _G["auto_save"]()
        _kosit_hintergrund(st, xml_bytes)
        return {"storniert": True, "id": st._id, "nummer": nummer, "original": nr, "original_id": inv._id, "bezahlt": bezahlt,
                "brutto": float(st.tax_inclusive_amount()), "pdf_art": pdf_art, "warnungen": warnungen,
                "xml_url": f"/api/gutschriftverfahren/{st._id}/xml", "pdf_url": f"/api/gutschriftverfahren/{st._id}/pdf",
                "hinweis": "Bitte übermitteln Sie den Stornobeleg dem Lieferanten." + zusatz, "kosit": {"ausstehend": True}}, 201


# ══════════════════════════════════════════════════════════════════════
# 1.2 – Versand an den Lieferanten (Gutschrift und Stornobeleg)
# ══════════════════════════════════════════════════════════════════════

_MAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def versand_empfaenger(inv) -> str:
    for adresse in (getattr(inv.seller.contact, "email", ""), inv.seller.electronic_address):
        if adresse and _MAIL.match(adresse.strip()):
            return adresse.strip()
    return ""


def versand_text(inv):
    """(Betreff, Text) für die Mail an den Lieferanten."""
    aussteller = _s(inv.buyer.name)
    betrag = _geld(inv.tax_inclusive_amount())
    ohne = " (ohne Umsatzsteuer, § 19 UStG)" if ist_ohne_ust(inv) else ""
    if ist_gv_storno(inv):
        vor = inv.preceding_invoice
        vor_datum = _de_datum(getattr(inv, "preceding_invoice_date", None))
        grund = (inv.note or "").split("Grund:", 1)[1].strip() if "Grund:" in (inv.note or "") else ""
        betreff = f"Stornierung der Gutschrift Nr. {vor} im Gutschriftverfahren"
        text = (f"Sehr geehrte Damen und Herren,\n\n"
                f"hiermit stornieren wir unsere Gutschrift Nr. {vor}" + (f" vom {vor_datum}" if vor_datum else "")
                + f" im Gutschriftverfahren (§ 14 Abs. 2 Satz 2 UStG) über {betrag} EUR{ohne}.\n"
                + (f"Grund: {grund}\n" if grund else "")
                + f"\nDie Gutschrift verliert damit ihre Wirkung als Rechnung; bitte verwenden Sie sie nicht mehr. "
                  f"Der Betrag wird nicht überwiesen. Haben Sie ihn bereits erhalten, erstatten Sie ihn bitte oder wir "
                  f"verrechnen ihn mit der nächsten Abrechnung.\n\n"
                  f"Im Anhang finden Sie den Stornobeleg Nr. {inv.invoice_number} als elektronische Rechnung "
                  f"({inv.invoice_number}.xml) und in lesbarer Fassung als PDF.\n\n"
                  f"Mit freundlichen Grüßen\n{aussteller}")
        return betreff, text
    zeitraum = ""
    if inv.period_start and inv.period_end:
        zeitraum = f" im Zeitraum {_de_datum(inv.period_start)} bis {_de_datum(inv.period_end)}"
    elif inv.tax_point_date or inv.period_end:
        zeitraum = f" vom {_de_datum(inv.tax_point_date or inv.period_end)}"
    konto = _iban_gruppiert(inv.payment.iban)
    bis = f" bis zum {_de_datum(inv.payment.due_date)}" if inv.payment.due_date else ""
    betreff = f"Gutschrift im Gutschriftverfahren Nr. {inv.invoice_number} – Abrechnung über Ihre Leistung"
    text = (f"Sehr geehrte Damen und Herren,\n\n"
            f"anbei erhalten Sie unsere Gutschrift Nr. {inv.invoice_number} vom {_de_datum(inv.invoice_date)} im "
            f"Gutschriftverfahren (§ 14 Abs. 2 Satz 2 UStG). Wir rechnen darin über Ihre Lieferungen und Leistungen{zeitraum} ab.\n\n"
            f"Gutschriftsbetrag: {betrag} EUR{ohne}\n"
            f"Zahlung: per Überweisung auf Ihr Konto {konto}{bis}.\n\n"
            f"Die Gutschrift gilt als Ihre Rechnung. Bitte prüfen Sie die Angaben. Sind Sie mit der Abrechnung nicht "
            f"einverstanden, widersprechen Sie bitte umgehend – mit Ihrem Widerspruch verliert die Gutschrift ihre "
            f"Wirkung als Rechnung.\n\n"
            f"Im Anhang finden Sie die elektronische Rechnung ({inv.invoice_number}.xml) und eine lesbare Fassung als PDF.\n\n"
            f"Mit freundlichen Grüßen\n{aussteller}")
    return betreff, text


def versand_sperre(inv):
    """Warum dieser Beleg nicht versendet wird – oder None."""
    a = art(inv)
    if a == "kunde":
        return "Diese Gutschrift hat Ihr Kunde ausgestellt – sie wird nicht versendet."
    if a not in ("eigen", "storno"):
        return "Dieser Beleg gehört nicht zum Gutschriftverfahren."
    k = getattr(inv, "_kosit", None) or {}
    if k.get("gueltig") is False:
        return (f"Nicht versendet: Die amtliche Prüfung (KoSIT) meldet {k.get('fehler', 0)} Fehler in diesem Beleg. "
                f"Der Empfänger könnte ihn zurückweisen.")
    if a == "eigen" and inv.status == "STORNIERT":
        st = finde_storno(inv)
        return (f"Die Gutschrift {inv.invoice_number} ist storniert – versenden Sie bitte den Stornobeleg"
                + (f" {st.invoice_number}." if st else "."))
    if a == "eigen" and inv.status == "ZURUECKGEWIESEN":
        return f"Die Gutschrift {inv.invoice_number} ist zurückgewiesen und wird nicht versendet."
    return None


def versand_verlauf(inv) -> list:
    return [{"am": e.timestamp, "von": e.user, "text": e.comment}
            for e in (inv.audit_trail or []) if e.event_type == EREIGNIS_VERSENDET]


def versand_vorschau(inv) -> dict:
    betreff, text = versand_text(inv)
    sender = _webapp("email_sender")
    cfg = getattr(sender, "config", None)
    nr = inv.invoice_number.replace("/", "_")
    return {"id": inv._id, "nummer": inv.invoice_number, "art": art(inv), "empfaenger": versand_empfaenger(inv),
            "betreff": betreff, "text": text, "anhaenge": [f"{nr}.xml", f"{nr}.pdf"],
            "absender": getattr(cfg, "smtp_from_address", "") or "", "gesperrt": versand_sperre(inv),
            "testmodus": type(sender).__name__.startswith("Mock") if sender is not None else False,
            "versand_moeglich": sender is not None, "bisher": versand_verlauf(inv),
            "pdf_url": f"/api/gutschriftverfahren/{inv._id}/pdf", "kosit": getattr(inv, "_kosit", None) or {"ausstehend": True}}


def versenden(inv_id: str, daten: dict, benutzer: str = "web-user"):
    """Kern von POST /api/gutschriftverfahren/<id>/senden. Nutzt den Postausgang des Programms (email_sender) und sein
    Versandprotokoll (_log_sent_mail). Rückgabe (antwort, http_status)."""
    daten = daten if isinstance(daten, dict) else {}
    inv = _G["invoices"].get(inv_id)
    if inv is None or art(inv) not in ("eigen", "storno", "kunde"):
        return {"error": "Beleg nicht gefunden"}, 404
    sperre = versand_sperre(inv)
    if sperre:
        return {"error": sperre, "gesendet": False, "kosit": getattr(inv, "_kosit", None)}, 409
    vor = versand_vorschau(inv)
    empfaenger = _s(daten.get("empfaenger")) or vor["empfaenger"]
    if not _MAIL.match(empfaenger or ""):
        return {"error": "Bitte eine gültige E-Mail-Adresse des Lieferanten angeben.", "gesendet": False}, 400
    betreff = _s(daten.get("betreff")) or vor["betreff"]
    text = str(daten.get("text") or "").strip() or vor["text"]
    sender = _webapp("email_sender")
    if sender is None:
        return {"error": "Der Postausgang des Programms steht nicht bereit.", "gesendet": False}, 503
    try:
        xml_bytes = archiv_xml(inv) or generate_and_serialize(inv)
        pdf_name, pdf_bytes = beleg_pdf(inv)
    except Exception as ex:
        return {"error": f"Versand abgebrochen: Die Anhänge konnten nicht erzeugt werden ({ex}).", "gesendet": False}, 500
    protokoll = sender.send_invoice(invoice=inv, recipient=empfaenger, subject=betreff, body_text=text,
                                    xml_bytes=xml_bytes, additional_attachments=[(pdf_name, pdf_bytes)])
    testmodus = type(sender).__name__.startswith("Mock")
    anhaenge = [getattr(protokoll, "attachment_filename", "") or vor["anhaenge"][0], pdf_name]
    if not getattr(protokoll, "success", False):
        return {"error": f"Die E-Mail wurde nicht versendet: {getattr(protokoll, 'error', '') or 'unbekannter Fehler'}",
                "gesendet": False}, 502
    was = "Stornobeleg" if ist_gv_storno(inv) else "Gutschrift"
    inv.add_audit(EREIGNIS_VERSENDET, benutzer,
                  f"{was} an {empfaenger} gesendet (XML + PDF), Message-ID: {getattr(protokoll, 'message_id', '')}"
                  + (" – Testbetrieb: kein Postausgang eingerichtet, die Nachricht wurde nur abgelegt" if testmodus else ""))
    try:
        log = _G.get("_log_sent_mail")
        if callable(log):
            log(empfaenger, getattr(protokoll, "subject", "") or betreff, text,
                doc_type="gutschriftverfahren_storno" if ist_gv_storno(inv) else "gutschriftverfahren",
                reference=inv.invoice_number, attachments=anhaenge,
                from_addr=getattr(getattr(sender, "config", None), "smtp_from_address", ""),
                message_id=getattr(protokoll, "message_id", ""))
    except Exception as ex:
        print(f"[gutschriftverfahren] Versandprotokoll: {ex}", flush=True)
    _G["auto_save"]()
    return {"gesendet": True, "testmodus": testmodus, "empfaenger": empfaenger, "betreff": betreff, "anhaenge": anhaenge,
            "message_id": getattr(protokoll, "message_id", ""), "nummer": inv.invoice_number}, 200


# ══════════════════════════════════════════════════════════════════════
# 1.2 – Vom Kunden ausgestellte Gutschrift (eingehende 389, Verkäufer = wir)
# ══════════════════════════════════════════════════════════════════════
# Rechnet ein Kunde im Gutschriftverfahren über UNSERE Leistung ab, ist sein Beleg für uns ein Erlös (Forderung), keine
# Eingangsrechnung. Erkannt wird das an „Belegart 389 UND Verkäufer = Mandant“. Nur bei sicherer Übereinstimmung wird
# umgestuft; sonst bleibt der Beleg, wie er ist, und bekommt einen deutlichen Hinweis.

_GESEHEN: set = set()
_RECHTSFORM = re.compile(r"\b(gmbh|mbh|ag|kg|ohg|gbr|ug|ek|ev|eg|se|kgaa|co|partg|mbb|haftungsbeschraenkt|haftungsbeschränkt|"
                         r"und|inhaber|inh)\b")


def _norm_ustid(t) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(t or "").upper())


def _norm_steuernr(t) -> str:
    return re.sub(r"\D", "", str(t or ""))


def _norm_name(t) -> str:
    t = str(t or "").casefold().replace("&", " und ").replace("e. k.", "ek").replace("e.k.", "ek").replace("e. v.", "ev").replace("e.v.", "ev")
    return " ".join(re.sub(r"[^a-z0-9äöüß ]", " ", t).split())


def _kern_name(t) -> str:
    return " ".join(_RECHTSFORM.sub(" ", _norm_name(t)).split())


def eigene_firma(partei, ms: dict):
    """Ist diese Partei eines Belegs der Mandant? Rückgabe (urteil, woran): 'sicher' | 'unsicher' | 'nein'.
    Reihenfolge: USt-IdNr., sonst Steuernummer, sonst Name. Widerspricht eine Kennung dem gleichen Namen → unsicher."""
    ms = ms or {}
    name_m, name_p = _norm_name(ms.get("name")), _norm_name(getattr(partei, "name", ""))
    name_gleich = bool(name_m) and name_m == name_p
    kern_m, kern_p = _kern_name(ms.get("name")), _kern_name(getattr(partei, "name", ""))
    name_aehnlich = bool(kern_m) and bool(kern_p) and (kern_m == kern_p or (len(kern_m) >= 5 and len(kern_p) >= 5
                                                                           and (kern_m in kern_p or kern_p in kern_m)))
    ust_m, ust_p = _norm_ustid(ms.get("vat_id")), _norm_ustid(getattr(partei, "vat_id", ""))
    if ust_m and ust_p:
        if ust_m == ust_p:
            return "sicher", "USt-IdNr."
        return ("unsicher", "gleicher Name, aber eine andere USt-IdNr.") if (name_gleich or name_aehnlich) else ("nein", "")
    st_m, st_p = _norm_steuernr(ms.get("tax_registration_id")), _norm_steuernr(getattr(partei, "tax_registration_id", ""))
    if st_m and st_p:
        if st_m == st_p:
            return "sicher", "Steuernummer"
        return ("unsicher", "gleicher Name, aber eine andere Steuernummer") if (name_gleich or name_aehnlich) else ("nein", "")
    if name_gleich:
        return "sicher", "Name"
    if name_aehnlich:
        return "unsicher", "ähnlicher Name, keine USt-IdNr. oder Steuernummer zum Vergleich"
    return "nein", ""


def _archiv_richtung(inv, richtung: str):
    """Zieht die Richtung im Archiv nach (Index und metadata.json), damit Archiv und Bestand übereinstimmen."""
    try:
        archiv = _G["archive"]
        geaendert = False
        for e in getattr(archiv, "_index", []):
            if e.get("invoice_id") == inv._id:
                e["direction"] = richtung
                if isinstance(e.get("invoice_metadata"), dict):
                    e["invoice_metadata"]["direction"] = richtung
                geaendert = True
        if geaendert and hasattr(archiv, "_save_index"):
            archiv._save_index()
        meta = Path(archiv.root) / inv._id / "metadata.json"
        if meta.exists():
            d = json.loads(meta.read_text("utf-8"))
            d["direction"] = richtung
            meta.write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
    except Exception as ex:
        print(f"[gutschriftverfahren] Archiv-Richtung {getattr(inv, 'invoice_number', '?')}: {ex}", flush=True)


def _als_kunde_fuehren(inv, woran: str, benutzer: str = "system"):
    alt = f"{inv._direction or 'AUSGANG'}/{inv.status}"
    inv._direction = "AUSGANG"                   # Forderung: Gegenpartei ist der Käufer (unser Kunde)
    inv.status = "NEU"                           # zu prüfen: anerkennen oder widersprechen – kein Freigabelauf wie bei Kosten
    inv.assigned_to = ""
    inv.add_audit(EREIGNIS_KUNDE, benutzer,
                  f"Gutschrift im Gutschriftverfahren, ausgestellt vom Kunden {_s(inv.buyer.name)} über unsere Leistung "
                  f"(Verkäufer = eigenes Unternehmen, erkannt an: {woran}). Wird als Ausgang (Forderung) geführt, die Nummer "
                  f"des Kunden bleibt. Bitte prüfen und anerkennen – oder widersprechen.",
                  old_value=alt, new_value="AUSGANG/NEU")
    _archiv_richtung(inv, "AUSGANG")


def eingang_einstufen(inv, ms=None, benutzer: str = "system") -> str:
    """Prüft EINEN Beleg. Rückgabe 'kunde' (umgestuft), 'unsicher' (nur Hinweis) oder '' (nichts zu tun)."""
    if str(getattr(inv, "invoice_type_code", "") or "") != BELEGART or (getattr(inv, "_direction", "") or "") != "EINGANG":
        return ""
    if _hat(inv, EREIGNIS, EREIGNIS_KUNDE, EREIGNIS_UNSICHER, EREIGNIS_BLEIBT):
        return ""                                # eigener Beleg oder schon entschieden
    ms = ms if ms is not None else _ms()
    if not _abschnitt(ms).get("eingang_erkennen", True):
        return ""
    urteil, woran = eigene_firma(inv.seller, ms)
    if urteil == "nein":
        return ""
    bearbeitet = inv.status not in ("NEU", "IN_PRUEFUNG", "IN_FREIGABE") or ist_bezahlt(inv)
    if urteil == "sicher" and not bearbeitet:
        _als_kunde_fuehren(inv, woran, benutzer)
        return "kunde"
    grund = (f"der Beleg ist schon bearbeitet (Status {inv.status})" if urteil == "sicher" else woran)
    inv.add_audit(EREIGNIS_UNSICHER, benutzer,
                  f"Achtung: Belegart 389 (Gutschriftverfahren), und der Verkäufer „{_s(inv.seller.name)}“ könnte Ihr eigenes "
                  f"Unternehmen sein ({grund}). Dann wäre das keine Eingangsrechnung, sondern eine Gutschrift Ihres Kunden über "
                  f"Ihre Leistung (Erlös). Der Beleg wurde NICHT umgestuft – bitte entscheiden Sie von Hand.")
    return "unsicher"


def eingaenge_durchsehen() -> int:
    """Sieht alle noch nicht betrachteten Belege an (jeder Einleseweg landet im Bestand). Rückgabe: Zahl der Änderungen."""
    geaendert = 0
    try:
        offen = [(i, v) for i, v in list(_G["invoices"].items()) if i not in _GESEHEN]
        if not offen:
            return 0
        ms = None
        with _SPERRE:
            for i, v in offen:
                _GESEHEN.add(i)
                if str(getattr(v, "invoice_type_code", "") or "") != BELEGART:
                    continue
                if ms is None:
                    ms = _ms()
                if eingang_einstufen(v, ms):
                    geaendert += 1
        if geaendert:
            _G["auto_save"]()
    except Exception as ex:
        print(f"[gutschriftverfahren] Eingänge durchsehen: {ex}", flush=True)
    return geaendert


def von_hand_einstufen(inv_id: str, daten: dict, benutzer: str = "web-user"):
    daten = daten if isinstance(daten, dict) else {}
    inv = _G["invoices"].get(inv_id)
    if inv is None or art(inv) != "unsicher":
        return {"error": "Zu diesem Beleg ist nichts zu entscheiden."}, 404
    als = _s(daten.get("als")).lower()
    if als == "kunde":
        if ist_bezahlt(inv):
            return {"error": "Der Beleg ist schon als bezahlt geführt. Bitte zuerst die Zahlung lösen, dann umstufen."}, 409
        _als_kunde_fuehren(inv, "von Hand bestätigt", benutzer)
        _G["auto_save"]()
        return {"art": "kunde", "status": inv.status, "richtung": inv._direction}, 200
    if als == "eingang":
        inv.add_audit(EREIGNIS_BLEIBT, benutzer, "Von Hand entschieden: Der Beleg ist eine Eingangsrechnung eines Lieferanten.")
        _G["auto_save"]()
        return {"art": "", "status": inv.status, "richtung": inv._direction}, 200
    return {"error": "Feld „als“ erwartet: „kunde“ oder „eingang“."}, 400


def anerkennen(inv_id: str, benutzer: str = "web-user"):
    inv = _G["invoices"].get(inv_id)
    if inv is None or not ist_gv_kunde(inv):
        return {"error": "Beleg nicht gefunden"}, 404
    if _hat(inv, EREIGNIS_WIDERSPROCHEN) or inv.status == "ZURUECKGEWIESEN":
        return {"error": "Dieser Gutschrift wurde bereits widersprochen."}, 409
    if _hat(inv, EREIGNIS_ANERKANNT):
        return {"error": "Die Gutschrift ist bereits anerkannt.", "status": inv.status}, 409
    alt = inv.status
    if inv.status in ("NEU", "IN_PRUEFUNG", "IN_FREIGABE"):
        inv.status = "FREIGEGEBEN"
    inv.add_audit(EREIGNIS_ANERKANNT, benutzer,
                  f"Gutschrift des Kunden {_s(inv.buyer.name)} geprüft und anerkannt ({_geld(inv.tax_inclusive_amount())} EUR).",
                  old_value=alt, new_value=inv.status)
    _G["auto_save"]()
    return {"anerkannt": True, "status": inv.status}, 200


def widersprechen(inv_id: str, daten: dict, benutzer: str = "web-user"):
    daten = daten if isinstance(daten, dict) else {}
    inv = _G["invoices"].get(inv_id)
    if inv is None or not ist_gv_kunde(inv):
        return {"error": "Beleg nicht gefunden"}, 404
    if _hat(inv, EREIGNIS_WIDERSPROCHEN):
        return {"error": "Dieser Gutschrift wurde bereits widersprochen."}, 409
    if ist_bezahlt(inv):
        return {"error": "Zu dieser Gutschrift ist schon eine Zahlung verbucht. Bitte klären Sie das zuerst mit dem Kunden "
                         "(Zahlung lösen oder zurückzahlen); der Widerspruch wird sonst nicht vermerkt.", "bezahlt": True}, 409
    grund = _s(daten.get("grund"))
    if len(grund) < 3:
        return {"error": "Bitte den Grund des Widerspruchs angeben."}, 400
    alt = inv.status
    inv.status = "ZURUECKGEWIESEN"
    inv.add_audit(EREIGNIS_WIDERSPROCHEN, benutzer,
                  f"Widerspruch gegen die Gutschrift des Kunden {_s(inv.buyer.name)} vermerkt. Grund: {grund}. Die Gutschrift "
                  f"verliert damit die Wirkung einer Rechnung (§ 14 Abs. 2 Satz 3 UStG). Bitte teilen Sie dem Kunden den "
                  f"Widerspruch mit und rechnen Sie die Leistung selbst ab.", old_value=alt, new_value="ZURUECKGEWIESEN")
    _G["auto_save"]()
    return {"widersprochen": True, "status": inv.status,
            "hinweis": "Der Widerspruch ist vermerkt. Das Programm benachrichtigt den Kunden nicht – bitte teilen Sie ihm den "
                       "Widerspruch mit und stellen Sie über die Leistung eine eigene Rechnung."}, 200


def stand(inv) -> dict:
    """Was die Rechnungsansicht über einen Beleg wissen muss (auch für Belege, die nicht zum Modul gehören: art = '')."""
    a = art(inv)
    aus = {"id": getattr(inv, "_id", ""), "art": a, "aktionen": {}}
    if not a:
        return aus
    aus.update(nummer=inv.invoice_number, status=inv.status, richtung=inv._direction or "AUSGANG", bezahlt=ist_bezahlt(inv),
               brutto=float(inv.tax_inclusive_amount()), kosit=getattr(inv, "_kosit", None) or {"ausstehend": True})
    if a != "unsicher":
        aus.update(pdf_url=f"/api/gutschriftverfahren/{inv._id}/pdf", xml_url=f"/api/gutschriftverfahren/{inv._id}/xml")
    if a == "eigen":
        st = finde_storno(inv)
        aus.update(schild="Gutschriftverfahren", partner=inv.seller.name, versendet=versand_verlauf(inv),
                   storno_id=st._id if st else None, storno_nummer=st.invoice_number if st else None,
                   hinweis=(f"Storniert durch {st.invoice_number}." if st else
                            "Selbst ausgestellte Gutschrift im Gutschriftverfahren – Sie zahlen an den Lieferanten."))
        aus["aktionen"] = {"storno": st is None and inv.status != "STORNIERT", "senden": versand_sperre(inv) is None}
    elif a == "storno":
        orig = next((v for v in list(_G["invoices"].values())
                     if ist_gv(v) and (v.invoice_number or "").upper() == (inv.preceding_invoice or "").upper()), None)
        aus.update(schild="Storno im Gutschriftverfahren", partner=inv.seller.name, versendet=versand_verlauf(inv),
                   original_id=orig._id if orig else None, original_nummer=inv.preceding_invoice,
                   hinweis=f"Stornobeleg zur Gutschrift {inv.preceding_invoice}. Bitte dem Lieferanten übermitteln.")
        aus["aktionen"] = {"senden": versand_sperre(inv) is None}
    elif a == "kunde":
        wider, anerk = _hat(inv, EREIGNIS_WIDERSPROCHEN), _hat(inv, EREIGNIS_ANERKANNT)
        aus.update(schild="Gutschriftverfahren (vom Kunden ausgestellt)", partner=inv.buyer.name,
                   anerkannt=anerk, widersprochen=wider,
                   hinweis=("Sie haben dieser Gutschrift widersprochen – sie gilt nicht mehr als Rechnung." if wider else
                            f"Diese Gutschrift hat Ihr Kunde {_s(inv.buyer.name)} über Ihre Leistung ausgestellt. Sie gilt als "
                            f"Ihre Rechnung an ihn (Forderung), solange Sie nicht widersprechen."
                            + ("" if anerk else " Bitte prüfen und anerkennen – oder widersprechen.")))
        aus["aktionen"] = {"anerkennen": not anerk and not wider and inv.status not in ("STORNIERT",),
                           "widersprechen": not wider and not ist_bezahlt(inv) and inv.status not in ("STORNIERT",)}
    elif a == "unsicher":
        e = next((x for x in reversed(inv.audit_trail or []) if x.event_type == EREIGNIS_UNSICHER), None)
        aus.update(schild="Gutschriftverfahren – bitte entscheiden", partner=inv.seller.name, hinweis=e.comment if e else "")
        aus["aktionen"] = {"einstufen": True}
    return aus


# ══════════════════════════════════════════════════════════════════════
# Einhängen in die Flask-Anwendung
# ══════════════════════════════════════════════════════════════════════

def einrichten(app, g: dict):
    """Registriert die Endpunkte. `g` ist globals() von webapp.py."""
    from flask import request, Response

    import os
    if os.environ.get("GUTSCHRIFTVERFAHREN_AUS", "") == "1":      # Notschalter: Modul gar nicht einhängen
        print("[gutschriftverfahren] durch GUTSCHRIFTVERFAHREN_AUS=1 nicht eingehängt", flush=True)
        return
    fehlend = [n for n in _BRAUCHT if n not in g]
    if fehlend:
        raise RuntimeError("webapp.py stellt nicht bereit: " + ", ".join(fehlend))
    if "gv_einstellung" in app.view_functions:
        return                                   # schon eingerichtet
    _G.clear()
    _G.update({n: g[n] for n in _BRAUCHT})
    _G.update({n: g[n] for n in _OPTIONAL if n in g})
    _G["__webapp__"] = g

    def js(daten, status=200):
        return app.response_class(json.dumps(daten, ensure_ascii=False, default=str), status=status,
                                  mimetype="application/json")

    def wer(daten):
        try:
            fn = _G.get("_wer")
            return fn(daten) if callable(fn) else "web-user"
        except Exception:
            return "web-user"

    def finde(inv_id):
        """Beleg dieses Moduls: selbst ausgestellt, Storno dazu oder vom Kunden erhalten."""
        inv = _G["invoices"].get(inv_id)
        return inv if art(inv) in ("eigen", "storno", "kunde") else None

    # Einstellung. Unter /api/mandant/…, weil webapp.py Einstellungen dort auch ohne Lizenz speichern lässt;
    # /api/gutschriftverfahren/einstellung ist derselbe Endpunkt.
    @app.route("/api/mandant/gutschriftverfahren", methods=["GET", "POST"], endpoint="gv_einstellung")
    @app.route("/api/gutschriftverfahren/einstellung", methods=["GET", "POST"], endpoint="gv_einstellung_alt")
    def gv_einstellung():
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            if "aktiv" not in d:
                return js({"error": "Feld „aktiv“ (true/false) erwartet"}, 400)
            aktiv_setzen(bool(d.get("aktiv")))
        ms = _ms()
        kosit_ok, kosit_grund = False, ""
        try:
            from kosit_validator import is_available
            kosit_ok, kosit_grund = is_available(_kosit_pfad(ms))
        except Exception as ex:
            kosit_grund = str(ex)
        return js({"verfuegbar": True, "version": VERSION, "aktiv": ist_aktiv(ms), "belegart": BELEGART,
                   "naechste_nummer": naechste_nummer(advance=False), "einheiten": EINHEITEN,
                   "steuerfaelle": steuerfaelle_verfuegbar(), "nur_steuernummer_moeglich": _KENNUNG_IM_KERN,
                   "kosit_verfuegbar": bool(kosit_ok), "kosit_grund": kosit_grund,
                   "aussteller": {k: v for k, v in _mandant_lesen(ms).items() if k in ("name", "strasse", "plz", "ort", "ust_id", "email")}})

    @app.route("/api/gutschriftverfahren", methods=["POST"], endpoint="gv_ausstellen")
    def gv_ausstellen():
        daten = request.get_json(silent=True)
        if not isinstance(daten, dict):
            return js({"error": "JSON-Body erforderlich"}, 400)
        try:
            antwort, status = ausstellen(daten, wer(daten))
        except Exception as ex:
            import traceback
            print(f"[gutschriftverfahren] {ex}\n{traceback.format_exc()}", flush=True)
            return js({"error": f"Nicht möglich: {ex}", "angelegt": False}, 500)
        return js(antwort, status)

    def kurz(inv):
        return {"id": inv._id, "nummer": inv.invoice_number, "datum": inv.invoice_date.isoformat(),
                "steuerfall": "kleinunternehmer" if ist_ohne_ust(inv) else "regel", "art": art(inv),
                "lieferant": inv.seller.name, "brutto": float(inv.tax_inclusive_amount()), "status": inv.status,
                "kosit": getattr(inv, "_kosit", None) or {"ausstehend": True},
                "xml_url": f"/api/gutschriftverfahren/{inv._id}/xml", "pdf_url": f"/api/gutschriftverfahren/{inv._id}/pdf"}

    @app.route("/api/gutschriftverfahren", methods=["GET"], endpoint="gv_liste")
    def gv_liste():
        belege = sorted((kurz(i) for i in list(_G["invoices"].values()) if ist_gv(i)),
                        key=lambda e: e["nummer"], reverse=True)
        return js({"aktiv": ist_aktiv(), "belege": belege[:200], "anzahl": len(belege)})

    @app.route("/api/gutschriftverfahren/<inv_id>", methods=["GET"], endpoint="gv_beleg")
    def gv_beleg(inv_id):
        inv = finde(inv_id)
        if inv is None:
            return js({"error": "Beleg nicht gefunden"}, 404)
        if not getattr(inv, "_kosit", None) and art(inv) != "kunde":
            _kosit_hintergrund(inv)              # z. B. nach einem Neustart während der Prüfung
        return js(kurz(inv))

    @app.route("/api/gutschriftverfahren/<inv_id>/xml", endpoint="gv_xml")
    def gv_xml(inv_id):
        inv = finde(inv_id)
        if inv is None:
            return js({"error": "Beleg nicht gefunden"}, 404)
        xml_bytes = archiv_xml(inv) or generate_and_serialize(inv)
        art = "inline" if request.args.get("inline") in ("1", "true", "yes") else "attachment"
        return Response(xml_bytes, mimetype="application/xml",
                        headers={"Content-Disposition": f'{art}; filename="{inv.invoice_number}.xml"'})

    @app.route("/api/gutschriftverfahren/<inv_id>/pdf", endpoint="gv_pdf")
    def gv_pdf(inv_id):
        inv = finde(inv_id)
        if inv is None:
            return js({"error": "Beleg nicht gefunden"}, 404)
        try:
            name, pdf_bytes = beleg_pdf(inv)
        except Exception as ex:
            return js({"error": f"PDF-Erzeugung fehlgeschlagen: {ex}"}, 500)
        art = "attachment" if request.args.get("download") in ("1", "true", "yes") else "inline"
        return Response(pdf_bytes, mimetype="application/pdf",
                        headers={"Content-Disposition": f'{art}; filename="{name}"'})

    # Die vorhandenen PDF-Wege (Rechnungsansicht, Versand, ZUGFeRD-Knopf) würden aus einem solchen Beleg ein
    # Dokument „Rechnung“ mit dem Mandanten als Absender UND Empfänger machen. Deshalb liefern sie für Belege
    # dieses Moduls das eigene PDF; für alle anderen Belege bleibt alles, wie es ist.
    alt_pdf = g.get("_build_invoice_pdf")
    if callable(alt_pdf) and not getattr(alt_pdf, "_gv", False):
        def _build_invoice_pdf(inv, *a, **kw):
            if art(inv) in ("eigen", "storno", "kunde"):
                return beleg_pdf(inv)
            return alt_pdf(inv, *a, **kw)
        _build_invoice_pdf._gv = True
        _build_invoice_pdf.__doc__ = alt_pdf.__doc__
        g["_build_invoice_pdf"] = _build_invoice_pdf

    alt_zf = g.get("generate_zugferd_pdf")
    if callable(alt_zf) and not getattr(alt_zf, "_gv", False):
        def generate_zugferd_pdf(inv, *a, **kw):
            if art(inv) in ("eigen", "storno", "kunde"):
                return pdf_bauen(inv)[1]
            return alt_zf(inv, *a, **kw)
        generate_zugferd_pdf._gv = True
        generate_zugferd_pdf.__doc__ = alt_zf.__doc__
        g["generate_zugferd_pdf"] = generate_zugferd_pdf

    # ── 1.2: Storno, Versand, vom Kunden ausgestellte Gutschriften ──────────────────────────────────────────────────
    @app.route("/api/gutschriftverfahren/<inv_id>/stand", endpoint="gv_stand")
    def gv_stand(inv_id):
        inv = _G["invoices"].get(inv_id)
        if inv is None:
            return js({"error": "Beleg nicht gefunden"}, 404)
        return js(stand(inv))

    def _aktion(fn, *a):
        try:
            antwort, status = fn(*a)
        except Exception as ex:
            import traceback
            print(f"[gutschriftverfahren] {ex}\n{traceback.format_exc()}", flush=True)
            return js({"error": f"Nicht möglich: {ex}"}, 500)
        return js(antwort, status)

    @app.route("/api/gutschriftverfahren/<inv_id>/storno", methods=["POST"], endpoint="gv_storno")
    def gv_storno(inv_id):
        d = request.get_json(silent=True) or {}
        return _aktion(stornieren, inv_id, d, wer(d))

    @app.route("/api/gutschriftverfahren/<inv_id>/senden", methods=["GET", "POST"], endpoint="gv_senden")
    def gv_senden(inv_id):
        if request.method == "GET":              # Vorschau: sendet nichts
            inv = finde(inv_id)
            if inv is None:
                return js({"error": "Beleg nicht gefunden"}, 404)
            return js(versand_vorschau(inv))
        d = request.get_json(silent=True) or {}
        return _aktion(versenden, inv_id, d, wer(d))

    @app.route("/api/gutschriftverfahren/<inv_id>/anerkennen", methods=["POST"], endpoint="gv_anerkennen")
    def gv_anerkennen(inv_id):
        return _aktion(anerkennen, inv_id, wer(request.get_json(silent=True) or {}))

    @app.route("/api/gutschriftverfahren/<inv_id>/widerspruch", methods=["POST"], endpoint="gv_widerspruch")
    def gv_widerspruch(inv_id):
        d = request.get_json(silent=True) or {}
        return _aktion(widersprechen, inv_id, d, wer(d))

    @app.route("/api/gutschriftverfahren/<inv_id>/einstufen", methods=["POST"], endpoint="gv_einstufen")
    def gv_einstufen(inv_id):
        d = request.get_json(silent=True) or {}
        return _aktion(von_hand_einstufen, inv_id, d, wer(d))

    # Jeder Einleseweg (Hochladen, Postfach, Hintergrundabruf) legt den Beleg im Bestand ab. Statt an jedem Weg einen
    # Haken zu setzen, sieht das Modul vor jeder Anfrage die NEUEN Belege des Bestands durch (kostet nur bei neuen etwas).
    @app.before_request
    def _gv_eingaenge():
        try:
            if (request.path or "").startswith("/api/"):
                eingaenge_durchsehen()
        except Exception as ex:
            print(f"[gutschriftverfahren] {ex}", flush=True)
        return None

    # Eine vom Kunden ausgestellte Gutschrift ist eine Forderung: Sie zählt wie eine versendete Ausgangsrechnung zu den
    # offenen Posten (Liste, Dashboard, Zahlungsabgleich) – auch ohne dass wir sie je versendet hätten. Gemahnt wird sie
    # vom Programm nicht (die Mahntexte sprechen von „unserer Rechnung“); deshalb gilt sie hier nie als überfällig.
    alt_sent = g.get("_is_invoice_sent")
    if callable(alt_sent) and not getattr(alt_sent, "_gv", False):
        def _is_invoice_sent(inv, *a, **kw):
            if ist_gv_kunde(inv):
                return getattr(inv, "status", "") not in ("ZURUECKGEWIESEN", "STORNIERT")
            return alt_sent(inv, *a, **kw)
        _is_invoice_sent._gv = True
        _is_invoice_sent.__doc__ = alt_sent.__doc__
        g["_is_invoice_sent"] = _is_invoice_sent

    alt_over = g.get("_is_invoice_overdue")
    if callable(alt_over) and not getattr(alt_over, "_gv", False):
        def _is_invoice_overdue(inv, *a, **kw):
            if ist_gv_kunde(inv):
                return False
            return alt_over(inv, *a, **kw)
        _is_invoice_overdue._gv = True
        _is_invoice_overdue.__doc__ = alt_over.__doc__
        g["_is_invoice_overdue"] = _is_invoice_overdue

    # Vorhandene Wege, die für Belege dieses Moduls das Falsche täten, weisen sie mit Klartext ab.
    def _sperre(name, pruefung):
        alt = app.view_functions.get(name)
        if not callable(alt) or getattr(alt, "_gv", False):
            return

        def huelle(*a, **kw):
            inv = _G["invoices"].get(kw.get("inv_id") or (a[0] if a else ""))
            meldung = pruefung(inv) if inv is not None else None
            if meldung:
                return js({"error": meldung, "gutschriftverfahren": art(inv)}, 400)
            return alt(*a, **kw)
        huelle._gv = True
        huelle.__name__ = getattr(alt, "__name__", name)
        huelle.__doc__ = alt.__doc__
        app.view_functions[name] = huelle

    _sperre("api_send_invoice", lambda inv: {
        "kunde": "Diese Gutschrift hat Ihr Kunde ausgestellt – sie wird nicht versendet.",
        "eigen": "Bitte den Versand an den Lieferanten benutzen (Knopf „An Lieferanten senden“).",
        "storno": "Bitte den Versand an den Lieferanten benutzen (Knopf „An Lieferanten senden“)."}.get(art(inv)))
    for _name in ("api_storno", "api_gutschrift"):
        _sperre(_name, lambda inv: {
            "kunde": "Eine Gutschrift Ihres Kunden können Sie nicht selbst stornieren oder berichtigen – widersprechen Sie ihr "
                     "oder bitten Sie den Kunden um eine Korrektur.",
            "eigen": "Bitte „Gutschrift stornieren“ des Gutschriftverfahrens benutzen.",
            "storno": "Ein Stornobeleg kann nicht storniert werden."}.get(art(inv)))
    _sperre("api_dunning_invoice_generate", lambda inv: (
        "Zu einer vom Kunden ausgestellten Gutschrift erzeugt das Programm kein Mahnschreiben – bitte erinnern Sie den "
        "Kunden von Hand an die Zahlung." if art(inv) == "kunde" else None))

    print(f"[gutschriftverfahren] {VERSION} eingerichtet (Schalter {'an' if ist_aktiv() else 'aus'})", flush=True)
