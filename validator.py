"""
E-Rechnungssystem – Validierungsengine
P-04 / FR-210: Geschäftsregeln XRechnung / EN 16931
Unterscheidet: ERROR (blockierend), WARNING, INFO
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re
from models import Invoice


class Severity(Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationIssue:
    rule_id: str
    severity: Severity
    message: str
    field: str = ""
    context: str = ""


@dataclass
class ValidationReport:
    invoice_number: str = ""
    timestamp: str = ""
    rule_profile: str = "XRechnung 3.0 / EN 16931"
    validator_version: str = "1.0.0"
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)

    def summary(self) -> str:
        status = "GÜLTIG" if self.is_valid else "UNGÜLTIG"
        lines = [
            f"═══ Validierungsbericht ═══",
            f"Rechnung:    {self.invoice_number}",
            f"Zeitpunkt:   {self.timestamp}",
            f"Profil:      {self.rule_profile}",
            f"Status:      {status}",
            f"Fehler:      {self.error_count}  Warnungen: {self.warning_count}",
        ]
        if self.issues:
            lines.append("")
            for i in self.issues:
                px = {"ERROR": "✗", "WARNING": "⚠", "INFO": "ℹ"}[i.severity.value]
                lines.append(f"  {px} [{i.rule_id}] {i.message}")
                if i.field:
                    lines.append(f"    Feld: {i.field}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "invoice_number": self.invoice_number,
            "timestamp": self.timestamp,
            "rule_profile": self.rule_profile,
            "validator_version": self.validator_version,
            "is_valid": self.is_valid,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [{"rule_id": i.rule_id, "severity": i.severity.value,
                        "message": i.message, "field": i.field} for i in self.issues],
        }


IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$")


# BT-3 Belegart: erlaubte Codes nach EN 16931 (Regel BR-CL-01, aus den amtlichen KoSIT-Prüfregeln übernommen)
# und die engere Auswahl der XRechnung (BR-DE-17). 24.11.2026.
_BT3_RECHNUNG = set("71 80 81 82 84 102 130 202 203 204 211 218 219 295 325 326 331 380 382 383 384 385 386 387 388 389 "
                    "390 393 394 395 456 457 471 472 473 500 501 502 503 527 553 575 623 633 751 780 817 870 875 876 877 935".split())
_BT3_GUTSCHRIFT = set("81 83 261 262 296 308 381 396 420 458 532".split())
_BT3_XRECHNUNG = {"326", "380", "384", "389", "381", "875", "876", "877"}


# ── Leitweg-ID (BT-10 bei Rechnungen an Behörden) ──────────────────────────────────────────────────
# Aufbau: Grobadressierung – Feinadressierung – Prüfziffer, z. B. 04011000-1234512345-06
#   Grobadressierung  2, 3, 5, 8 oder 12 Ziffern (Bund/Land, Regierungsbezirk, Kreis, Gemeinde)
#   Feinadressierung  freiwillig, bis 30 Buchstaben/Ziffern
#   Prüfziffer        2 Ziffern, ISO 7064 Modulo 97-10 über Grob- und Feinadressierung ohne Bindestriche
#                     (Buchstaben A=10 … Z=35, wie bei der IBAN: 98 − (Zahl·100 mod 97))
_LEITWEG_AEHNLICH = re.compile(r"^(\d{2,12})-(?:([A-Za-z0-9]{0,30})-)?([A-Za-z0-9]{1,3})$")


def leitweg_pruefziffer(grob: str, fein: str = "") -> str:
    zahl = "".join(str(int(c, 36)) for c in (grob + fein).upper())
    return f"{98 - int(zahl + '00') % 97:02d}"


def leitweg_pruefen(text) -> tuple:
    """(sieht_wie_leitweg_id_aus, fehlertext). fehlertext leer = formal richtig.
    „Sieht so aus“ heißt: Ziffernblock, Bindestrich, ggf. Feinadresse, kurzer Schlussblock. Kundennummern
    wie K-0001 oder PRIV-00012 und Texte fallen nicht darunter und werden nicht beanstandet."""
    t = str(text or "").strip()
    m = _LEITWEG_AEHNLICH.match(t)
    if not m:
        return False, ""
    grob, fein, pz = m.group(1), m.group(2) or "", m.group(3)
    if len(grob) not in (2, 3, 5, 8, 12):
        return True, f"die Grobadressierung „{grob}“ muss 2, 3, 5, 8 oder 12 Ziffern haben"
    if not (len(pz) == 2 and pz.isdigit()):
        return True, f"die Prüfziffer „{pz}“ muss aus genau zwei Ziffern bestehen"
    if len(t) > 46:
        return True, "eine Leitweg-ID hat höchstens 46 Zeichen"
    soll = leitweg_pruefziffer(grob, fein)
    if pz != soll:
        return True, f"die Prüfziffer „{pz}“ passt nicht (rechnerisch {soll}) – bitte auf Tippfehler prüfen"
    return True, ""


def validate_invoice(inv: Invoice) -> ValidationReport:
    report = ValidationReport(invoice_number=inv.invoice_number, timestamp=datetime.now().isoformat())
    iss = report.issues

    # Die deutschen Zusatzregeln (BR-DE-*) gelten nur fuer die XRechnung. Eine
    # eingegangene ZUGFeRD-/Factur-X-Rechnung (Profil EN 16931, BASIC …) oder eine
    # UBL-Rechnung ohne XRechnung-Kennung ist ohne diese Angaben normkonform —
    # dort sind BR-DE-Befunde Hinweise, keine Fehler.
    xrechnung = (getattr(inv, "_source_format", "") or "") not in ("ZUGFERD_CII", "ZUGFERD", "UBL_GENERIC")

    def err(rid, msg, fld=""):
        if rid.startswith("BR-DE") and not xrechnung:
            iss.append(ValidationIssue(rid, Severity.WARNING, msg + " (nur für XRechnung verpflichtend)", fld))
        else:
            iss.append(ValidationIssue(rid, Severity.ERROR, msg, fld))
    def warn(rid, msg, fld=""): iss.append(ValidationIssue(rid, Severity.WARNING, msg, fld))
    def info(rid, msg, fld=""): iss.append(ValidationIssue(rid, Severity.INFO, msg, fld))

    # Ausstellen oder Einlesen? Ein eingelesener Fremdbeleg trägt die Richtung EINGANG oder (vor der
    # Einordnung) einen Empfangszeitpunkt vom Parser. Steuerregeln sind beim AUSSTELLEN Fehler — die amtliche
    # Prüfung lehnt den Beleg sonst ab —, beim Einlesen Warnungen: den fremden Beleg können wir nicht ändern.
    _richtung = (getattr(inv, "_direction", "") or "")
    eingang = _richtung == "EINGANG" or (_richtung != "AUSGANG" and bool(getattr(inv, "_received_at", "")))

    def regel(rid, msg, fld=""):
        if eingang:
            warn(rid, msg + " (eingelesener Beleg: nur Hinweis)", fld)
        else:
            err(rid, msg, fld)

    # Kopf-Pflichtfelder
    if not inv.invoice_number: err("BR-01", "Rechnungsnummer (BT-1) fehlt.", "BT-1")
    if not inv.invoice_date: err("BR-02", "Rechnungsdatum (BT-2) fehlt.", "BT-2")
    if not inv.invoice_type_code: err("BR-03", "Rechnungstyp (BT-3) fehlt.", "BT-3")
    _tc = str(inv.invoice_type_code or "").strip()
    if _tc and _tc not in _BT3_RECHNUNG and _tc not in _BT3_GUTSCHRIFT:
        err("BR-CL-01", f"Rechnungstyp (BT-3) „{_tc}“ steht nicht in der Codeliste UNTDID 1001 der EN 16931.", "BT-3")
    elif _tc and _tc not in _BT3_XRECHNUNG:
        warn("BR-DE-17", f"Rechnungstyp (BT-3) „{_tc}“: die XRechnung sieht nur 326, 380, 381, 384, 389, 875, 876, 877 vor.", "BT-3")
    _vor = (getattr(inv, "preceding_invoice", "") or "").strip()
    _vor_datum = getattr(inv, "preceding_invoice_date", None)
    if _tc == "384" and not _vor:
        warn("BG-3", "Rechnungskorrektur (384) ohne Bezug auf die Ursprungsrechnung (BT-25).", "BT-25")
    if _vor_datum and not _vor:
        err("BR-55", "Datum der Bezugsrechnung (BT-26) ohne deren Nummer (BT-25).", "BT-25")
    if _vor_datum and inv.invoice_date and _vor_datum > inv.invoice_date:
        warn("BT-26", "Das Datum der Bezugsrechnung (BT-26) liegt nach dem Datum dieses Belegs.", "BT-26")
    if not inv.currency_code: err("BR-04", "Währungscode (BT-5) fehlt.", "BT-5")

    ref = inv.buyer_reference or inv.buyer.buyer_reference
    if not ref: err("BR-DE-15", "Buyer Reference (BT-10) ist in XRechnung verpflichtend.", "BT-10")
    if ref:
        _lw_aehnlich, _lw_fehler = leitweg_pruefen(ref)
        if _lw_aehnlich and _lw_fehler:
            warn("BT-10-LEITWEG", f"Die Käuferreferenz „{ref}“ sieht wie eine Leitweg-ID aus, ist aber formal falsch: "
                                  f"{_lw_fehler}. Aufbau: Grobadressierung-Feinadressierung-Prüfziffer, "
                                  f"z. B. 04011000-1234512345-06.", "BT-10")

    # Seller
    if not inv.seller.name: err("BR-06", "Verkäufername (BT-27) fehlt.", "BT-27")
    if not inv.seller.electronic_address: err("BR-DE-16", "Seller Electronic Address (BT-34) fehlt.", "BT-34")
    if not inv.seller.electronic_address_scheme: err("BR-DE-16-S", "Scheme Identifier für BT-34 fehlt.", "BT-34-1")
    if not inv.seller.address.city: err("BR-09", "Seller City (BT-37) fehlt.", "BT-37")
    if not inv.seller.address.post_code: err("BR-DE-04", "Seller Post Code (BT-38) fehlt.", "BT-38")
    if not inv.seller.address.country_code: err("BR-09-C", "Seller Country Code (BT-40) fehlt.", "BT-40")
    if not inv.seller.contact.name: err("BR-DE-02", "Seller Contact Name (BT-41) fehlt.", "BT-41")
    if not inv.seller.contact.telephone: err("BR-DE-06", "Seller Contact Telephone (BT-42) fehlt.", "BT-42")
    if not inv.seller.contact.email: err("BR-DE-07", "Seller Contact Email (BT-43) fehlt.", "BT-43")
    if not inv.seller.vat_id and not inv.seller.tax_registration_id:
        err("BR-CO-26", "Mindestens Steuer-ID (BT-31 oder BT-32) erforderlich.", "BT-31/BT-32")
    else:
        # BR-CO-26 (EN 16931): zur Identifikation des Verkäufers braucht es die USt-ID (BT-31), die Registernummer
        # (BT-30) oder eine Verkäuferkennung (BT-29) — die Steuernummer BT-32 allein genügt der amtlichen Prüfung nicht.
        # Beim Ausstellen zählt die wirksame Kennung (Seller.kennung(): ohne USt-ID und Registernummer die Steuernummer),
        # beim Einlesen nur, was in der Datei stand.
        _bt29 = (getattr(inv.seller, "seller_id", "") or "") if eingang else (
            inv.seller.kennung() if hasattr(inv.seller, "kennung") else "")
        if not (inv.seller.vat_id or getattr(inv.seller, "company_id", "") or _bt29):
            regel("BR-CO-26", "Der Verkäufer ist nicht identifizierbar: USt-ID (BT-31), Registernummer (BT-30) "
                              "oder Verkäuferkennung (BT-29) erforderlich.", "BT-29/BT-30/BT-31")

    # Buyer
    if not inv.buyer.name: err("BR-07", "Käufername (BT-44) fehlt.", "BT-44")
    if not inv.buyer.electronic_address: err("BR-DE-17", "Buyer Electronic Address (BT-49) fehlt.", "BT-49")
    if not inv.buyer.address.city: err("BR-11", "Buyer City (BT-52) fehlt.", "BT-52")
    if not inv.buyer.address.post_code: err("BR-DE-05", "Buyer Post Code (BT-53) fehlt.", "BT-53")

    # Zahlung
    if inv.amount_due() > Decimal("0"):
        if not inv.payment.due_date and not inv.payment.payment_terms:
            err("BR-CO-25", "Bei positivem Zahlbetrag: Fälligkeitsdatum oder Zahlungsbedingung nötig.", "BT-9/BT-20")
    if inv.payment.means_code in ("30", "58"):
        if not inv.payment.iban:
            err("BR-DE-19", "Bei Überweisung: IBAN (BT-84) erforderlich.", "BT-84")
        elif not IBAN_RE.match(inv.payment.iban.replace(" ", "").upper()):
            warn("BR-DE-19-F", f"IBAN-Format prüfen: '{inv.payment.iban}'", "BT-84")
    if inv.payment.means_code == "59" and not inv.payment.mandate_reference:
        err("BR-DE-20", "Bei Lastschrift: Mandatsreferenz (BT-89) erforderlich.", "BT-89")

    # Positionen
    if not inv.lines:
        err("BR-16", "Mindestens eine Position erforderlich.")
    for idx, line in enumerate(inv.lines, 1):
        ctx = f"Pos. {idx} ({line.line_id})"
        if not line.line_id: err("BR-21", f"{ctx}: Positionsnummer fehlt.", "BT-126")
        if line.quantity <= Decimal("0"): err("BR-22", f"{ctx}: Menge muss > 0 sein.", "BT-129")
        if not line.item_name: err("BR-25", f"{ctx}: Artikelbezeichnung fehlt.", "BT-153")
        if line.unit_price < Decimal("0"): err("BR-26", f"{ctx}: Einzelpreis darf nicht negativ sein.", "BT-146")
        computed = line.compute_net()
        if computed != line.line_net_amount:
            err("BR-CO-10", f"{ctx}: Nettobetrag inkonsistent. Erwartet={computed}, angegeben={line.line_net_amount}", "BT-131")
        # BR-S-05 (EN 16931): Kategorie „S“ (Regelbesteuerung) verlangt einen Steuersatz über 0. Eine 0-%-Position
        # braucht eine andere Kategorie samt Grund (E steuerfrei/Kleinunternehmer § 19 UStG, AE § 13b UStG, Z Nullsatz,
        # O nicht steuerbar). Bis 25.11.2026 meldete die Prüfung hier „0 Fehler“, die amtliche Prüfung (KoSIT) lehnt
        # solche Rechnungen ab. Die Kategorie wird NICHT geraten — das ist eine fachliche Entscheidung des Ausstellers.
        # Eingangsrechnungen: nur Hinweis (bei erfassten Papier-/PDF-Rechnungen stammt die Kategorie nicht vom Aussteller).
        if line.tax_rate == Decimal("0") and (line.tax_category or "S") == "S":
            _txt = (f"Position {idx}: Steuersatz 0 % ohne Angabe, warum keine Umsatzsteuer berechnet wird "
                    f"(Kleinunternehmer § 19 UStG, Steuerschuldnerschaft des Leistungsempfängers § 13b UStG oder steuerfrei).")
            # 25.11.2026: vorerst nur WARNUNG, auch bei Ausgangsrechnungen. Solange es im Formular kein Auswahlfeld für den
            # Grund (§ 19, § 13b, steuerfrei) gibt, würde ein Fehler jede 0-%-Rechnung vom Versand ausschließen
            # (Kleinunternehmer könnten nichts mehr versenden). Auf err(...) umstellen, sobald das Feld existiert.
            # Seit es den Steuerfall gibt (Auswahl in der Maske, Feld steuerfall am Beleg), ist das beim Ausstellen
            # ein FEHLER; beim Einlesen fremder Belege bleibt es eine Warnung.
            if eingang:
                warn("BR-S-05", _txt + " (Eingangsrechnung: nur Hinweis)", "BT-151")
            else:
                err("BR-S-05", _txt + " Bitte den Steuerfall der Rechnung wählen.", "BT-151")

    # ── Steuerkategorien und Steuerfall (BG-23, BT-118 … BT-121) ──
    from models import STEUERFAELLE as _SF, KATEGORIEN_MIT_GRUND as _MIT_GRUND
    _kats: dict = {}
    for _x in list(inv.lines) + list(inv.allowances_charges):
        _kats.setdefault((_x.tax_category or "S"), []).append(_x.tax_rate)
    _kat_name = {"E": "E (steuerfrei)", "AE": "AE (Steuerschuldnerschaft des Leistungsempfängers)", "Z": "Z (Nullsatz)"}
    for _k, _rid in (("E", "BR-E-05"), ("AE", "BR-AE-05"), ("Z", "BR-Z-05")):
        if _k in _kats and any(Decimal(str(_r or 0)) != 0 for _r in _kats[_k]):
            regel(_rid, f"Kategorie {_kat_name[_k]} verlangt den Steuersatz 0 %.", "BT-152")
    _hat_grund = lambda k: bool((inv.befreiungsgrund_text(k) if hasattr(inv, "befreiungsgrund_text") else "")
                                or (inv.befreiungsgrund_code(k) if hasattr(inv, "befreiungsgrund_code") else ""))
    for _k, _rid in (("E", "BR-E-10"), ("AE", "BR-AE-10"), ("K", "BR-IC-10"), ("G", "BR-G-10"), ("O", "BR-O-10")):
        if _k in _kats and _k in _MIT_GRUND and not _hat_grund(_k):
            regel(_rid, f"Kategorie {_k}: Es fehlt der Grund, warum keine Umsatzsteuer berechnet wird (BT-120). "
                        + ("Bei „steuerfrei“ bitte die Vorschrift angeben, z. B. „Steuerfrei nach § 4 Nr. … UStG“."
                           if _k == "E" else ""), "BT-120")
    if "AE" in _kats:
        if eingang:
            # amtlich (BR-AE-02 … 04): Verkäufer BT-31/BT-32, Käufer BT-48 oder BT-47
            if not (inv.seller.vat_id or inv.seller.tax_registration_id):
                warn("BR-AE-02", "Steuerschuldnerschaft des Leistungsempfängers: Steuernummer oder USt-ID des Verkäufers fehlt.", "BT-31")
            if not (inv.buyer.vat_id or getattr(inv.buyer, "company_id", "")):
                warn("BR-AE-02", "Steuerschuldnerschaft des Leistungsempfängers: USt-ID des Käufers (BT-48) fehlt.", "BT-48")
        else:
            if not inv.seller.vat_id:
                err("BR-AE-02", "Steuerschuldnerschaft des Leistungsempfängers (§ 13b UStG): Die USt-ID des Verkäufers (BT-31) ist erforderlich.", "BT-31")
            if not inv.buyer.vat_id:
                err("BR-AE-02", "Steuerschuldnerschaft des Leistungsempfängers (§ 13b UStG): Die USt-ID des Käufers (BT-48) ist erforderlich.", "BT-48")
    if not eingang:
        _fall = getattr(inv, "steuerfall", "regel") or "regel"
        if _fall not in _SF:
            err("STEUERFALL-01", f"Steuerfall „{_fall}“ ist unbekannt.", "BT-151")
        elif _fall != "regel" and inv.lines and set(_kats) != {_SF[_fall]["kategorie"]}:
            err("STEUERFALL-02", f"Steuerfall „{_SF[_fall]['name']}“: alle Positionen müssen die Kategorie "
                                 f"{_SF[_fall]['kategorie']} tragen (gefunden: {', '.join(sorted(_kats))}). "
                                 f"Invoice.steuerfall_anwenden() setzt sie.", "BT-151")
        elif _fall == "regel" and (set(_kats) & {"E", "AE"}):
            # Kategorie von Hand gesetzt, ohne Steuerfall: zulässig, solange der Grund da ist (oben geprüft)
            pass

    # Nachlass-Konsistenz
    for idx, ac in enumerate(inv.allowances_charges, 1):
        if ac.percentage and ac.base_amount:
            expected = (ac.base_amount * ac.percentage / Decimal("100")).quantize(Decimal("0.01"))
            if expected != ac.amount:
                warn("BR-CO-05", f"Nachlass/Zuschlag {idx}: Betrag ({ac.amount}) ≠ Prozent×Basis ({expected})")

    # Summen — BR-CO-16: Zahlbetrag = Bruttobetrag minus Anzahlungen.
    # Eine Schlussrechnung MIT Anzahlung hat zu Recht einen kleineren
    # Zahlbetrag; das ist kein Befund, sondern der Sinn von BT-113.
    brutto = inv.tax_inclusive_amount()
    vorab = Decimal(str(getattr(inv, "prepaid_amount", 0) or 0))
    actual_due = inv.amount_due()
    if actual_due != brutto - vorab:
        warn("BR-CO-16", f"Zahlbetrag ({actual_due}) ≠ Bruttobetrag ({brutto}) minus "
                         f"Anzahlungen ({vorab})", "BT-115")
    if vorab < Decimal("0"):
        err("BR-DE-ANZ", f"Anzahlungsbetrag darf nicht negativ sein ({vorab}).", "BT-113")
    if vorab > brutto:
        err("BR-DE-ANZ", f"Anzahlungen ({vorab}) übersteigen den Bruttobetrag ({brutto}) — "
                         f"das ergäbe einen negativen Zahlbetrag.", "BT-113")

    # Summen aus der eingelesenen Datei (BT-106 … BT-115) gegen die Nachrechnung aus den Positionen.
    # Das Modell rechnet selbst; weicht die Datei um mehr als 1 Cent ab, ist das eine Warnung — angezeigt und
    # gebucht wird der nachgerechnete Wert, die Abweichung soll aber niemand übersehen.
    _ds = getattr(inv, "_datei_summen", None)
    if isinstance(_ds, dict) and _ds:
        _rund = Decimal(str(_ds.get("BT-114") or 0))
        _soll = {
            "BT-106": ("Summe der Positionen", inv.sum_line_net()),
            "BT-107": ("Summe der Nachlässe", inv.sum_allowances()),
            "BT-108": ("Summe der Zuschläge", inv.sum_charges()),
            "BT-109": ("Gesamtbetrag ohne Umsatzsteuer", inv.tax_exclusive_amount()),
            "BT-110": ("Umsatzsteuer", inv.tax_amount()),
            "BT-112": ("Gesamtbetrag mit Umsatzsteuer", inv.tax_inclusive_amount()),
            "BT-113": ("Vorauszahlung", vorab),
            "BT-115": ("Zahlbetrag", inv.amount_due(rounding=_rund)),
        }
        for _bt, (_name, _berechnet) in _soll.items():
            if _bt not in _ds:
                continue
            _datei = Decimal(str(_ds[_bt]))
            if abs(_datei - Decimal(str(_berechnet))) > Decimal("0.01"):
                warn("SUMME-" + _bt, f"{_name} ({_bt}): in der Datei steht {_datei:.2f}, die Nachrechnung aus den "
                                     f"Positionen ergibt {Decimal(str(_berechnet)):.2f}. Bitte den Beleg prüfen.", _bt)
        if _rund:
            info("SUMME-BT-114", f"Die Datei enthält einen Rundungsbetrag (BT-114) von {_rund:.2f}; "
                                 f"er wird im Zahlbetrag nicht mitgeführt.", "BT-114")

    # Hinweise
    if not inv.order_reference and not inv.contract_reference:
        info("HINT-01", "Keine Bestell-/Vertragsreferenz – empfohlen für automatisierte Verarbeitung.", "BT-13/BT-12")
    if not inv.period_start and not inv.period_end and not inv.tax_point_date:
        info("HINT-02", "Kein Leistungszeitraum angegeben.", "BT-73/BT-74")

    return report
