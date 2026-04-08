"""
PDF-Import für Ausgangsrechnungen im Format 'Energieberatung Rolf Krause'.

Parst das bekannte Excel-basierte Rechnungs-PDF und erzeugt ein
models.Invoice-Objekt, das direkt in den bestehenden erechnung-Flow
(xrechnung_generator, archive, persistence, webapp) eingespeist werden kann.

Referenzen:
- Pflichtenheft FR-700 (Ausgangsrechnungen strukturiert erzeugen)
- erechnung-erstellen.txt Abschnitt 6 (fachliches Datenmodell)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Union

import pdfplumber

from models import (
    Address,
    Buyer,
    Contact,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    PaymentInfo,
    Seller,
    TaxSubtotal,
)


# ── Seller-Stammdaten aus Briefkopf ───────────────────────────────────
# Werden verwendet, falls in mandant_settings.json noch nichts gepflegt ist.
_SELLER_FALLBACK = dict(
    name="Energieberatung Rolf Krause",
    street="Ginsterweg 2a",
    post_code="41379",
    city="Brüggen",
    country="DE",
    email="energieberatung@rolfkrause.com",
    telephone="+49 176 22872775",
    iban="DE62334500000034417394",
    bic="",  # aus mandant_settings.json übernehmen, wenn gepflegt
    bank_name="Sparkasse-HRV",
    contact_name="Rolf Krause",
)


# ── Kleine Helfer ──────────────────────────────────────────────────────

def _dec(s: str) -> Decimal:
    """'470,59 €' -> Decimal('470.59'). Leere Strings -> 0."""
    if not s:
        return Decimal("0.00")
    # pdfplumber fügt mitunter Leerzeichen zwischen Ziffern ein ('2 9,41')
    cleaned = s.replace("€", "").replace(" ", "").replace("\u00a0", "")
    cleaned = cleaned.replace(".", "").replace(",", ".").strip()
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if not cleaned:
        return Decimal("0.00")
    return Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_german_date(s: str) -> date:
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unbekanntes Datumsformat: {s!r}")


# ── Öffentliche API ────────────────────────────────────────────────────

def parse_energieberatung_pdf(
    pdf_path: Union[str, Path],
    seller_overrides: dict | None = None,
) -> Invoice:
    """
    Parst eine Energieberatungs-Rechnung und liefert ein vollständiges
    models.Invoice-Objekt mit _direction='AUSGANG'.

    Wirft ValueError bei fehlenden Pflichtfeldern oder inkonsistenten Summen.
    """
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise ValueError("PDF enthält keine Seiten")
        text = pdf.pages[0].extract_text() or ""

    # --- Kopfdaten ---
    m_num = re.search(r"Rechnungsnr\.?:\s*(.+?)(?:\s+Steu|\n|$)", text)
    m_date = re.search(r"Datum:\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", text)
    m_cust = re.search(r"Kundennummer:\s*(\S+)", text)
    m_tax = re.search(r"Steu[er]*nummer:\s*([\d/]+)", text)

    if not (m_num and m_date):
        raise ValueError("PDF-Import: Rechnungsnummer oder Datum nicht erkannt")

    invoice_number = re.sub(r"\s+", "", m_num.group(1).strip())
    issue_date = _parse_german_date(m_date.group(1))
    customer_number = m_cust.group(1).strip() if m_cust else ""
    seller_tax_number = m_tax.group(1).strip() if m_tax else ""

    # --- Käufer, Positionen, Summen ---
    buyer = _extract_buyer(text)
    lines = _extract_lines(text)

    m_sub = re.search(r"Zwischensumme\s+([\d\s.,]+?)\s*€", text)
    m_rate = re.search(r"USt\s*%\s+([\d\s,]+?)\s*%", text)
    m_tax_amt = re.search(r"Ust\s+([\d\s.,]+?)\s*€", text)
    m_total = re.search(r"total\s+([\d\s.,]+?)\s*€", text)

    subtotal = _dec(m_sub.group(1)) if m_sub else Decimal("0.00")
    tax_rate = _dec(m_rate.group(1)) if m_rate else Decimal("19.00")
    tax_amount = _dec(m_tax_amt.group(1)) if m_tax_amt else Decimal("0.00")
    total = _dec(m_total.group(1)) if m_total else Decimal("0.00")

    active_lines = [l for l in lines if l.quantity > 0]

    # --- Plausibilitätsprüfung ---
    _validate_parsed(invoice_number, buyer, active_lines, subtotal, tax_rate, tax_amount)

    # --- In models.Invoice überführen ---
    sd = {**_SELLER_FALLBACK, **(seller_overrides or {})}

    seller = Seller(
        name=sd["name"],
        address=Address(
            street=sd["street"],
            city=sd["city"],
            post_code=sd["post_code"],
            country_code=sd["country"],
        ),
        electronic_address=sd["email"],
        electronic_address_scheme="EM",
        contact=Contact(
            name=sd["contact_name"],
            telephone=sd["telephone"],
            email=sd["email"],
        ),
        tax_registration_id=seller_tax_number,
        registration_name=sd["name"],
    )

    payment = PaymentInfo(
        means_code="30",  # Überweisung
        payment_terms="sofort",
        iban=sd["iban"],
        bic=sd.get("bic", ""),
        bank_name=sd["bank_name"],
    )

    # Positions-Lines in models.InvoiceLine-Format
    mdl_lines: list[InvoiceLine] = []
    for i, l in enumerate(active_lines, start=1):
        mdl_lines.append(InvoiceLine(
            line_id=str(i),
            quantity=l.quantity,
            unit_code="C62",  # stk (UN/ECE Rec 20)
            line_net_amount=l.line_total,
            item_name=l.description,
            unit_price=l.unit_price,
            tax_category="S",
            tax_rate=tax_rate,
        ))

    inv = Invoice(
        invoice_number=invoice_number,
        invoice_date=issue_date,
        invoice_type_code="380",
        currency_code="EUR",
        buyer_reference=customer_number or "N/A",  # BT-10 Pflicht
        seller=seller,
        buyer=buyer,
        payment=payment,
        lines=mdl_lines,
        status=InvoiceStatus.FREIGEGEBEN.value,  # Ausgang: direkt freigabefertig
    )
    inv._direction = "AUSGANG"
    inv._source_file = pdf_path.name
    inv._source_format = "PDF-Energieberatung"
    inv._received_at = datetime.now().isoformat()

    return inv


# ── interne Extraktoren ────────────────────────────────────────────────

def _extract_buyer(text: str) -> Buyer:
    """Käuferblock: 'Herr/Frau/Firma <Name>' + Straße + 'PLZ Ort'."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name = street = plz = city = ""

    for i, line in enumerate(lines):
        if re.match(r"^(Herr|Frau|Firma)\s+", line):
            # Metadaten ('Rechnungsnr.:', 'Steuernummer:' etc.) abschneiden,
            # die pdfplumber auf derselben Zeile ausgibt.
            name = re.split(r"\s+(Rechnungsnr|Steu[er]*nummer|Kundennummer|Datum)", line)[0].strip()
            rest = [
                l for l in lines[i + 1 : i + 8]
                if not re.match(r"^(Rechnungsnr|Steu[er]*nummer|Anzahl|Datum|Kunden|Rechnung\s+für)", l)
            ]
            _clip = lambda s: re.split(r"\s+(Rechnungsnr|Steu[er]*nummer|Kundennummer|Datum)", s)[0].strip()
            for r in rest:
                m = re.match(r"^(\d{5})\s+(.+)$", r)
                if m and not plz:
                    plz, city = m.group(1), _clip(m.group(2))
                    break
                elif not street:
                    street = _clip(r)
            break

    return Buyer(
        name=name or "Unbekannt",
        address=Address(street=street, city=city, post_code=plz, country_code="DE"),
        electronic_address="buyer@unknown.local",  # Platzhalter BT-49
        electronic_address_scheme="EM",
        buyer_reference="N/A",
    )


class _RawLine:
    __slots__ = ("quantity", "description", "unit_price", "line_total")

    def __init__(self, qty, desc, unit, total):
        self.quantity = qty
        self.description = desc
        self.unit_price = unit
        self.line_total = total


def _extract_lines(text: str) -> list[_RawLine]:
    """
    Findet Positionszeilen im Muster:
      '0 Wärmeschutznachweis Neubau WG 470,59 € 0,00 €'
      '1 Verbrauchsausweis 29,41 € 29,41 €'
    """
    pattern = re.compile(
        r"^\s*(\d+)\s+(.+?)\s+([\d.,]+)\s*€\s+([\d.,]+)\s*€\s*$"
    )
    result: list[_RawLine] = []
    for raw in text.splitlines():
        m = pattern.match(raw)
        if not m:
            continue
        qty, desc, unit, total = m.groups()
        if re.search(r"(Zwischensumme|USt|Ust|total)", desc, re.IGNORECASE):
            continue
        result.append(_RawLine(
            _dec(qty), desc.strip(), _dec(unit), _dec(total)
        ))
    return result


def _validate_parsed(
    invoice_number: str,
    buyer: Buyer,
    lines: list,
    subtotal: Decimal,
    tax_rate: Decimal,
    tax_amount: Decimal,
) -> None:
    errors = []
    if not invoice_number:
        errors.append("Rechnungsnummer fehlt")
    if not lines:
        errors.append("Keine aktiven Positionen (Menge > 0) gefunden")
    if not buyer.name or buyer.name == "Unbekannt":
        errors.append("Käufer konnte nicht erkannt werden")

    calc_sub = sum((l.line_total for l in lines), Decimal("0.00"))
    if abs(calc_sub - subtotal) > Decimal("0.02"):
        errors.append(f"Zwischensumme inkonsistent: berechnet {calc_sub}, PDF {subtotal}")

    calc_tax = (subtotal * tax_rate / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if abs(calc_tax - tax_amount) > Decimal("0.02"):
        errors.append(f"USt-Betrag inkonsistent: berechnet {calc_tax}, PDF {tax_amount}")

    if errors:
        raise ValueError("PDF-Import fehlgeschlagen: " + "; ".join(errors))
