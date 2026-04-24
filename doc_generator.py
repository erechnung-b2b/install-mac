"""
E-Rechnungssystem – PDF-Dokumenterzeugung (Phase 2)
Erzeugt PDF-Dokumente für alle Workflow-Stufen:
Angebotsanfrage, Bestellung, Kundenangebot, Auftragsbestätigung,
Lieferschein, Mahnung.
Gemeinsames Layout mit Firmen-Briefkopf, Positionen, Summen.
"""
from __future__ import annotations
import os, json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, Image, HRFlowable, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Konstanten ───────────────────────────────────────────────────────

PAGE_W, PAGE_H = A4  # 595.28 x 841.89 pt
MARGIN_L = 25 * mm
MARGIN_R = 20 * mm
MARGIN_T = 20 * mm
MARGIN_B = 25 * mm

# Brand-Farben (energieberatung rolf krause)
DEEP_BLUE = colors.HexColor("#1B4B82")
CYAN = colors.HexColor("#00A0D2")
DARK_GRAY = colors.HexColor("#1E293B")
MID_GRAY = colors.HexColor("#64748B")
LIGHT_GRAY = colors.HexColor("#F5F7FA")
BORDER_GRAY = colors.HexColor("#D1D5DB")
WHITE = colors.white

DOC_TYPE_TITLES = {
    "supplier_quote":  "Angebotsanfrage",
    "purchase_order":  "Bestellung",
    "supplier_invoice": "Eingangsrechnung",
    "customer_quote":  "Angebot",
    "order_intake":    "Auftragsbestätigung",
    "delivery_note":   "Lieferschein",
    "invoice":         "Rechnung",
    "dunning_1":       "Zahlungserinnerung",
    "dunning_2":       "1. Mahnung",
    "dunning_3":       "2. Mahnung – Letzte Aufforderung",
}


# ── Styles ───────────────────────────────────────────────────────────

def _build_styles():
    ss = getSampleStyleSheet()
    styles = {}

    styles["header_name"] = ParagraphStyle(
        "header_name", parent=ss["Normal"],
        fontSize=11, textColor=DEEP_BLUE, leading=14, fontName="Helvetica-Bold")

    styles["header_sub"] = ParagraphStyle(
        "header_sub", parent=ss["Normal"],
        fontSize=8, textColor=MID_GRAY, leading=10)

    styles["sender_line"] = ParagraphStyle(
        "sender_line", parent=ss["Normal"],
        fontSize=6.5, textColor=MID_GRAY, leading=8)

    styles["recipient"] = ParagraphStyle(
        "recipient", parent=ss["Normal"],
        fontSize=10, textColor=DARK_GRAY, leading=13)

    styles["doc_title"] = ParagraphStyle(
        "doc_title", parent=ss["Normal"],
        fontSize=16, textColor=DEEP_BLUE, leading=20, fontName="Helvetica-Bold",
        spaceAfter=2*mm)

    styles["doc_meta"] = ParagraphStyle(
        "doc_meta", parent=ss["Normal"],
        fontSize=9, textColor=MID_GRAY, leading=12)

    styles["body"] = ParagraphStyle(
        "body", parent=ss["Normal"],
        fontSize=9.5, textColor=DARK_GRAY, leading=13)

    styles["body_bold"] = ParagraphStyle(
        "body_bold", parent=ss["Normal"],
        fontSize=9.5, textColor=DARK_GRAY, leading=13, fontName="Helvetica-Bold")

    styles["small"] = ParagraphStyle(
        "small", parent=ss["Normal"],
        fontSize=8, textColor=MID_GRAY, leading=10)

    styles["footer"] = ParagraphStyle(
        "footer", parent=ss["Normal"],
        fontSize=7, textColor=MID_GRAY, leading=9, alignment=TA_CENTER)

    styles["table_header"] = ParagraphStyle(
        "table_header", parent=ss["Normal"],
        fontSize=8, textColor=WHITE, leading=10, fontName="Helvetica-Bold")

    styles["table_cell"] = ParagraphStyle(
        "table_cell", parent=ss["Normal"],
        fontSize=8.5, textColor=DARK_GRAY, leading=11)

    styles["table_cell_r"] = ParagraphStyle(
        "table_cell_r", parent=ss["Normal"],
        fontSize=8.5, textColor=DARK_GRAY, leading=11, alignment=TA_RIGHT)

    styles["total_label"] = ParagraphStyle(
        "total_label", parent=ss["Normal"],
        fontSize=9, textColor=DARK_GRAY, leading=12, alignment=TA_RIGHT)

    styles["total_value"] = ParagraphStyle(
        "total_value", parent=ss["Normal"],
        fontSize=9, textColor=DARK_GRAY, leading=12, alignment=TA_RIGHT,
        fontName="Helvetica-Bold")

    styles["grand_total"] = ParagraphStyle(
        "grand_total", parent=ss["Normal"],
        fontSize=11, textColor=DEEP_BLUE, leading=14, alignment=TA_RIGHT,
        fontName="Helvetica-Bold")

    return styles


# ── Hilfsfunktionen ──────────────────────────────────────────────────

def _fmt(n):
    """Deutsche Zahlenformatierung."""
    if n is None:
        return "–"
    return f"{float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _date_de(d):
    """ISO-Datum → dd.mm.yyyy."""
    if not d:
        return ""
    if isinstance(d, str):
        d = d[:10]
        try:
            d = date.fromisoformat(d)
        except Exception:
            return d
    return d.strftime("%d.%m.%Y")


def _find_logo(data_dir: Path) -> Optional[str]:
    """Sucht Logo in verschiedenen Pfaden."""
    for name in ("Logo.jpg", "logo.jpg", "Logo.png", "logo.png"):
        for base in [data_dir, data_dir.parent, data_dir / "data"]:
            p = base / name
            if p.exists():
                return str(p)
    return None


# ── Briefkopf ────────────────────────────────────────────────────────

def _build_header(story, styles, seller: dict, logo_path: Optional[str]):
    """Erzeugt den Briefkopf mit Logo und Firmendaten."""
    # Logo + Firmeninfo nebeneinander
    header_data = []

    logo_cell = ""
    if logo_path and os.path.exists(logo_path):
        try:
            logo_cell = Image(logo_path, width=22*mm, height=22*mm,
                              kind="proportional")
        except Exception:
            logo_cell = ""

    name = seller.get("name", "")
    street = seller.get("street", "")
    plz_city = f'{seller.get("post_code", "")} {seller.get("city", "")}'.strip()
    email = seller.get("email", "")
    phone = seller.get("contact_phone", "")
    vat = seller.get("vat_id", "")
    contact = seller.get("contact_name", "")

    info_lines = [f'<font size="11"><b>{name}</b></font>']
    if contact:
        info_lines.append(contact)
    if street:
        info_lines.append(street)
    if plz_city:
        info_lines.append(plz_city)
    detail_parts = []
    if phone:
        detail_parts.append(f"Tel: {phone}")
    if email:
        detail_parts.append(email)
    if vat:
        detail_parts.append(f"USt-ID: {vat}")
    if detail_parts:
        info_lines.append(" · ".join(detail_parts))

    info_para = Paragraph("<br/>".join(info_lines), styles["header_sub"])

    header_table = Table(
        [[logo_cell, info_para]],
        colWidths=[28*mm, PAGE_W - MARGIN_L - MARGIN_R - 28*mm],
        rowHeights=[28*mm]
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)

    # Trennlinie
    story.append(Spacer(1, 2*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=CYAN,
                             spaceAfter=3*mm))


def _build_address_block(story, styles, seller: dict, recipient: dict):
    """Absenderzeile + Empfängeradresse."""
    # Absenderzeile (klein, über Empfänger)
    s_name = seller.get("name", "")
    s_street = seller.get("street", "")
    s_plz = seller.get("post_code", "")
    s_city = seller.get("city", "")
    sender_line = f"{s_name} · {s_street} · {s_plz} {s_city}"
    story.append(Paragraph(sender_line, styles["sender_line"]))
    story.append(Spacer(1, 2*mm))

    # Empfänger
    lines = []
    if recipient.get("name"):
        lines.append(f'<b>{recipient["name"]}</b>')
    if recipient.get("contact_name"):
        lines.append(recipient["contact_name"])
    if recipient.get("street"):
        lines.append(recipient["street"])
    plz_city = f'{recipient.get("post_code", "")} {recipient.get("city", "")}'.strip()
    if plz_city:
        lines.append(plz_city)

    story.append(Paragraph("<br/>".join(lines), styles["recipient"]))
    story.append(Spacer(1, 12*mm))


# ── Positionen-Tabelle ───────────────────────────────────────────────

def _build_positions_table(story, styles, positions: list, show_prices: bool = True):
    """Erzeugt die Positionstabelle."""
    if not positions:
        story.append(Paragraph("<i>Keine Positionen</i>", styles["body"]))
        return

    usable_w = PAGE_W - MARGIN_L - MARGIN_R

    if show_prices:
        col_widths = [12*mm, None, 18*mm, 14*mm, 22*mm, 15*mm, 24*mm]
        # None = Rest
        rest = usable_w - sum(w for w in col_widths if w)
        col_widths[1] = rest

        header = [
            Paragraph("Pos", styles["table_header"]),
            Paragraph("Beschreibung", styles["table_header"]),
            Paragraph("Menge", styles["table_header"]),
            Paragraph("Einh.", styles["table_header"]),
            Paragraph("Einzelpreis", styles["table_header"]),
            Paragraph("Rab.%", styles["table_header"]),
            Paragraph("Netto", styles["table_header"]),
        ]
        data = [header]

        for p in positions:
            data.append([
                Paragraph(str(p.get("pos_nr", "")), styles["table_cell"]),
                Paragraph(str(p.get("description", "")), styles["table_cell"]),
                Paragraph(str(p.get("quantity", "")), styles["table_cell_r"]),
                Paragraph(str(p.get("unit", "Stk")), styles["table_cell"]),
                Paragraph(_fmt(p.get("unit_price", 0)), styles["table_cell_r"]),
                Paragraph(f'{p.get("discount_percent", 0):.0f}' if p.get("discount_percent") else "",
                          styles["table_cell_r"]),
                Paragraph(_fmt(p.get("net_amount", 0)), styles["table_cell_r"]),
            ])
    else:
        # Lieferschein: ohne Preise
        col_widths = [12*mm, None, 20*mm, 16*mm]
        rest = usable_w - sum(w for w in col_widths if w)
        col_widths[1] = rest

        header = [
            Paragraph("Pos", styles["table_header"]),
            Paragraph("Beschreibung", styles["table_header"]),
            Paragraph("Menge", styles["table_header"]),
            Paragraph("Einheit", styles["table_header"]),
        ]
        data = [header]
        for p in positions:
            data.append([
                Paragraph(str(p.get("pos_nr", "")), styles["table_cell"]),
                Paragraph(str(p.get("description", "")), styles["table_cell"]),
                Paragraph(str(p.get("quantity", "")), styles["table_cell_r"]),
                Paragraph(str(p.get("unit", "Stk")), styles["table_cell"]),
            ])

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl_style = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), DEEP_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        # Zeilen
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, BORDER_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    # Zebra
    for i in range(1, len(data)):
        if i % 2 == 0:
            tbl_style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GRAY))

    tbl.setStyle(TableStyle(tbl_style))
    story.append(tbl)


# ── Summenblock ──────────────────────────────────────────────────────

def _build_totals(story, styles, positions: list, step: dict):
    """Netto, Dokumentrabatt/-zuschlag, USt, Brutto."""
    net = sum(p.get("net_amount", 0) for p in positions)
    doc_disc = step.get("doc_discount_amount", 0)
    doc_surcharge = step.get("doc_surcharge_amount", 0)
    subtotal = net - doc_disc + doc_surcharge

    # USt berechnen (vereinfacht: ein Satz)
    tax_rate = 19
    if positions:
        tax_rate = positions[0].get("tax_rate", 19)
    tax = round(subtotal * tax_rate / 100, 2)
    gross = round(subtotal + tax, 2)

    usable_w = PAGE_W - MARGIN_L - MARGIN_R

    story.append(Spacer(1, 4*mm))

    rows = []
    rows.append([
        Paragraph("Netto:", styles["total_label"]),
        Paragraph(f"{_fmt(net)} €", styles["total_value"]),
    ])
    if doc_disc:
        rows.append([
            Paragraph("Nachlass:", styles["total_label"]),
            Paragraph(f"– {_fmt(doc_disc)} €", styles["total_value"]),
        ])
    if doc_surcharge:
        rows.append([
            Paragraph("Zuschlag:", styles["total_label"]),
            Paragraph(f"+ {_fmt(doc_surcharge)} €", styles["total_value"]),
        ])
    if doc_disc or doc_surcharge:
        rows.append([
            Paragraph("Zwischensumme:", styles["total_label"]),
            Paragraph(f"{_fmt(subtotal)} €", styles["total_value"]),
        ])
    rows.append([
        Paragraph(f"USt {tax_rate}%:", styles["total_label"]),
        Paragraph(f"{_fmt(tax)} €", styles["total_value"]),
    ])
    rows.append([
        Paragraph("Gesamtbetrag:", styles["total_label"]),
        Paragraph(f"{_fmt(gross)} €", styles["grand_total"]),
    ])

    totals_table = Table(rows, colWidths=[usable_w - 40*mm, 40*mm])
    totals_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LINEABOVE", (0, -1), (-1, -1), 1, DEEP_BLUE),
    ]))
    story.append(totals_table)

    return {"net": net, "tax": tax, "gross": gross, "tax_rate": tax_rate}


# ── Bankdaten / Zahlungsinfo ─────────────────────────────────────────

def _build_payment_info(story, styles, seller: dict, gross: float = 0,
                        due_date: str = "", reference: str = ""):
    """IBAN, BIC, Zahlungsziel."""
    story.append(Spacer(1, 6*mm))

    parts = []
    iban = seller.get("iban", "")
    bic = seller.get("bic", "")
    if iban:
        parts.append(f"<b>IBAN:</b> {iban}")
    if bic:
        parts.append(f"<b>BIC:</b> {bic}")
    if due_date:
        parts.append(f"<b>Zahlbar bis:</b> {_date_de(due_date)}")
    if reference:
        parts.append(f"<b>Verwendungszweck:</b> {reference}")

    if parts:
        story.append(Paragraph("<br/>".join(parts), styles["body"]))


# ── Fußzeile ─────────────────────────────────────────────────────────

def _footer_func(canvas, doc, seller: dict):
    """Fußzeile auf jeder Seite."""
    canvas.saveState()
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(MID_GRAY)

    name = seller.get("name", "")
    street = seller.get("street", "")
    plz_city = f'{seller.get("post_code", "")} {seller.get("city", "")}'.strip()
    email = seller.get("email", "")
    vat = seller.get("vat_id", "")
    iban = seller.get("iban", "")

    left_parts = [p for p in [name, street, plz_city] if p]
    right_parts = [p for p in [email, f"USt-ID: {vat}" if vat else "",
                                f"IBAN: {iban}" if iban else ""] if p]

    y = 12 * mm
    canvas.line(MARGIN_L, y + 3*mm, PAGE_W - MARGIN_R, y + 3*mm)
    canvas.drawString(MARGIN_L, y, " · ".join(left_parts))
    canvas.drawRightString(PAGE_W - MARGIN_R, y, " · ".join(right_parts))

    # Seitenzahl
    canvas.drawCentredString(PAGE_W / 2, y - 8, f"Seite {doc.page}")
    canvas.restoreState()


# ── Hauptgenerator ───────────────────────────────────────────────────

def generate_document(
    doc_type: str,
    seller: dict,
    recipient: dict,
    positions: list,
    step: dict,
    reference: str = "",
    doc_date: str = "",
    due_date: str = "",
    delivery_date: str = "",
    subject: str = "",
    intro_text: str = "",
    closing_text: str = "",
    logo_path: Optional[str] = None,
    dunning_level: int = 0,
    original_invoice_ref: str = "",
    original_invoice_amount: float = 0,
    dunning_fee: float = 0,
) -> bytes:
    """
    Erzeugt ein PDF-Dokument.
    Gibt die PDF-Bytes zurück.
    """
    buf = BytesIO()
    styles = _build_styles()

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
        topMargin=MARGIN_T, bottomMargin=MARGIN_B,
    )

    story = []

    # ── Briefkopf ──
    _build_header(story, styles, seller, logo_path)

    # ── Adressblock ──
    _build_address_block(story, styles, seller, recipient)

    # ── Dokumenttitel + Meta ──
    title_key = doc_type
    if doc_type == "dunning":
        title_key = f"dunning_{dunning_level}" if dunning_level else "dunning_1"
    title = DOC_TYPE_TITLES.get(title_key, "Dokument")

    story.append(Paragraph(title, styles["doc_title"]))

    meta_parts = []
    if reference:
        meta_parts.append(f"<b>Nr.:</b> {reference}")
    if doc_date:
        meta_parts.append(f"<b>Datum:</b> {_date_de(doc_date)}")
    if delivery_date:
        meta_parts.append(f"<b>Leistungsdatum:</b> {_date_de(delivery_date)}")
    if subject:
        meta_parts.append(f"<b>Betreff:</b> {subject}")
    if meta_parts:
        story.append(Paragraph("  ·  ".join(meta_parts), styles["doc_meta"]))
    story.append(Spacer(1, 6*mm))

    # ── Einleitungstext ──
    if not intro_text:
        intro_text = _default_intro(doc_type, seller, recipient, reference,
                                     dunning_level, original_invoice_ref)
    story.append(Paragraph(intro_text, styles["body"]))
    story.append(Spacer(1, 4*mm))

    # ── Mahnungs-Sonderblock ──
    if doc_type == "dunning":
        _build_dunning_block(story, styles, original_invoice_ref,
                              original_invoice_amount, dunning_fee, dunning_level)
    else:
        # ── Positionen ──
        show_prices = doc_type != "delivery_note"
        _build_positions_table(story, styles, positions, show_prices=show_prices)

        # ── Summen ──
        if show_prices and positions:
            totals = _build_totals(story, styles, positions, step)
        else:
            totals = {"gross": 0}

    # ── Schlusstext ──
    if not closing_text:
        closing_text = _default_closing(doc_type, seller, dunning_level)
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(closing_text, styles["body"]))

    # ── Zahlungsinfo (nur bei Dokumenten mit Zahlungserwartung) ──
    if doc_type in ("customer_quote", "order_intake", "dunning"):
        gross = totals.get("gross", 0) if doc_type != "dunning" else original_invoice_amount + dunning_fee
        _build_payment_info(story, styles, seller, gross=gross,
                            due_date=due_date, reference=reference)

    # ── Build PDF ──
    def on_page(canvas, doc_obj):
        _footer_func(canvas, doc_obj, seller)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


# ── Standard-Texte ───────────────────────────────────────────────────

def _default_intro(doc_type, seller, recipient, reference,
                    dunning_level=0, original_ref=""):
    r_name = recipient.get("name", "")
    s_name = seller.get("name", "")

    if doc_type == "supplier_quote":
        return (f"Sehr geehrte Damen und Herren,<br/><br/>"
                f"wir bitten Sie um Abgabe eines Angebots für die nachfolgend "
                f"aufgeführten Positionen.")
    elif doc_type == "purchase_order":
        return (f"Sehr geehrte Damen und Herren,<br/><br/>"
                f"hiermit bestellen wir verbindlich die nachfolgend aufgeführten "
                f"Positionen. Bitte bestätigen Sie den Auftrag schriftlich.")
    elif doc_type == "customer_quote":
        return (f"Sehr geehrte Damen und Herren,<br/><br/>"
                f"vielen Dank für Ihre Anfrage. Gerne unterbreiten wir Ihnen "
                f"folgendes Angebot:")
    elif doc_type == "order_intake":
        return (f"Sehr geehrte Damen und Herren,<br/><br/>"
                f"vielen Dank für Ihren Auftrag. Hiermit bestätigen wir die "
                f"Ausführung der nachfolgend aufgeführten Leistungen.")
    elif doc_type == "delivery_note":
        return (f"Sehr geehrte Damen und Herren,<br/><br/>"
                f"mit diesem Lieferschein bestätigen wir die Lieferung "
                f"folgender Positionen:")
    elif doc_type == "dunning":
        level_text = {
            1: "bei der Durchsicht unserer Konten haben wir festgestellt, "
               "dass die nachstehende Rechnung noch offen ist. "
               "Wir möchten Sie freundlich an die Zahlung erinnern.",
            2: "trotz unserer Zahlungserinnerung konnten wir leider "
               "keinen Zahlungseingang feststellen. "
               "Wir bitten Sie dringend, den ausstehenden Betrag zu begleichen.",
            3: "wir mussten leider feststellen, dass unsere bisherigen "
               "Zahlungsaufforderungen ohne Ergebnis geblieben sind. "
               "Wir fordern Sie hiermit letztmalig zur Zahlung auf.",
        }
        return (f"Sehr geehrte Damen und Herren,<br/><br/>"
                f"{level_text.get(dunning_level, level_text[1])}")
    return "Sehr geehrte Damen und Herren,"


def _default_closing(doc_type, seller, dunning_level=0):
    s_name = seller.get("name", "")

    if doc_type == "supplier_quote":
        return ("Bitte senden Sie uns Ihr Angebot bis zum angegebenen Datum zu.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "purchase_order":
        return (f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "customer_quote":
        return ("Dieses Angebot ist 30 Tage gültig. "
                "Bei Fragen stehen wir Ihnen gerne zur Verfügung.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "order_intake":
        return (f"Wir freuen uns auf die Zusammenarbeit.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "delivery_note":
        return (f"Bitte bestätigen Sie den Empfang durch Ihre Unterschrift.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "dunning":
        if dunning_level >= 3:
            return ("Sollte der Betrag nicht innerhalb von 7 Tagen eingehen, "
                    "sehen wir uns gezwungen, die Angelegenheit an unseren "
                    "Rechtsanwalt zu übergeben.<br/><br/>"
                    f"Mit freundlichen Grüßen<br/>{s_name}")
        return (f"Für Rückfragen stehen wir Ihnen gerne zur Verfügung.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    return f"Mit freundlichen Grüßen<br/>{s_name}"


# ── Mahnungs-Sonderblock ────────────────────────────────────────────

def _build_dunning_block(story, styles, invoice_ref, amount, fee, level):
    """Zeigt Rechnungsdaten + Mahngebühr statt Positionstabelle."""
    usable_w = PAGE_W - MARGIN_L - MARGIN_R

    rows = []
    rows.append([
        Paragraph("<b>Rechnungsnummer:</b>", styles["body"]),
        Paragraph(str(invoice_ref), styles["body"]),
    ])
    rows.append([
        Paragraph("<b>Offener Betrag:</b>", styles["body"]),
        Paragraph(f"{_fmt(amount)} €", styles["body_bold"]),
    ])
    if fee:
        rows.append([
            Paragraph(f"<b>Mahngebühr (Stufe {level}):</b>", styles["body"]),
            Paragraph(f"{_fmt(fee)} €", styles["body"]),
        ])
        rows.append([
            Paragraph("<b>Gesamtforderung:</b>", styles["body"]),
            Paragraph(f"{_fmt(amount + fee)} €", styles["grand_total"]),
        ])

    tbl = Table(rows, colWidths=[usable_w * 0.5, usable_w * 0.5])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
    ]))
    story.append(tbl)


# ── Convenience-Wrapper ──────────────────────────────────────────────

class DocumentGenerator:
    """Wrapper der Seller-Daten und Logo einmal lädt."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.docs_dir = self.data_dir / "documents"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.doc_index_path = self.data_dir / "documents.json"

    def _load_seller(self) -> dict:
        cfg_path = self.data_dir / "mandant_settings.json"
        if cfg_path.exists():
            try:
                return json.loads(cfg_path.read_text("utf-8"))
            except Exception:
                pass
        return {}

    def _logo(self) -> Optional[str]:
        return _find_logo(self.data_dir)

    def _load_doc_index(self) -> list:
        if self.doc_index_path.exists():
            try:
                return json.loads(self.doc_index_path.read_text("utf-8"))
            except Exception:
                pass
        return []

    def _save_doc_index(self, index: list):
        self.doc_index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), "utf-8"
        )

    def generate(self, doc_type: str, recipient: dict, positions: list,
                 step: dict, reference: str = "", subject: str = "",
                 doc_date: str = "", due_date: str = "",
                 delivery_date: str = "",
                 transaction_id: str = "", step_key: str = "",
                 dunning_level: int = 0, original_invoice_ref: str = "",
                 original_invoice_amount: float = 0,
                 dunning_fee: float = 0,
                 intro_text: str = "", closing_text: str = "") -> dict:
        """
        Erzeugt PDF, speichert Datei, gibt Metadaten zurück.
        """
        import hashlib

        seller = self._load_seller()
        logo_path = self._logo()

        if not doc_date:
            doc_date = date.today().isoformat()

        pdf_bytes = generate_document(
            doc_type=doc_type,
            seller=seller,
            recipient=recipient,
            positions=positions,
            step=step,
            reference=reference,
            doc_date=doc_date,
            due_date=due_date,
            delivery_date=delivery_date,
            subject=subject,
            logo_path=logo_path,
            dunning_level=dunning_level,
            original_invoice_ref=original_invoice_ref,
            original_invoice_amount=original_invoice_amount,
            dunning_fee=dunning_fee,
            intro_text=intro_text,
            closing_text=closing_text,
        )

        # Dateiname
        filename = f"{reference or doc_type}.pdf".replace("/", "-")
        filepath = self.docs_dir / filename
        filepath.write_bytes(pdf_bytes)

        # Hash
        sha = hashlib.sha256(pdf_bytes).hexdigest()

        # Index
        doc_entry = {
            "id": f"doc-{sha[:12]}",
            "type": doc_type,
            "filename": filename,
            "filepath": str(filepath),
            "hash_sha256": sha,
            "size_bytes": len(pdf_bytes),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "transaction_id": transaction_id,
            "step_key": step_key,
            "reference": reference,
        }

        index = self._load_doc_index()
        index.append(doc_entry)
        self._save_doc_index(index)

        return doc_entry

    def get_filepath(self, filename: str) -> Optional[Path]:
        p = self.docs_dir / filename
        return p if p.exists() else None

    def list_docs(self, transaction_id: str = "") -> list:
        index = self._load_doc_index()
        if transaction_id:
            index = [d for d in index if d.get("transaction_id") == transaction_id]
        return index
