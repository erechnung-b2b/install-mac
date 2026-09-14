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

# Brand-Farben (Betreiber-Design)
DEEP_BLUE = colors.HexColor("#1B4B82")
CYAN = colors.HexColor("#00A0D2")
DARK_GRAY = colors.HexColor("#1E293B")
MID_GRAY = colors.HexColor("#64748B")
LIGHT_GRAY = colors.HexColor("#F5F7FA")
BORDER_GRAY = colors.HexColor("#D1D5DB")
WHITE = colors.white

DOC_TYPE_TITLES = {
    "supplier_quote":     "Anfrage",
    "purchase_order":     "Bestellung",
    "supplier_invoice":   "Eingangsrechnung",
    "customer_quote":     "Angebot",
    "order_intake":       "Auftragseingang",
    "order_confirmation": "Auftragsbestätigung",
    "delivery_note":      "Lieferschein",
    "invoice":            "Rechnung",
    "payment_invoice":    "Abschlagsrechnung",
    "storno":             "Stornorechnung",
    "gutschrift":         "Gutschrift",
    "dunning_1":          "Zahlungserinnerung",
    "dunning_2":          "1. Mahnung",
    "dunning_3":          "2. Mahnung – Letzte Aufforderung",
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

    # Markdown-Render-Styles (für Angebots-Textbausteine + AGB)
    styles["md_h1"] = ParagraphStyle(
        "md_h1", parent=ss["Normal"],
        fontSize=13, textColor=DEEP_BLUE, leading=16, fontName="Helvetica-Bold",
        spaceBefore=4*mm, spaceAfter=1.5*mm)
    styles["md_h2"] = ParagraphStyle(
        "md_h2", parent=ss["Normal"],
        fontSize=11, textColor=DEEP_BLUE, leading=14, fontName="Helvetica-Bold",
        spaceBefore=3*mm, spaceAfter=1*mm)
    styles["md_h3"] = ParagraphStyle(
        "md_h3", parent=ss["Normal"],
        fontSize=10, textColor=DARK_GRAY, leading=13, fontName="Helvetica-Bold",
        spaceBefore=2*mm, spaceAfter=0.5*mm)
    styles["md_li"] = ParagraphStyle(
        "md_li", parent=ss["Normal"],
        fontSize=9.5, textColor=DARK_GRAY, leading=13, leftIndent=10, bulletIndent=2)
    styles["block_label"] = ParagraphStyle(
        "block_label", parent=ss["Normal"],
        fontSize=10, textColor=DEEP_BLUE, leading=13, fontName="Helvetica-Bold",
        spaceBefore=2*mm, spaceAfter=1*mm)
    styles["product_name"] = ParagraphStyle(
        "product_name", parent=ss["Normal"],
        fontSize=12, textColor=DEEP_BLUE, leading=15, fontName="Helvetica-Bold",
        spaceBefore=4*mm, spaceAfter=2*mm)
    styles["section_heading"] = ParagraphStyle(
        "section_heading", parent=ss["Normal"],
        fontSize=14, textColor=DEEP_BLUE, leading=17, fontName="Helvetica-Bold",
        spaceBefore=2*mm, spaceAfter=3*mm)

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
    """Sucht Logo: zuerst per UI hochgeladenes (data/logo/logo.*),
    dann projekt-eigenes (data/Logo.*, ../Logo.*)."""
    # 1) Per UI hochgeladen via /api/logo → data/logo/
    upload_dir = data_dir / "logo"
    if upload_dir.exists():
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            p = upload_dir / f"logo{ext}"
            if p.exists():
                return str(p)
    # 2) Fallback: Default-Logos im Projekt
    for name in ("Logo.jpg", "logo.jpg", "Logo.png", "logo.png"):
        for base in [data_dir, data_dir.parent, data_dir / "data"]:
            p = base / name
            if p.exists():
                return str(p)
    return None


# ── Briefkopf ────────────────────────────────────────────────────────

def _street_line(d: dict) -> str:
    """Kombiniert Straße + Hausnummer (DIN 5008). Liefert die Straße allein,
    falls keine Hausnummer im dict liegt."""
    s = (d.get("street", "") or "").strip()
    h = (d.get("house_number", "") or "").strip()
    return (s + " " + h).strip() if h else s


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
    street = _street_line(seller)
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
    # Absenderzeile ist einzeilig — ein Umbruch im Namen wird hier zum Leerzeichen
    s_name = " ".join((seller.get("name", "") or "").split())
    s_street = _street_line(seller)
    s_plz = seller.get("post_code", "")
    s_city = seller.get("city", "")
    sender_line = f"{s_name} · {s_street} · {s_plz} {s_city}"
    story.append(Paragraph(sender_line, styles["sender_line"]))
    story.append(Spacer(1, 2*mm))

    # Empfänger
    salut = (recipient.get("salutation") or "").strip()
    r_name = (recipient.get("name") or "").strip()
    contact = (recipient.get("contact_name") or "").strip()
    is_private = bool(recipient.get("is_private"))
    lines = []
    if is_private:
        # Privatperson: NUR eine Namenszeile (Ansprechpartner bevorzugt, sonst der
        # Name) mit vorangestellter Anrede — keine separate Firmenzeile, sonst
        # stünde der Name doppelt ("Führich" + "Herr Hans Führich").
        person = contact or r_name
        if person:
            if salut and not person.lower().startswith(salut.lower()):
                person = f"{salut} {person}"
            for teil in [z.strip() for z in person.splitlines() if z.strip()]:
                lines.append(f'<b>{teil}</b>')
    else:
        # Geschäftskunde: Firma/Name-Zeile + (Anrede + Ansprechpartner)-Zeile.
        if r_name:
            # Ohne Ansprechpartner: Anrede vor den Namen ("Eheleute Führich" …)
            name_line = r_name
            if salut and not contact and not r_name.lower().startswith(salut.lower()):
                name_line = f"{salut} {r_name}"
            # Ein im Namen gesetzter Zeilenumbruch wird uebernommen. Lange Namen
            # bricht der Absatz ohnehin selbst um; der Umbruch steuert nur, WO
            # ("PFS Property I S.a r.l." / "c/o RME Retail Management ...").
            for teil in [z.strip() for z in name_line.splitlines() if z.strip()]:
                lines.append(f'<b>{teil}</b>')
        if contact:
            # Mit Ansprechpartner: Anrede vor die Person ("Herr Max Mustermann").
            # Steht die Person schon in der Namenszeile (Kunden ohne Firma tragen
            # "Frau Ingrida Kasparovaite" als Namen), waere das eine Dopplung.
            kontaktzeile = f"{salut} {contact}".strip()
            namensblock = " ".join(r_name.split()).lower()
            if contact.strip().lower() not in namensblock:
                lines.append(kontaktzeile)
    r_street = _street_line(recipient)
    if r_street:
        lines.append(r_street)
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

        # ── Nullpositionen als „s. u." statt „0,00" (19.08.2026) ──
        # Bei einem PAUSCHALANGEBOT beschreiben die Leistungszeilen nur den Umfang;
        # der Preis steht in einer eigenen Pauschalzeile darunter. Dort „0,00 €"
        # zu drucken liest sich wie „kostet nichts" — gemeint ist „im Pauschalpreis
        # enthalten". Deshalb erscheint in beiden Betragsspalten „s. u.".
        #
        # Greift NUR, wenn das Dokument tatsächlich eine solche Sammelzeile hat
        # (eine Position mit Betrag > 0 UND Einheit „Pauschal"). Sonst bleibt eine
        # echte 0,00-Position eine 0,00-Position — eine kostenlose Beigabe soll
        # weiterhin als solche erkennbar sein.
        _hat_pauschale = any(
            str(x.get("unit", "")).strip().lower() == "pauschal"
            and (float(x.get("net_amount") or 0) > 0)
            for x in (positions or [])
        )

        for p in positions:
            _null = (_hat_pauschale
                     and not (float(p.get("net_amount") or 0) > 0)
                     and str(p.get("unit", "")).strip().lower() != "pauschal")
            _ep = "s. u." if _null else _fmt(p.get("unit_price", 0))
            _net = "s. u." if _null else _fmt(p.get("net_amount", 0))
            data.append([
                Paragraph(str(p.get("pos_nr", "")), styles["table_cell"]),
                Paragraph(str(p.get("description", "")), styles["table_cell"]),
                Paragraph(str(p.get("quantity", "")), styles["table_cell_r"]),
                Paragraph(str(p.get("unit", "Stk")), styles["table_cell"]),
                Paragraph(_ep, styles["table_cell_r"]),
                Paragraph(f'{p.get("discount_percent", 0):.0f}' if p.get("discount_percent") else "",
                          styles["table_cell_r"]),
                Paragraph(_net, styles["table_cell_r"]),
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

def _build_totals(story, styles, positions: list, step: dict, gross_label: str = "Gesamtbetrag:"):
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
        Paragraph(gross_label, styles["total_label"]),
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

def _build_giro_qr_image(beneficiary_name: str, iban: str, amount: float,
                          bic: str = "", reference: str = "",
                          size_mm: float = 32):
    """Erzeugt einen GiroCode-QR-Code (EPC069-12) als ReportLab-Image-Flowable.
    Gibt None zurück, wenn QR-Bibliothek fehlt oder Daten unvollständig sind."""
    if not iban or amount <= 0:
        return None
    try:
        import qrcode
        from girocode import build_epc_payload
        payload = build_epc_payload(
            beneficiary_name=beneficiary_name,
            iban=iban,
            amount=amount,
            bic=bic,
            text=(f"Verwendungszweck: {reference}" if reference else ""),
        )
        if not payload:
            return None
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8, border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return Image(buf, width=size_mm*mm, height=size_mm*mm)
    except Exception as e:
        import sys
        print(f"[doc_generator] GiroCode-QR fehlgeschlagen: {e}", file=sys.stderr)
        return None


def _build_prepaid_summary(story, styles, prepaid_invoices: list, gross_total: float) -> float:
    """Listet bereits gezahlte/gestellte Abschlagsrechnungen auf und liefert die Summe.
    Genutzt in der Schlussrechnung, um den Restbetrag korrekt darzustellen."""
    if not prepaid_invoices:
        return 0.0
    total = 0.0
    rows = []
    for p in prepaid_invoices:
        gross = float(p.get("amount_gross", 0) or 0)
        if gross <= 0:
            continue
        status = p.get("status", "")
        date_str = p.get("paid_at") or p.get("date") or ""
        marker = "✓" if status == "PAID" else "·"
        rows.append([
            Paragraph(marker, styles["table_cell"]),
            Paragraph(str(p.get("reference", "")), styles["table_cell"]),
            Paragraph(str(p.get("title", "")), styles["table_cell"]),
            Paragraph(_date_de(date_str), styles["table_cell_r"]),
            Paragraph(_fmt(gross) + " €", styles["table_cell_r"]),
        ])
        total += gross
    if not rows:
        return 0.0

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("<b>Bereits geleistete Anzahlungen / Abschläge</b>",
                            styles["block_label"]))

    usable_w = PAGE_W - MARGIN_L - MARGIN_R
    col_widths = [6*mm, 28*mm, None, 26*mm, 28*mm]
    rest = usable_w - sum(w for w in col_widths if w)
    col_widths[2] = rest
    header = [
        Paragraph("", styles["table_header"]),
        Paragraph("Nr.", styles["table_header"]),
        Paragraph("Bezeichnung", styles["table_header"]),
        Paragraph("Datum", styles["table_header"]),
        Paragraph("Brutto", styles["table_header"]),
    ]
    tbl = Table([header] + rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), DEEP_BLUE),
        ("TEXTCOLOR", (0,0), (-1,0), WHITE),
        ("FONTSIZE", (0,0), (-1,0), 8),
        ("BOTTOMPADDING", (0,0), (-1,0), 4),
        ("TOPPADDING", (0,0), (-1,0), 4),
        ("FONTSIZE", (0,1), (-1,-1), 8.5),
        ("LINEBELOW", (0,0), (-1,-1), 0.25, BORDER_GRAY),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 2*mm))

    rest_betrag = max(0, float(gross_total) - total)
    story.append(Paragraph(
        f"<b>Summe Anzahlungen: {_fmt(total)} €</b>  ·  "
        f"<b>Verbleibender Restbetrag: {_fmt(rest_betrag)} €</b>",
        styles["body_bold"]))
    return total


def _build_payment_info(story, styles, seller: dict, gross: float = 0,
                        due_date: str = "", reference: str = "",
                        show_qr: bool = False, doc_date: str = ""):
    """IBAN, BIC, Zahlungsziel. Optional mit GiroCode-QR (für Rechnung + Mahnung)."""
    story.append(Spacer(1, 6*mm))

    parts = []
    iban = seller.get("iban", "")
    bic = seller.get("bic", "")
    if iban:
        parts.append(f"<b>IBAN:</b> {iban}")
    if bic:
        parts.append(f"<b>BIC:</b> {bic}")
    if due_date:
        # Fällig am/vor Rechnungsdatum → "Zahlbar sofort", sonst "Zahlbar bis: <Datum>"
        if doc_date and str(due_date)[:10] <= str(doc_date)[:10]:
            parts.append("<b>Zahlbar sofort</b>")
        else:
            parts.append(f"<b>Zahlbar bis:</b> {_date_de(due_date)}")
    if reference:
        parts.append(f"<b>Verwendungszweck:</b> {reference}")
    if gross > 0:
        parts.append(f"<b>Betrag:</b> {_fmt(gross)} EUR")

    if not parts:
        return

    text_block = Paragraph("<br/>".join(parts), styles["body"])

    # GiroCode-QR nur bei show_qr=True (Rechnung + Mahnung)
    qr_img = None
    if show_qr:
        qr_img = _build_giro_qr_image(
            beneficiary_name=seller.get("name", ""),
            iban=iban, amount=gross, bic=bic,
            reference=reference, size_mm=32
        )

    if qr_img is not None:
        # 2-Spalten-Layout: links Text+Überschrift, rechts QR + Mini-Beschriftung
        qr_caption = Paragraph(
            "<b>GiroCode</b><br/>"
            "<font size='7'>SEPA-Überweisung per Banking-App scannen</font>",
            styles["small"]
        )
        # Innere Tabelle: QR oben, Caption unten
        right_cell = Table([[qr_img], [qr_caption]],
                            colWidths=[34*mm],
                            style=TableStyle([
                                ("ALIGN", (0,0), (-1,-1), "CENTER"),
                                ("VALIGN", (0,0), (-1,-1), "TOP"),
                                ("TOPPADDING", (0,1), (-1,1), 2),
                                ("BOTTOMPADDING", (0,0), (-1,-1), 0),
                            ]))
        outer = Table([[text_block, right_cell]],
                       colWidths=[None, 38*mm],
                       style=TableStyle([
                           ("VALIGN", (0,0), (-1,-1), "TOP"),
                           ("LEFTPADDING", (0,0), (-1,-1), 0),
                           ("RIGHTPADDING", (0,0), (-1,-1), 0),
                           ("TOPPADDING", (0,0), (-1,-1), 0),
                           ("BOTTOMPADDING", (0,0), (-1,-1), 0),
                       ]))
        story.append(outer)
    else:
        story.append(text_block)


# ── Fußzeile ─────────────────────────────────────────────────────────

def _footer_func(canvas, doc, seller: dict):
    """Fußzeile auf jeder Seite."""
    canvas.saveState()
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(MID_GRAY)

    name = seller.get("name", "")
    street = _street_line(seller)
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

OFFER_BLOCK_LABELS = [
    ("intro",          "Einleitung"),
    ("leistungsumfang","Leistungsumfang"),
    ("lieferumfang",   "Lieferumfang"),
    ("mitwirkung",     "Mitwirkungspflichten des Auftraggebers"),
    ("haftung",        "Haftung"),
]


import re as _re_md


def _md_inline(s: str) -> str:
    """Inline-Markdown -> ReportLab-Mini-HTML.
    Reihenfolge: **bold**, *italic* (NUR umschlossen), _italic_, `code`."""
    # erst escapen — aber HTML-Tags die der User vielleicht schon nutzt erlauben
    # ReportLab erwartet <b> <i> <u> <br/> ... -> wir nehmen Markdown -> diese Tags
    out = s
    # **fett**
    out = _re_md.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    # *kursiv* (nicht * am Zeilenanfang, das war schon Liste)
    out = _re_md.sub(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", out)
    # _kursiv_
    out = _re_md.sub(r"(?<![A-Za-z0-9_])_([^_\n]+?)_(?![A-Za-z0-9_])", r"<i>\1</i>", out)
    return out


def _render_markdown(story, styles, text: str):
    """Sehr leichter Markdown-Renderer (Überschriften, Listen, Absätze).
    Akzeptiert auch reinen Klartext + manuelle HTML-Tags."""
    if not text:
        return
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    para_buf = []
    list_buf = []

    def flush_para():
        if para_buf:
            joined = "<br/>".join(_md_inline(l) for l in para_buf)
            story.append(Paragraph(joined, styles["body"]))
            para_buf.clear()

    def flush_list():
        if list_buf:
            from reportlab.platypus import ListFlowable, ListItem
            items = [ListItem(Paragraph(_md_inline(li), styles["md_li"]),
                              leftIndent=8, bulletColor=DEEP_BLUE)
                     for li in list_buf]
            story.append(ListFlowable(items, bulletType="bullet",
                                       bulletFontSize=9, bulletOffsetY=-1,
                                       leftIndent=10, spaceBefore=1*mm, spaceAfter=2*mm))
            list_buf.clear()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para(); flush_list()
            continue
        # ### → h3
        m = _re_md.match(r"^\s*###\s+(.*)$", line)
        if m:
            flush_para(); flush_list()
            story.append(Paragraph(_md_inline(m.group(1)), styles["md_h3"]))
            continue
        # ## → h2
        m = _re_md.match(r"^\s*##\s+(.*)$", line)
        if m:
            flush_para(); flush_list()
            story.append(Paragraph(_md_inline(m.group(1)), styles["md_h2"]))
            continue
        # # → h1
        m = _re_md.match(r"^\s*#\s+(.*)$", line)
        if m:
            flush_para(); flush_list()
            story.append(Paragraph(_md_inline(m.group(1)), styles["md_h1"]))
            continue
        # Listenpunkt
        m = _re_md.match(r"^\s*[\*\-•]\s+(.*)$", line)
        if m:
            flush_para()
            list_buf.append(m.group(1))
            continue
        # normale Zeile
        flush_list()
        para_buf.append(line)
    flush_para(); flush_list()


def _build_product_offer_blocks(story, styles, positions: list,
                                 products_lookup: Optional[dict] = None,
                                 offer_settings: Optional[dict] = None):
    """Rendert je Position (mit art_nr) die Angebots-Textbausteine aus dem
    Produktkatalog mit Markdown-Unterstützung. Haftung fällt auf globalen
    Default aus offer_settings zurück, wenn pro Produkt nicht gesetzt."""
    if not positions or not products_lookup:
        return
    default_haftung = ((offer_settings or {}).get("default_haftung") or "").strip()
    seen = set()
    blocks_to_render = []
    for pos in positions:
        art = (pos.get("art_nr") or "").strip()
        if not art or art in seen:
            continue
        prod = products_lookup.get(art)
        if not prod:
            continue
        ob = dict(prod.get("offer_blocks") or {})
        # Haftung-Fallback auf globalen Default
        if not (ob.get("haftung") or "").strip() and default_haftung:
            ob["haftung"] = default_haftung
        if not any((ob.get(k) or "").strip() for k, _ in OFFER_BLOCK_LABELS):
            continue
        seen.add(art)
        blocks_to_render.append((prod, ob))

    if not blocks_to_render:
        return

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("Leistungsbeschreibungen", styles["section_heading"]))

    for prod, ob in blocks_to_render:
        name = (prod.get("name") or "").strip()
        art = (prod.get("art_nr") or "").strip()
        story.append(Paragraph(f"{name}  <font size='9' color='#888888'>({art})</font>",
                               styles["product_name"]))
        for key, label in OFFER_BLOCK_LABELS:
            txt = (ob.get(key) or "").strip()
            if not txt:
                continue
            story.append(Paragraph(label, styles["block_label"]))
            _render_markdown(story, styles, txt)
        story.append(Spacer(1, 4*mm))


def _build_agb_appendix(story, styles, offer_settings: Optional[dict]):
    """Hängt die globalen AGB als eigene Seite am Ende des Dokuments an."""
    if not offer_settings:
        return
    agb_text = (offer_settings.get("agb_text") or "").strip()
    if not agb_text:
        return
    title = (offer_settings.get("agb_title") or "Allgemeine Geschäftsbedingungen").strip()
    from reportlab.platypus import PageBreak
    story.append(PageBreak())
    story.append(Paragraph(title, styles["doc_title"]))
    story.append(Spacer(1, 4*mm))
    _render_markdown(story, styles, agb_text)


def _build_vertrag_appendix(story, styles, offer_settings: Optional[dict],
                            recipient: Optional[dict] = None, net_total: float = 0):
    """Hängt den Beratungsvertrag-Entwurf als eigenen Anhang ans Kundenangebot.
    Platzhalter ({{empf_*}}, {{geb_*}}, {{verguetung}}) werden aus Empfänger-,
    Projekt- und Angebotsdaten gefüllt. Vergütung kommt aus der Angebotssumme."""
    if not offer_settings:
        return
    txt = (offer_settings.get("vertrag_text") or "").strip()
    if not txt:
        return
    title = (offer_settings.get("vertrag_title") or "Beratungsvertrag (Entwurf)").strip()
    r = recipient or {}
    proj = offer_settings.get("vertrag_projekt") or {}
    repl = {
        "{{empf_name}}":      str(r.get("name") or r.get("firma") or ""),
        "{{empf_vorname}}":   str(r.get("vorname") or ""),
        "{{empf_strasse}}":   str(r.get("strasse") or ""),
        "{{empf_plz}}":       str(r.get("plz") or r.get("postcode") or ""),
        "{{empf_ort}}":       str(r.get("ort") or r.get("city") or ""),
        "{{geb_strasse}}":    str(proj.get("strasse") or ""),
        "{{geb_plz_ort}}":    str(proj.get("plz_ort") or ""),
        "{{geb_bundesland}}": str(proj.get("bundesland") or ""),
        "{{geb_we}}":         str(proj.get("we") or ""),
        "{{verguetung}}":     (_fmt(net_total) + " € zzgl. MwSt.") if net_total else "________ € zzgl. MwSt.",
    }
    for k, v in repl.items():
        txt = txt.replace(k, v)
    from reportlab.platypus import PageBreak
    story.append(PageBreak())
    story.append(Paragraph(title, styles["doc_title"]))
    story.append(Spacer(1, 4*mm))
    _render_markdown(story, styles, txt)


def _build_order_signature_block(story, styles, offer_settings: Optional[dict] = None):
    """Hängt im Kundenangebot einen 'Auftrag erteilt'-Passus mit Unterschriftfeld an.

    Der Kunde kann das Angebot durch Ankreuzen + Unterschrift verbindlich
    beauftragen und unterschrieben zurücksenden. Der Begleittext lässt sich
    über offer_settings['auftrag_text'] überschreiben.
    """
    usable_w = PAGE_W - MARGIN_L - MARGIN_R
    intro = ((offer_settings or {}).get("auftrag_text") or "").strip() or (
        "Mit meiner Unterschrift erteile ich auf Grundlage des vorstehenden "
        "Angebots einen verbindlichen Auftrag und erkenne die beigefügten "
        "Allgemeinen Geschäftsbedingungen an. Bitte senden Sie dieses Angebot "
        "unterschrieben an uns zurück (per Post oder E-Mail).")

    # Ankreuz-Box neben der fett gesetzten Auftragszeile
    box = Table([[""]], colWidths=[5 * mm], rowHeights=[5 * mm])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, DARK_GRAY),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    head = Table(
        [[box, Paragraph(
            "<b>Auftrag erteilt</b> &ndash; ich beauftrage die oben angebotenen "
            "Leistungen verbindlich.", styles["body"])]],
        colWidths=[8 * mm, usable_w - 8 * mm],
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    # Zwei Unterschriftslinien (Ort/Datum · Unterschrift/Stempel)
    col = (usable_w - 15 * mm) / 2
    sig = Table(
        [["", "", ""],
         [Paragraph("Ort, Datum", styles["small"]), "",
          Paragraph("Unterschrift / Firmenstempel", styles["small"])]],
        colWidths=[col, 15 * mm, col], rowHeights=[14 * mm, None],
    )
    sig.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (0, 0), 0.6, DARK_GRAY),
        ("LINEBELOW", (2, 0), (2, 0), 0.6, DARK_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
    ]))

    story.append(KeepTogether([
        Spacer(1, 8 * mm),
        HRFlowable(width="100%", thickness=0.6, color=BORDER_GRAY,
                   spaceBefore=0, spaceAfter=3 * mm),
        Paragraph("Auftragserteilung", styles["section_heading"]),
        head,
        Spacer(1, 1.5 * mm),
        Paragraph(intro, styles["body"]),
        sig,
    ]))


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
    products_lookup: Optional[dict] = None,
    offer_settings: Optional[dict] = None,
    prepaid_invoices: Optional[list] = None,
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
    else:
        # Vorlagentext: generische Anredezeile durch die persoenliche ersetzen
        intro_text = _absatz_html(_intro_mit_anrede(intro_text, recipient))
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
            totals = _build_totals(story, styles, positions, step,
                                   gross_label=("Gutschriftsbetrag:" if doc_type in ("gutschrift", "storno")
                                                else "Gesamtbetrag:"))
        else:
            totals = {"gross": 0}

    # ── Angebots-Textbausteine (Kundenangebot + Auftragsbestätigung) ──
    if doc_type in ("customer_quote", "order_confirmation"):
        _build_product_offer_blocks(story, styles, positions, products_lookup, offer_settings)

    # ── Schlusstext ──
    if not closing_text:
        closing_text = _default_closing(doc_type, seller, dunning_level)
    else:
        closing_text = _absatz_html(closing_text)
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(closing_text, styles["body"]))

    # ── Schlussrechnung: Erhaltene Anzahlungen anzeigen + vom Rechnungsbetrag abziehen ──
    prepaid_total = 0.0
    if doc_type == "invoice" and prepaid_invoices:
        prepaid_total = _build_prepaid_summary(story, styles, prepaid_invoices,
                                                 totals.get("gross", 0))

    # ── Zahlungsinfo (nur bei Dokumenten mit Zahlungserwartung) ──
    if doc_type in ("customer_quote", "order_intake", "order_confirmation",
                     "invoice", "payment_invoice", "dunning"):
        gross = totals.get("gross", 0) if doc_type != "dunning" else original_invoice_amount + dunning_fee
        # Bei Schlussrechnung: bereits gezahlte Anzahlungen abziehen → tatsächlich offener Restbetrag
        if doc_type == "invoice" and prepaid_total > 0:
            gross = max(0, gross - prepaid_total)
        show_qr = doc_type in ("invoice", "payment_invoice", "dunning")
        _build_payment_info(story, styles, seller, gross=gross,
                            due_date=due_date, reference=reference,
                            show_qr=show_qr, doc_date=doc_date)

    # ── Auftragserteilung mit Unterschrift (nur BAFA/KFW-Angebote) ──
    # Enthält das Angebot ein Produkt mit "Vertrag separat" (contract_separate),
    # bekommt das Angebot einen "Auftrag erteilt"-Block mit Ankreuzfeld +
    # Unterschriftszeilen. Der Kunde beauftragt verbindlich durch Unterschrift
    # auf dem Angebot — ein separater Dienstvertrag ist für Neuverträge nicht
    # nötig. Die AGB bleiben Bestandteil des Angebots.
    _sep_contract = any(
        (products_lookup or {}).get(p.get("art_nr"), {}).get("contract_separate")
        for p in (positions or [])
    )
    if doc_type == "customer_quote" and _sep_contract:
        _build_order_signature_block(story, styles, offer_settings)

    # ── AGB-Anhang aus Markdown-Text (Kundenangebot + Auftragsbestätigung) ──
    # NUR wenn agb_mode != "pdf"; PDF-Anhang erfolgt nach doc.build per Merge.
    agb_mode = (offer_settings or {}).get("agb_mode", "markdown")
    if doc_type in ("customer_quote", "order_confirmation") and agb_mode != "pdf":
        _build_agb_appendix(story, styles, offer_settings)

    # ── Build PDF ──
    def on_page(canvas, doc_obj):
        _footer_func(canvas, doc_obj, seller)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    pdf_bytes = buf.getvalue()

    # ── AGB-Anhang aus PDF-Datei (Kundenangebot + Auftragsbestätigung) ──
    # ── Dienstvertrag-PDF anhängen (nur Kundenangebot) – vor der AGB ──
    if doc_type == "customer_quote" and (offer_settings or {}).get("vertrag_mode") == "pdf":
        # Enthält das Angebot ein Produkt mit "Vertrag separat" (BAFA/KFW), wird
        # der Dienstvertrag NICHT ins Angebot gemerged (das Angebot trägt dann
        # den Auftragserteilung-Unterschriftsblock; ein Vertrag ist optional als
        # separater Mail-Anhang beifügbar). Die AGB bleiben Bestandteil.
        #
        # NEU 19.08.2026: `step["kein_vertrag"]` unterdrückt ihn ausdrücklich.
        # Vorher gab es dafür KEINEN Weg — ohne „Vertrag separat"-Produkt wurde er
        # an JEDES Kundenangebot gehängt, und die Artikelnummer entschied allein.
        # Gesetzt wird das Kennzeichen aus der Auswahl im Versand-Dialog
        # („Dienstvertrag in das Angebots-PDF einbinden"), damit der Nutzer es je Angebot
        # entscheidet statt über die Produktwahl („nicht bei allen angeboten…
        # am besten steht der Vertragsanhang als auswahl beim versenden").
        vertrag_path = (offer_settings or {}).get("_vertrag_pdf_path")
        if vertrag_path and not _sep_contract and not (step or {}).get("kein_vertrag"):
            pdf_bytes = _merge_agb_pdf(pdf_bytes, vertrag_path)

    if doc_type in ("customer_quote", "order_confirmation") and agb_mode == "pdf":
        agb_path = (offer_settings or {}).get("_agb_pdf_path")
        if agb_path:
            pdf_bytes = _merge_agb_pdf(pdf_bytes, agb_path)
    return pdf_bytes


def _merge_agb_pdf(main_pdf_bytes: bytes, agb_pdf_path) -> bytes:
    """Hängt das AGB-PDF als zusätzliche Seiten an das Hauptdokument an."""
    try:
        from pypdf import PdfReader, PdfWriter
        from pathlib import Path as _P
        from io import BytesIO as _BIO
        p = _P(agb_pdf_path)
        if not p.exists():
            return main_pdf_bytes
        writer = PdfWriter()
        main_reader = PdfReader(_BIO(main_pdf_bytes))
        for page in main_reader.pages:
            writer.add_page(page)
        agb_reader = PdfReader(str(p))
        for page in agb_reader.pages:
            writer.add_page(page)
        out = _BIO()
        writer.write(out)
        return out.getvalue()
    except Exception as e:
        # Fallback: ohne AGB ausliefern, Fehler loggen
        import sys
        print(f"[doc_generator] AGB-Merge fehlgeschlagen: {e}", file=sys.stderr)
        return main_pdf_bytes


# ── Standard-Texte ───────────────────────────────────────────────────

_ANREDE_GENERISCH = "Sehr geehrte Damen und Herren,"


def _anrede(recipient):
    """Baut die Anrede aus Anrede-Feld + Ansprechpartner. Ist kein
    Ansprechpartner hinterlegt (z.B. bei 'Eheleute'/'Familie'), wird der
    Kundenname (Firma/Name) genommen. Spiegelt das Frontend buildAnrede()."""
    import re
    salut = (recipient.get("salutation") or "").strip()
    contact = (recipient.get("contact_name") or "").strip()
    is_private = bool(recipient.get("is_private"))
    nachname = contact.split()[-1] if contact else ""

    # Ist ein Ansprechpartner hinterlegt, gilt SEINE Anrede (07.09.2026:
    # „wenn Ansprechpartner gesetzt ist, sollte im Brief auch Sehr geehrter usw.
    # stehen"). `salutation` beschreibt die Adresszeile — steht dort „Firma" oder
    # „Eheleute", passt sie nicht auf eine einzelne Person: aus „Firma" + „Müller"
    # wurde sonst die Anrede „Firma Müller,". Fehlt die Ansprechpartner-Anrede,
    # gilt die alte Regel weiter (Altbestand ohne das neue Feld).
    if nachname:
        k_salut = (recipient.get("contact_salutation") or "").strip()
        if k_salut:
            salut = k_salut
        elif salut in ("Firma", "Eheleute", "Familie"):
            salut = ""     # keine Person — dann lieber generisch als falsch
    if not nachname:
        # Kein Ansprechpartner: Namen als Grundlage nehmen (führendes Anrede-Wort
        # entfernen). Bei Eheleute/Familie den ganzen Namen, bei Privatkunden den
        # Nachnamen (letztes Wort). Geschäftskunden ohne Ansprechpartner bekommen
        # keine persönliche Anrede.
        name = re.sub(r"^(Eheleute|Familie|Fam\.?|Herrn?|Frau)\s+", "",
                      (recipient.get("name") or "").strip(), flags=re.I).strip()
        if salut in ("Eheleute", "Familie"):
            nachname = name
        elif is_private and name:
            nachname = name.split()[-1]
    if salut and nachname:
        mapping = {
            "Herr": f"Sehr geehrter Herr {nachname},",
            "Frau": f"Sehr geehrte Frau {nachname},",
            "Eheleute": f"Sehr geehrte Eheleute {nachname},",
            "Familie": f"Sehr geehrte Familie {nachname},",
            "Herr Dr.": f"Sehr geehrter Herr Dr. {nachname},",
            "Frau Dr.": f"Sehr geehrte Frau Dr. {nachname},",
            "Herr Prof.": f"Sehr geehrter Herr Prof. {nachname},",
            "Frau Prof.": f"Sehr geehrte Frau Prof. {nachname},",
        }
        return mapping.get(salut, f"{salut} {nachname},")
    return _ANREDE_GENERISCH


def _absatz_html(text: str) -> str:
    """Macht Zeilenumbrüche eines Vorlagentextes im PDF sichtbar.

    Die Vorlagen aus den Einstellungen kommen mit "\n"; reportlab behandelt das
    im Paragraph als Leerzeichen — die Grußformel klebte dadurch am Schlusstext
    ("… Rückmeldung. Mit freundlichen Grüßen Firmenname").
    Bereits gesetzte <br/> bleiben unberührt.
    """
    import re as _re
    if not text:
        return text
    # nacktes & schützen (Entities wie &amp; bleiben stehen)
    text = _re.sub(r"&(?!(?:[a-zA-Z]+|#\d+);)", "&amp;", text)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _intro_mit_anrede(intro_text: str, recipient: dict) -> str:
    """Setzt die persönliche Anrede in einen Vorlagen-Einleitungstext.

    Die Dokumentvorlagen tragen die Anrede fest im Text ("Sehr geehrte Damen
    und Herren,"). Da ein hinterlegter Vorlagentext den Standardtext komplett
    ersetzt, kam _anrede() dort nie zum Zug — ein namentlich bekannter Kunde
    wurde trotzdem unpersönlich angeschrieben. Ist die Anrede des Empfängers
    bekannt, wird die generische Anredezeile hier ausgetauscht; hat der Text
    gar keine Anredezeile (selbst geschrieben), bleibt er unverändert.
    """
    import re as _re
    anrede = _anrede(recipient)
    if anrede == _ANREDE_GENERISCH:
        return intro_text          # keine persoenliche Anrede ermittelbar
    rest = _re.sub(r"^(?:\s|<br\s*/?>)+", "", intro_text or "")
    m = _re.match(r"sehr geehrte\s+damen\s+und\s+herren\s*[,;:!]?", rest, _re.I)
    if not m:
        return intro_text
    rest = _re.sub(r"^(?:\s|<br\s*/?>)+", "", rest[m.end():])
    return f"{anrede}<br/><br/>{rest}" if rest else anrede


def _default_intro(doc_type, seller, recipient, reference,
                    dunning_level=0, original_ref=""):
    r_name = recipient.get("name", "")
    # Absenderzeile ist einzeilig — ein Umbruch im Namen wird hier zum Leerzeichen
    s_name = " ".join((seller.get("name", "") or "").split())
    anrede = _anrede(recipient)

    if doc_type == "supplier_quote":
        return (f"{anrede}<br/><br/>"
                f"wir bitten Sie um Abgabe eines Angebots für die nachfolgend "
                f"aufgeführten Positionen.")
    elif doc_type == "purchase_order":
        return (f"{anrede}<br/><br/>"
                f"hiermit bestellen wir verbindlich die nachfolgend aufgeführten "
                f"Positionen. Bitte bestätigen Sie den Auftrag schriftlich.")
    elif doc_type == "customer_quote":
        return (f"{anrede}<br/><br/>"
                f"vielen Dank für Ihre Anfrage. Gerne unterbreiten wir Ihnen "
                f"folgendes Angebot:")
    elif doc_type == "order_intake":
        return (f"{anrede}<br/><br/>"
                f"hiermit dokumentieren wir den Eingang Ihrer Auftragsbestätigung "
                f"zu unserem Angebot. Die Bearbeitung wird nun aufgenommen.")
    elif doc_type == "order_confirmation":
        return (f"{anrede}<br/><br/>"
                f"vielen Dank für Ihren Auftrag. Hiermit bestätigen wir verbindlich "
                f"die Ausführung der nachfolgend aufgeführten Leistungen zu den "
                f"vereinbarten Konditionen. Es gelten die im Anhang dieses Dokuments "
                f"abgedruckten Allgemeinen Geschäftsbedingungen.")
    elif doc_type == "payment_invoice":
        return (f"{anrede}<br/><br/>"
                f"vereinbarungsgemäß stellen wir Ihnen folgende Abschlagsrechnung. "
                f"Die Endabrechnung erfolgt nach Abschluss aller vereinbarten "
                f"Leistungen unter Berücksichtigung dieser sowie ggf. weiterer "
                f"Anzahlungen.")
    elif doc_type == "delivery_note":
        return (f"{anrede}<br/><br/>"
                f"mit diesem Lieferschein bestätigen wir die Lieferung "
                f"folgender Positionen:")
    elif doc_type == "storno":
        bezug = f" zu unserer Rechnung {original_ref}" if original_ref else ""
        return (f"{anrede}<br/><br/>"
                f"hiermit erhalten Sie die Stornorechnung{bezug}. "
                f"Die nachstehend aufgeführten Positionen werden in voller Höhe "
                f"gutgeschrieben; eine bereits geleistete Zahlung wird auf das "
                f"unten genannte Konto erstattet.")
    elif doc_type == "gutschrift":
        bezug = f" zu unserer Rechnung {original_ref}" if original_ref else ""
        return (f"{anrede}<br/><br/>"
                f"hiermit erhalten Sie unsere Gutschrift{bezug}. "
                f"Wir schreiben Ihnen die nachstehend aufgeführten Positionen gut.")
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
        return (f"{anrede}<br/><br/>"
                f"{level_text.get(dunning_level, level_text[1])}")
    return anrede


def _default_closing(doc_type, seller, dunning_level=0):
    # Absenderzeile ist einzeilig — ein Umbruch im Namen wird hier zum Leerzeichen
    s_name = " ".join((seller.get("name", "") or "").split())

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
        return (f"Wir setzen uns nach Sichtung der Unterlagen mit Ihnen in Verbindung.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "order_confirmation":
        return (f"Wir beginnen umgehend mit der Ausführung und melden uns bezüglich "
                f"Terminen und benötigter Unterlagen.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "payment_invoice":
        return (f"Wir bitten um Überweisung auf das unten genannte Konto unter "
                f"Angabe der Rechnungsnummer als Verwendungszweck. Die Endabrechnung "
                f"folgt nach Leistungserbringung.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "delivery_note":
        return (f"Bitte bestätigen Sie den Empfang durch Ihre Unterschrift.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "storno":
        return ("Diese Stornorechnung ersetzt die ursprüngliche Rechnung. "
                "Bitte bewahren Sie sie zusammen mit dem Original auf.<br/><br/>"
                f"Mit freundlichen Grüßen<br/>{s_name}")
    elif doc_type == "gutschrift":
        return ("Der Gutschriftsbetrag wird mit offenen Forderungen verrechnet bzw. "
                "auf das uns bekannte Konto erstattet.<br/><br/>"
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

        # Produktkatalog + Offer-Settings (für Textbausteine + AGB bei Angebot + Auftragsbestätigung)
        products_lookup = None
        offer_settings = None
        if doc_type in ("customer_quote", "order_confirmation"):
            try:
                prod_path = self.data_dir / "products.json"
                if prod_path.exists():
                    raw = json.loads(prod_path.read_text("utf-8"))
                    products_lookup = {p.get("art_nr"): p for p in raw if p.get("art_nr")}
            except Exception:
                products_lookup = None
            try:
                os_path = self.data_dir / "offer_settings.json"
                if os_path.exists():
                    offer_settings = json.loads(os_path.read_text("utf-8"))
                    # Absoluten Pfad zur AGB-PDF mitgeben, wenn agb_mode='pdf'
                    if offer_settings.get("agb_mode") == "pdf":
                        fname = offer_settings.get("agb_pdf_filename") or "AGB.pdf"
                        offer_settings["_agb_pdf_path"] = str(self.data_dir / "agb" / fname)
                    # Absoluten Pfad zur Dienstvertrag-PDF mitgeben, wenn vertrag_mode='pdf'
                    if offer_settings.get("vertrag_mode") == "pdf":
                        vfname = offer_settings.get("vertrag_pdf_filename") or "Dienstvertrag.pdf"
                        offer_settings["_vertrag_pdf_path"] = str(self.data_dir / "vertrag" / vfname)
            except Exception:
                offer_settings = None

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
            products_lookup=products_lookup,
            offer_settings=offer_settings,
            prepaid_invoices=getattr(self, "_prepaid_invoices", None),
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
