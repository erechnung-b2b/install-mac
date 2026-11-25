"""
E-Rechnungssystem – Fachliches Datenmodell
EN 16931 / XRechnung BT/BG-Felder
Basis: erechnung-erstellen.txt Abschnitt 6 + e-rechnung_pflichtenheft.docx Abschnitt 7
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
import json, uuid


class InvoiceStatus(Enum):
    NEU = "NEU"
    IN_PRUEFUNG = "IN_PRUEFUNG"
    IN_FREIGABE = "IN_FREIGABE"
    FREIGEGEBEN = "FREIGEGEBEN"
    EXPORTIERT = "EXPORTIERT"
    ZURUECKGEWIESEN = "ZURUECKGEWIESEN"
    ARCHIVIERT = "ARCHIVIERT"


class WorkflowAction(Enum):
    SACHLICHE_PRUEFUNG = "SACHLICHE_PRUEFUNG"
    FORMALE_PRUEFUNG = "FORMALE_PRUEFUNG"
    KAUFMAENNISCHE_FREIGABE = "KAUFMAENNISCHE_FREIGABE"
    ABLEHNUNG = "ABLEHNUNG"
    RUECKFRAGE = "RUECKFRAGE"
    WIEDERVORLAGE = "WIEDERVORLAGE"
    EXPORT = "EXPORT"


@dataclass
class Address:
    street: str = ""
    house_number: str = ""
    city: str = ""
    post_code: str = ""
    country_code: str = "DE"
    address_line_2: str = ""

    def street_full(self) -> str:
        """Straße + Hausnummer kombiniert für Anzeige (DIN 5008)."""
        s = (self.street or "").strip()
        h = (self.house_number or "").strip()
        return (s + " " + h).strip() if h else s


@dataclass
class Contact:
    name: str = ""
    telephone: str = ""
    email: str = ""


@dataclass
class Seller:
    name: str = ""
    address: Address = field(default_factory=Address)
    electronic_address: str = ""
    electronic_address_scheme: str = "EM"
    contact: Contact = field(default_factory=Contact)
    vat_id: str = ""
    tax_registration_id: str = ""
    registration_name: str = ""
    company_id: str = ""          # BT-30 Registernummer (z. B. HRB 4711)
    seller_id: str = ""           # BT-29 Verkäuferkennung (frei vergebene Kennung, z. B. Lieferantennummer)

    def kennung(self) -> str:
        """Wirksame Verkäuferkennung BT-29 für die E-Rechnung.

        EN 16931 (BR-CO-26) verlangt zur Identifikation des Verkäufers die USt-ID (BT-31), die
        Registernummer (BT-30) ODER eine Verkäuferkennung (BT-29). Wer keine USt-ID und keine
        Registernummer hat (typisch: Kleinunternehmer), wird über seine Steuernummer identifiziert —
        sie steht ohnehin als BT-32 im Beleg. Eine ausdrücklich hinterlegte Kennung hat Vorrang.
        """
        if (self.seller_id or "").strip():
            return self.seller_id.strip()
        if not (self.vat_id or "").strip() and not (self.company_id or "").strip():
            return (self.tax_registration_id or "").strip()
        return ""


@dataclass
class Buyer:
    name: str = ""
    address: Address = field(default_factory=Address)
    electronic_address: str = ""
    electronic_address_scheme: str = "EM"
    buyer_reference: str = ""
    vat_id: str = ""
    company_id: str = ""
    contact: Optional[Contact] = None


@dataclass
class PaymentInfo:
    means_code: str = "58"
    due_date: Optional[date] = None
    payment_terms: str = ""
    iban: str = ""
    bic: str = ""
    bank_name: str = ""
    mandate_reference: str = ""
    creditor_id: str = ""
    debited_account: str = ""
    # Skonto (16.11.2026): Prozent auf den Zahlbetrag bei Zahlung binnen so vielen Tagen ab Rechnungsdatum; 0 = keins
    skonto_percent: float = 0.0
    skonto_days: int = 0

    def skonto_zeile(self) -> str:
        """Strukturierte Skonto-Angabe der XRechnung (BR-DE-18), leer ohne Skonto."""
        try:
            p, t = float(self.skonto_percent or 0), int(self.skonto_days or 0)
        except (TypeError, ValueError):
            return ""
        return f"#SKONTO#TAGE={t}#PROZENT={p:.2f}#" if p > 0 and t > 0 else ""

    def terms_mit_skonto(self) -> str:
        """Zahlungsbedingung für die E-Rechnung (BT-20): Klartext, darunter die Skonto-Zeile mit Zeilenende."""
        z = self.skonto_zeile()
        if not z:
            return self.payment_terms
        return ((self.payment_terms or "").replace(" ohne Abzug", "").strip() + "\n" + z + "\n").lstrip("\n")


@dataclass
class AllowanceCharge:
    is_charge: bool = False
    amount: Decimal = Decimal("0.00")
    base_amount: Decimal = Decimal("0.00")
    percentage: Decimal = Decimal("0.00")
    reason: str = ""
    reason_code: str = ""
    tax_category: str = "S"
    tax_rate: Decimal = Decimal("19.00")


@dataclass
class InvoiceLine:
    line_id: str = ""
    quantity: Decimal = Decimal("1")
    unit_code: str = "C62"
    line_net_amount: Decimal = Decimal("0.00")
    item_name: str = ""
    unit_price: Decimal = Decimal("0.00")
    tax_category: str = "S"
    tax_rate: Decimal = Decimal("19.00")
    item_description: str = ""
    item_id: str = ""
    price_base_quantity: Decimal = Decimal("1")
    allowances_charges: list[AllowanceCharge] = field(default_factory=list)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    order_reference: str = ""
    note: str = ""

    def compute_net(self) -> Decimal:
        base = self.quantity * self.unit_price / self.price_base_quantity
        for ac in self.allowances_charges:
            base = base + ac.amount if ac.is_charge else base - ac.amount
        return base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class TaxSubtotal:
    taxable_amount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    category_code: str = "S"
    rate: Decimal = Decimal("19.00")
    exemption_reason: str = ""
    exemption_reason_code: str = ""     # BT-121 (VATEX)


# ── Steuerfall je Beleg (25.11.2026) ───────────────────────────────────────────────────────────────
# Ein Beleg trägt EINEN Steuerfall. Er bestimmt die Umsatzsteuerkategorie aller Positionen (BT-151),
# die Aufschlüsselung (BT-118), den Steuersatz 0 und den Befreiungsgrund (BT-120, bei § 13b auch BT-121).
#   regel             S   Regelbesteuerung mit 19 % / 7 %
#   kleinunternehmer  E   § 19 UStG — keine Umsatzsteuer, fester Hinweistext
#   reverse_charge    AE  § 13b UStG — Steuerschuldnerschaft des Leistungsempfängers
#   steuerfrei        E   steuerfreier Umsatz, Grund frei einzugeben
#   nullsatz          Z   Nullsteuersatz (kein Befreiungsgrund in der E-Rechnung erlaubt, BR-Z-10)
# Innergemeinschaftliche Lieferung (K) gibt es bewusst nicht: dafür fehlen Lieferanschrift und Ausland.
TEXT_KLEINUNTERNEHMER = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."
TEXT_REVERSE_CHARGE = "Steuerschuldnerschaft des Leistungsempfängers"
TEXT_NULLSATZ = "Es gilt der Nullsteuersatz (0 % Umsatzsteuer)."
STEUERFAELLE = {
    "regel":            {"kategorie": "S",  "text": "",                    "code": "",            "name": "Regelbesteuerung"},
    "kleinunternehmer": {"kategorie": "E",  "text": TEXT_KLEINUNTERNEHMER, "code": "",            "name": "Kleinunternehmer (§ 19 UStG)"},
    "reverse_charge":   {"kategorie": "AE", "text": TEXT_REVERSE_CHARGE,   "code": "VATEX-EU-AE", "name": "Steuerschuldnerschaft des Leistungsempfängers (§ 13b UStG)"},
    "steuerfrei":       {"kategorie": "E",  "text": "",                    "code": "",            "name": "Steuerfreier Umsatz"},
    "nullsatz":         {"kategorie": "Z",  "text": "",                    "code": "",            "name": "Nullsteuersatz"},
}
# Kategorien, die einen Befreiungsgrund (BT-120 oder BT-121) verlangen
KATEGORIEN_MIT_GRUND = ("E", "AE", "K", "G", "O")
_STEUERFALL_ALIAS = {
    "regel": "regel", "standard": "regel", "s": "regel", "regelbesteuerung": "regel",
    "kleinunternehmer": "kleinunternehmer", "§19": "kleinunternehmer", "19ustg": "kleinunternehmer",
    "§19ustg": "kleinunternehmer", "klein": "kleinunternehmer",
    "reverse_charge": "reverse_charge", "reversecharge": "reverse_charge", "reverse-charge": "reverse_charge",
    "§13b": "reverse_charge", "13b": "reverse_charge", "§13bustg": "reverse_charge", "ae": "reverse_charge",
    "steuerfrei": "steuerfrei", "befreit": "steuerfrei", "e": "steuerfrei",
    "nullsatz": "nullsatz", "nullsteuersatz": "nullsatz", "z": "nullsatz",
}


def steuerfall_normalisieren(wert) -> str:
    """Eingabe (Maske, Schnittstelle, CSV) → Schlüssel aus STEUERFAELLE. Leer = „regel“.
    Unbekannte Angaben werden nicht geraten: ValueError mit Klartext."""
    t = "".join(str(wert or "").lower().split())
    if not t:
        return "regel"
    if t in _STEUERFALL_ALIAS:
        return _STEUERFALL_ALIAS[t]
    raise ValueError(f"Steuerfall „{wert}“ ist unbekannt. Möglich sind: regel, kleinunternehmer, "
                     f"reverse_charge, steuerfrei, nullsatz.")


def steuerfall_ableiten(kategorien, grund: str = "") -> str:
    """Steuerfall eines EINGELESENEN Belegs aus seinen Kategorien (und dem Grundtext)."""
    k = {str(x or "S") for x in kategorien}
    if k == {"AE"}:
        return "reverse_charge"
    if k == {"Z"}:
        return "nullsatz"
    if k == {"E"}:
        g = (grund or "").lower()
        return "kleinunternehmer" if ("§ 19" in g or "§19" in g or "kleinunternehmer" in g) else "steuerfrei"
    return "regel"


def steuerfall_setzen(inv, fall: str, grund: str = "", satz_null: bool = True):
    """Setzt den Steuerfall eines Belegs — der eine Aufruf für jeden Aufrufer, auch für andere Module:

        steuerfall_setzen(inv, "kleinunternehmer")
        steuerfall_setzen(inv, "steuerfrei", "Steuerfrei nach § 4 Nr. 21 UStG")
        steuerfall_setzen(inv, "reverse_charge")

    Trägt Kategorie (und mit satz_null=True den Satz 0) in alle Positionen und Belegnachlässe ein.
    Danach liefern compute_tax_subtotals(), der UBL- und der CII-Generator Kategorie und Befreiungsgrund."""
    inv.steuerfall = steuerfall_normalisieren(fall)
    inv.steuerbefreiung_grund = " ".join(str(grund or "").split())
    inv.steuerfall_anwenden(satz_null=satz_null)
    return inv


@dataclass
class AuditEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    user: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    old_value: str = ""
    new_value: str = ""
    comment: str = ""
    source: str = ""


@dataclass
class Invoice:
    invoice_number: str = ""
    invoice_date: date = field(default_factory=date.today)
    invoice_type_code: str = "380"
    currency_code: str = "EUR"
    buyer_reference: str = ""
    tax_point_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    note: str = ""
    order_reference: str = ""
    contract_reference: str = ""
    project_reference: str = ""
    preceding_invoice: str = ""
    # BT-26: Ausstellungsdatum der Bezugsrechnung (zu BT-25), 24.11.2026
    preceding_invoice_date: Optional[date] = None

    # Bereits geleistete Anzahlungen (BT-113). Die Schlussrechnung zog sie bisher
    # NUR im PDF ab, die XRechnung trug PrepaidAmount fest 0,00 und forderte den
    # vollen Betrag — wer die XRechnung maschinell verarbeitet, zahlte doppelt
    # (07.09.2026). Der Betrag ist brutto, wie BT-113 es verlangt.
    prepaid_amount: Decimal = Decimal("0.00")
    # Einzelnachweis dazu (Nummer, Bezeichnung, Datum, Brutto, Status) — steht
    # so im PDF unter "Bereits geleistete Anzahlungen". Ohne ihn muesste das PDF
    # den Betrag erraten und wuerde wieder von der XRechnung abweichen.
    prepaid_details: list = field(default_factory=list)

    # Steuerfall des Belegs (25.11.2026), siehe STEUERFAELLE. Altbestand ohne das Feld = „regel“.
    steuerfall: str = "regel"
    # Befreiungsgrund BT-120: eigener Text des Ausstellers bzw. der eingelesene Text eines Fremdbelegs.
    # Leer = Standardtext des Steuerfalls (Kleinunternehmer, § 13b).
    steuerbefreiung_grund: str = ""
    # Befreiungsgrund BT-121 (VATEX-Code); leer = Vorgabe des Steuerfalls (nur § 13b: VATEX-EU-AE)
    steuerbefreiung_code: str = ""

    seller: Seller = field(default_factory=Seller)
    buyer: Buyer = field(default_factory=Buyer)
    payment: PaymentInfo = field(default_factory=PaymentInfo)
    lines: list[InvoiceLine] = field(default_factory=list)
    allowances_charges: list[AllowanceCharge] = field(default_factory=list)

    # Workflow (Eingangsrechnung)
    status: str = InvoiceStatus.NEU.value
    assigned_to: str = ""
    audit_trail: list[AuditEvent] = field(default_factory=list)

    # Meta
    _id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _direction: str = ""  # EINGANG / AUSGANG
    _source_file: str = ""
    _source_format: str = ""
    _received_at: str = ""
    _sender_email: str = ""

    def sum_line_net(self) -> Decimal:
        return sum((l.line_net_amount for l in self.lines), Decimal("0.00"))

    def sum_allowances(self) -> Decimal:
        return sum((ac.amount for ac in self.allowances_charges if not ac.is_charge), Decimal("0.00"))

    def sum_charges(self) -> Decimal:
        return sum((ac.amount for ac in self.allowances_charges if ac.is_charge), Decimal("0.00"))

    def tax_exclusive_amount(self) -> Decimal:
        return (self.sum_line_net() - self.sum_allowances() + self.sum_charges()
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def compute_tax_subtotals(self) -> list[TaxSubtotal]:
        buckets: dict[tuple[str, Decimal], Decimal] = {}
        for line in self.lines:
            key = (line.tax_category, line.tax_rate)
            buckets[key] = buckets.get(key, Decimal("0.00")) + line.line_net_amount
        for ac in self.allowances_charges:
            key = (ac.tax_category, ac.tax_rate)
            adj = ac.amount if ac.is_charge else -ac.amount
            buckets[key] = buckets.get(key, Decimal("0.00")) + adj
        result = []
        for (cat, rate), taxable in sorted(buckets.items()):
            taxable = taxable.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax = (taxable * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            result.append(TaxSubtotal(taxable_amount=taxable, tax_amount=tax, category_code=cat, rate=rate,
                                      exemption_reason=self.befreiungsgrund_text(cat),
                                      exemption_reason_code=self.befreiungsgrund_code(cat)))
        return result

    # ── Steuerfall ──
    def steuerfall_kategorie(self) -> str:
        return STEUERFAELLE.get(self.steuerfall or "regel", STEUERFAELLE["regel"])["kategorie"]

    def steuerfall_anwenden(self, satz_null: bool = False) -> None:
        """Trägt die Kategorie des Steuerfalls in alle Positionen und Belegnachlässe/-zuschläge ein.
        Bei „regel“ bleibt alles, wie es ist. satz_null=True setzt zusätzlich den Steuersatz auf 0."""
        fall = self.steuerfall or "regel"
        if fall == "regel" or fall not in STEUERFAELLE:
            return
        kat = STEUERFAELLE[fall]["kategorie"]
        for x in list(self.lines) + list(self.allowances_charges):
            x.tax_category = kat
            if satz_null:
                x.tax_rate = Decimal("0")

    def befreiungsgrund_text(self, kategorie: str = None) -> str:
        """BT-120 für eine Kategorie; leer, wenn die Kategorie keinen Grund trägt (S, Z)."""
        kat = kategorie or self.steuerfall_kategorie()
        if kat not in KATEGORIEN_MIT_GRUND:
            return ""
        eigen = " ".join((self.steuerbefreiung_grund or "").split())
        fall = self.steuerfall or "regel"
        if fall in STEUERFAELLE and STEUERFAELLE[fall]["kategorie"] == kat:
            if fall == "reverse_charge":
                # Pflichtangabe nach § 14a Abs. 5 UStG steht immer da; ein eigener Zusatz wird angehängt
                if not eigen or "steuerschuldnerschaft" in eigen.lower():
                    return eigen or TEXT_REVERSE_CHARGE
                return TEXT_REVERSE_CHARGE + " – " + eigen
            return eigen or STEUERFAELLE[fall]["text"]
        return eigen

    def befreiungsgrund_code(self, kategorie: str = None) -> str:
        """BT-121 (VATEX) für eine Kategorie."""
        kat = kategorie or self.steuerfall_kategorie()
        if kat not in KATEGORIEN_MIT_GRUND:
            return ""
        eigen = (self.steuerbefreiung_code or "").strip()
        if eigen:
            return eigen
        fall = self.steuerfall or "regel"
        if fall in STEUERFAELLE and STEUERFAELLE[fall]["kategorie"] == kat:
            return STEUERFAELLE[fall]["code"]
        return ""

    def steuerhinweis(self) -> str:
        """Hinweissatz für das PDF: warum keine Umsatzsteuer berechnet wird. Leer bei Regelbesteuerung."""
        fall = self.steuerfall or "regel"
        if fall == "regel" or fall not in STEUERFAELLE:
            return ""
        if fall == "nullsatz":
            return " ".join((self.steuerbefreiung_grund or "").split()) or TEXT_NULLSATZ
        return self.befreiungsgrund_text(STEUERFAELLE[fall]["kategorie"])

    def tax_amount(self) -> Decimal:
        return sum((t.tax_amount for t in self.compute_tax_subtotals()), Decimal("0.00"))

    def tax_inclusive_amount(self) -> Decimal:
        return self.tax_exclusive_amount() + self.tax_amount()

    def amount_due(self, prepaid: Decimal = None, rounding: Decimal = Decimal("0.00")) -> Decimal:
        """Zahlbetrag (BT-115) = Bruttobetrag - Anzahlungen + Rundung.

        Ohne ausdruecklichen `prepaid`-Wert gilt das Feld der Rechnung. Damit
        stimmen PDF, XRechnung und offener Posten ueberein; frueher kannte nur
        das PDF die Anzahlungen.
        """
        if prepaid is None:
            prepaid = self.prepaid_amount or Decimal("0.00")
        return self.tax_inclusive_amount() - Decimal(str(prepaid)) + rounding

    def add_audit(self, event_type: str, user: str = "system", comment: str = "",
                  old_value: str = "", new_value: str = ""):
        self.audit_trail.append(AuditEvent(
            event_type=event_type, user=user, comment=comment,
            old_value=old_value, new_value=new_value))

    def to_dict(self) -> dict:
        def _ser(obj):
            if isinstance(obj, (date, datetime)): return obj.isoformat()
            if isinstance(obj, Decimal): return str(obj)
            if isinstance(obj, Enum): return obj.value
            raise TypeError(f"Cannot serialize {type(obj)}")
        import dataclasses
        return json.loads(json.dumps(dataclasses.asdict(self), default=_ser))
