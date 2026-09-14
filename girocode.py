"""
E-Rechnungssystem – EPC QR-Code (GiroCode) Generator

Erzeugt einen EPC-konformen QR-Code nach dem European Payments Council
Quick Response Code Standard (EPC069-12) für SEPA-Überweisungen.

Der GiroCode kann mit jeder Banking-App gescannt werden und füllt
automatisch alle Zahlungsdaten aus: Empfänger, IBAN, BIC, Betrag,
Verwendungszweck.

Referenz: https://www.europeanpaymentscouncil.eu/document-library/guidance-documents/quick-response-code-guidelines-enable-data-capture-initiation
"""
from __future__ import annotations
import io
import base64
from decimal import Decimal
from typing import Optional

from models import Invoice

try:
    import qrcode
    import qrcode.image.svg
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


def build_epc_payload(
    beneficiary_name: str,
    iban: str,
    amount: Decimal | float,
    bic: str = "",
    reference: str = "",
    text: str = "",
    currency: str = "EUR",
) -> str:
    """
    Erzeugt den EPC QR-Code Payload nach EPC069-12 v2.1.

    Format:
      BCD                         – Service Tag (fest)
      002                         – Version (002 = v2.1)
      1                           – Zeichensatz (1 = UTF-8)
      SCT                         – Identifikation (SEPA Credit Transfer)
      [BIC]                       – BIC des Begünstigten (optional seit 2.1)
      [Name]                      – Name des Begünstigten (max 70 Zeichen)
      [IBAN]                      – IBAN (max 34 Zeichen)
      EUR[Betrag]                 – Währung + Betrag (max 12 Stellen)
      [Verwendungszweckcode]      – Purpose (4 Zeichen, optional)
      [Referenz]                  – Structured Reference (max 35 Zeichen)
      [Text]                      – Unstrukturierter Text (max 140 Zeichen)
      [Info]                      – Nutzerinformation (optional)

    Entweder Referenz ODER Text darf gefüllt sein, nicht beides.
    """
    # Bereinigung
    iban = iban.replace(" ", "").upper()
    bic = (bic or "").replace(" ", "").upper()
    name = beneficiary_name[:70]
    amt = f"{currency}{float(amount):.2f}"

    # Referenz vs. Freitext: EPC erlaubt nur eins
    ref = ""
    txt = ""
    if reference:
        ref = reference[:35]
    else:
        txt = text[:140] if text else ""

    lines = [
        "BCD",       # Service Tag
        "002",       # Version
        "1",         # Charset (UTF-8)
        "SCT",       # Identification
        bic,         # BIC
        name,        # Beneficiary
        iban,        # IBAN
        amt,         # Amount
        "",          # Purpose
        ref,         # Structured Reference
        txt,         # Unstructured Text
        "",          # Beneficiary to Originator Info
    ]

    return "\n".join(lines)


def build_epc_from_invoice(inv: Invoice) -> str:
    """Erzeugt den EPC-Payload aus einem Rechnungsobjekt."""
    # Betrag: Zahlbetrag (amount_due)
    amount = inv.amount_due()

    # Nur positive Beträge (keine Gutschriften)
    if amount <= 0:
        return ""

    # Nur EUR unterstützt von EPC
    if inv.currency_code != "EUR":
        return ""

    # IBAN erforderlich
    iban = inv.payment.iban or ""
    if not iban:
        return ""

    # Verwendungszweck
    text = f"Rechnung {inv.invoice_number}"
    if inv.buyer_reference:
        text += f" Ref: {inv.buyer_reference}"

    return build_epc_payload(
        beneficiary_name=inv.seller.name,
        iban=iban,
        amount=amount,
        bic=inv.payment.bic or "",
        text=text,
        currency=inv.currency_code,
    )


def generate_qr_svg(data: str, box_size: int = 8, border: int = 2) -> str:
    """Erzeugt einen QR-Code als SVG-String."""
    if not HAS_QRCODE or not data:
        return ""

    qr = qrcode.QRCode(
        version=None,  # Auto-Size
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)

    # SVG erzeugen
    factory = qrcode.image.svg.SvgPathImage
    img = qr.make_image(image_factory=factory)

    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def generate_qr_data_uri(data: str, box_size: int = 8, border: int = 2) -> str:
    """Erzeugt einen QR-Code als data:URI (SVG, base64-codiert)."""
    svg = generate_qr_svg(data, box_size, border)
    if not svg:
        return ""
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def generate_invoice_qr_svg(inv: Invoice, box_size: int = 8) -> str:
    """Erzeugt den GiroCode-QR als SVG für eine Rechnung."""
    payload = build_epc_from_invoice(inv)
    return generate_qr_svg(payload, box_size) if payload else ""


def generate_invoice_qr_data_uri(inv: Invoice, box_size: int = 8) -> str:
    """Erzeugt den GiroCode-QR als data:URI für eine Rechnung."""
    payload = build_epc_from_invoice(inv)
    return generate_qr_data_uri(payload, box_size) if payload else ""


def get_qr_info(inv: Invoice) -> dict:
    """Gibt QR-Metadaten für die API zurück."""
    payload = build_epc_from_invoice(inv)
    if not payload:
        return {"available": False, "reason": _get_unavailable_reason(inv)}

    return {
        "available": True,
        "type": "EPC/GiroCode",
        "version": "002 (v2.1)",
        "standard": "EPC069-12",
        "beneficiary": inv.seller.name,
        "iban": inv.payment.iban,
        "bic": inv.payment.bic or "",
        "amount": float(inv.amount_due()),
        "currency": inv.currency_code,
        "payload_preview": payload[:80] + "...",
        "data_uri": generate_qr_data_uri(payload),
    }


def _get_unavailable_reason(inv: Invoice) -> str:
    if inv.amount_due() <= 0:
        return "Kein positiver Zahlbetrag (Gutschrift)"
    if inv.currency_code != "EUR":
        return f"Währung {inv.currency_code} nicht unterstützt (nur EUR)"
    if not inv.payment.iban:
        return "Keine IBAN vorhanden"
    return "Unbekannt"


# ── Demo ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test mit Beispieldaten
    payload = build_epc_payload(
        beneficiary_name="Muster GmbH",
        iban="DE89370400440532013000",
        amount=1487.50,
        bic="COBADEFFXXX",
        text="Rechnung RE-2026-0001",
    )
    print("EPC Payload:")
    print(payload)
    print(f"\nPayload-Länge: {len(payload)} Zeichen")

    if HAS_QRCODE:
        svg = generate_qr_svg(payload)
        print(f"SVG-Größe: {len(svg)} Bytes")

        uri = generate_qr_data_uri(payload)
        print(f"data:URI-Länge: {len(uri)} Zeichen")
        print(f"data:URI Prefix: {uri[:50]}...")
        print("\n✓ QR-Code erfolgreich erzeugt")
    else:
        print("\n⚠ qrcode-Bibliothek nicht installiert")
