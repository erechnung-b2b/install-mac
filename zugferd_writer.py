"""
ZUGFeRD PDF/A-3 Writer — erzeugt hybride Rechnungs-PDFs mit eingebettetem XML.

Ergänzt zugferd.py (Leser) um den Schreibpfad (P-03).
Nutzt reportlab für die sichtbare Seite und pikepdf zum Einbetten des XML
als PDF/A-3-Attachment mit AFRelationship=Data (ZUGFeRD-konform).

Hinweis: Für strenge PDF/A-3b-Konformität (XMP-Metadaten, OutputIntent,
ICC-Profil) wäre zusätzlich ein Postprocessing mit veraPDF oder
qpdf+Ghostscript sinnvoll. Diese Implementierung erzeugt ein
ZUGFeRD-2.x-kompatibles Hybrid-PDF, das von üblichen Tools gelesen
werden kann — für formal vollständige PDF/A-3b-Zertifizierung muss
der Output durch einen Konverter (z.B. Ghostscript) geschickt werden.
"""
from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Union

import pikepdf
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from models import Invoice


# ── Sichtbaren PDF-Teil erzeugen ───────────────────────────────────────


def _build_visible_pdf(inv: Invoice) -> bytes:
    """Rendert die Rechnung als A4-PDF mit reportlab."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"Rechnung {inv.invoice_number}",
        author=inv.seller.name,
        subject="Elektronische Rechnung (ZUGFeRD)",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18,
                        textColor=colors.HexColor("#2c5f7c"))
    normal = styles["Normal"]
    small = ParagraphStyle("small", parent=normal, fontSize=8,
                           textColor=colors.grey)

    story = []

    # Kopf: Verkäufer
    s = inv.seller
    seller_html = (
        f"<b>{s.name}</b><br/>"
        f"{s.address.street}<br/>"
        f"{s.address.post_code} {s.address.city}<br/>"
        f"{s.contact.email or s.electronic_address}"
    )
    story.append(Paragraph(seller_html, small))
    story.append(Spacer(1, 8 * mm))

    # Titel
    story.append(Paragraph("Rechnung", h1))
    story.append(Spacer(1, 4 * mm))

    # Meta-Tabelle
    meta = [
        ["Rechnungsnummer:", inv.invoice_number],
        ["Rechnungsdatum:", inv.invoice_date.strftime("%d.%m.%Y")
         if hasattr(inv.invoice_date, "strftime") else str(inv.invoice_date)],
        ["Kundennummer:", inv.buyer_reference or "–"],
        ["Währung:", inv.currency_code],
    ]
    if inv.seller.tax_registration_id:
        meta.append(["Steuernummer:", inv.seller.tax_registration_id])
    t_meta = Table(meta, colWidths=[45 * mm, 80 * mm])
    t_meta.setStyle(TableStyle([
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("FONT", (1, 0), (1, -1), "Helvetica", 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 6 * mm))

    # Empfänger
    b = inv.buyer
    buyer_html = (
        f"<b>Rechnungsempfänger</b><br/>"
        f"{b.name}<br/>"
        f"{b.address.street}<br/>"
        f"{b.address.post_code} {b.address.city}"
    )
    story.append(Paragraph(buyer_html, normal))
    story.append(Spacer(1, 8 * mm))

    # Positionen
    data = [["Pos", "Bezeichnung", "Menge", "Einzelpreis", "Summe"]]
    total_net = Decimal("0.00")
    for i, l in enumerate(inv.lines, start=1):
        data.append([
            str(i),
            l.item_name,
            f"{l.quantity}",
            f"{l.unit_price:.2f} €",
            f"{l.line_net_amount:.2f} €",
        ])
        total_net += l.line_net_amount

    tax_rate = inv.lines[0].tax_rate if inv.lines else Decimal("19.00")
    tax_amount = (total_net * tax_rate / Decimal("100")).quantize(Decimal("0.01"))
    total_gross = total_net + tax_amount

    data.append(["", "", "", "Zwischensumme netto:", f"{total_net:.2f} €"])
    data.append(["", "", "", f"USt {tax_rate}%:", f"{tax_amount:.2f} €"])
    data.append(["", "", "", "Gesamtbetrag brutto:", f"{total_gross:.2f} €"])

    t = Table(data, colWidths=[12 * mm, 75 * mm, 20 * mm, 30 * mm, 30 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c5f7c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -4), "Helvetica", 9),
        ("FONT", (3, -3), (-1, -1), "Helvetica-Bold", 9),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -4), 0.3, colors.grey),
        ("LINEABOVE", (3, -3), (-1, -3), 0.5, colors.black),
        ("LINEABOVE", (3, -1), (-1, -1), 1.2, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 8 * mm))

    # Zahlungsinfo
    p = inv.payment
    pay_lines = [
        f"<b>Zahlungsbedingungen:</b> {p.payment_terms or 'sofort'}",
        f"<b>Bankverbindung:</b> {p.bank_name}" if p.bank_name else "",
        f"<b>IBAN:</b> {p.iban}" if p.iban else "",
        f"<b>BIC:</b> {p.bic}" if p.bic else "",
    ]
    pay_html = "<br/>".join(line for line in pay_lines if line)
    story.append(Paragraph(pay_html, normal))
    story.append(Spacer(1, 6 * mm))

    # Hybrid-Hinweis
    story.append(Paragraph(
        "Diese PDF enthält eine eingebettete ZUGFeRD-XML-Datei mit den "
        "strukturierten Rechnungsdaten (EN 16931). Der strukturierte Teil "
        "ist gemäß § 14 UStG führend.",
        small,
    ))

    doc.build(story)
    return buf.getvalue()


# ── XML als PDF/A-3-Attachment einbetten ───────────────────────────────


def _embed_xml_in_pdf(pdf_bytes: bytes, xml_bytes: bytes,
                     filename: str = "factur-x.xml") -> bytes:
    """
    Bettet XML als PDF-Attachment mit AFRelationship=Data ein.
    Das ist der Kern der ZUGFeRD-Spezifikation: AFRelationship=Data
    signalisiert, dass der XML-Anhang der maßgebliche strukturierte
    Datenteil der Rechnung ist.
    """
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        now_str = datetime.now().strftime("D:%Y%m%d%H%M%S")
        attachment = pikepdf.AttachedFileSpec(
            pdf,
            xml_bytes,
            description="ZUGFeRD Invoice XML (EN 16931)",
            mime_type="application/xml",
            relationship=pikepdf.Name("/Data"),  # ← ZUGFeRD-konform
            creation_date=now_str,
            mod_date=now_str,
        )
        pdf.attachments[filename] = attachment

        # AFRelationship zusätzlich auf Katalog-Ebene als /AF-Array setzen
        # (ZUGFeRD-Spezifikation verlangt das für strukturelle Auffindbarkeit)
        try:
            af_obj = pdf.attachments[filename].obj
            if "/AF" not in pdf.Root:
                pdf.Root.AF = pikepdf.Array([af_obj])
            else:
                pdf.Root.AF.append(af_obj)
        except Exception:
            pass  # Nicht fatal, Attachment ist trotzdem da

        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


# ── Öffentliche API ────────────────────────────────────────────────────


def generate_zugferd_pdf(inv: Invoice, xml_bytes: bytes) -> bytes:
    """
    Erzeugt ein ZUGFeRD-Hybrid-PDF (sichtbar + eingebettetes XML).

    Args:
        inv: Invoice-Objekt für die sichtbare Darstellung
        xml_bytes: Das XRechnung-XML, das eingebettet werden soll
                   (erzeugt über xrechnung_generator.generate_and_serialize)

    Returns:
        PDF-Bytes mit eingebettetem XML
    """
    visible_pdf = _build_visible_pdf(inv)
    return _embed_xml_in_pdf(visible_pdf, xml_bytes, filename="factur-x.xml")


def write_zugferd_pdf(inv: Invoice, xml_bytes: bytes,
                     output_path: Union[str, Path]) -> Path:
    """Bequemer Wrapper: erzeugt PDF und schreibt es auf Platte."""
    pdf_bytes = generate_zugferd_pdf(inv, xml_bytes)
    p = Path(output_path)
    p.write_bytes(pdf_bytes)
    return p
