#!/usr/bin/env python3
"""PDF-Erzeugung zu einer Rechnung — als eigenes Modul, damit sowohl die laufende
App als auch Wartungsskripte dasselbe PDF erzeugen (31.07.2026).

Die Abhängigkeiten (DocumentGenerator, Kundenstamm, Mandanteneinstellungen)
werden hereingereicht, damit hier NICHTS aus webapp importiert werden muss —
ein webapp-Import im Zweitprozess löst auto_save aus und leert invoices.json.
"""


# UN/ECE-Einheitencodes lesbar machen. Die Rechnung speichert den Code (C62 =
# Stueck), im PDF stand er bisher woertlich in der Spalte „Einh." — „C62".
_EINHEITEN = {
    "C62": "Stk", "H87": "Stk", "PCE": "Stk", "EA": "Stk",
    "LS": "Pauschal", "HUR": "Std", "DAY": "Tag", "MON": "Monat",
    "MTR": "m", "MTK": "m\u00b2", "MTQ": "m\u00b3", "KGM": "kg",
    "TNE": "t", "LTR": "l", "KWH": "kWh", "SET": "Satz", "P1": "%",
}


def ist_storno(inv):
    """Storno (Vollgutschrift zur Entwertung, Nummer ST-…) im Unterschied zur
    Teil-/Kulanzgutschrift (GS-…). Beide sind Typ 381; die Texte unterscheiden sich."""
    if getattr(inv, "invoice_type_code", "380") != "381":
        return False
    nr = (getattr(inv, "invoice_number", "") or "").upper()
    if nr.startswith("ST-"):
        return True
    return any(getattr(e, "event_type", "") == "STORNO_ERSTELLT"
               for e in (getattr(inv, "audit_trail", None) or []))


def _einheit(code):
    """Anzeigename zu einem Einheitencode (unbekannte Codes bleiben stehen)."""
    c = (str(code or "").strip() or "C62").upper()
    return _EINHEITEN.get(c, str(code or "").strip())


def build(inv, doc_gen, buyers, mandant_settings):
    """Erzeugt das PDF zur Rechnung (oder Storno) und liefert (filename, bytes).

    Wird sowohl vom Send-Endpoint (E-Mail-Anhang) als auch vom PDF-Download-
    Endpoint genutzt. Wirft eine Exception, wenn die Erzeugung fehlschlägt.
    Dateinamen-Muster: <Rechnungsnummer>.pdf (Slashes werden zu Unterstrich).
    """
    positions = []
    for i, line in enumerate(inv.lines):
        positions.append({
            "pos_nr": i + 1,
            "description": line.item_name,
            "quantity": line.quantity,
            "unit": _einheit(line.unit_code),
            "unit_price": line.unit_price,
            "net_amount": line.line_net_amount,
            "tax_rate": line.tax_rate,
            "discount_percent": 0,
            "discount_amount": 0,
        })
    # Anrede/Ansprechpartner aus dem Kundenstamm nachschlagen (die Rechnung
    # selbst trägt keine salutation) — Match über Referenz, sonst Name.
    _salut, _contact, _private, _kontakt_salut = "", "", False, ""
    try:
        _ref = (getattr(inv, "buyer_reference", "") or
                getattr(inv.buyer, "buyer_reference", "") or "").strip()
        _bname = (inv.buyer.name or "").strip()
        for _bb in (buyers or []):
            if (_ref and _bb.get("reference") == _ref) or \
               (_bname and _bb.get("name") == _bname):
                _salut = _bb.get("salutation", "") or ""
                _contact = _bb.get("contact_name", "") or ""
                # Anrede des Ansprechpartners — hat in der Briefanrede Vorrang
                # vor der Adresszeilen-Anrede (07.09.2026)
                _kontakt_salut = _bb.get("contact_salutation", "") or ""
                _private = bool(_bb.get("is_private"))
                break
    except Exception:
        pass
    buyer_data = {
        "name": inv.buyer.name,
        "salutation": _salut,
        "contact_salutation": _kontakt_salut,
        "contact_name": _contact,
        "is_private": _private,
        "street": inv.buyer.address.street if inv.buyer.address else "",
        "house_number": inv.buyer.address.house_number if inv.buyer.address else "",
        "city": inv.buyer.address.city if inv.buyer.address else "",
        "post_code": inv.buyer.address.post_code if inv.buyer.address else "",
    }
    templates = (mandant_settings or {}).get("doc_templates", {})
    is_credit = inv.invoice_type_code == "381"
    is_storno = ist_storno(inv)
    if is_storno:
        doc_type_for_pdf = "storno"
        intro_tpl = templates.get("storno_intro", "")
        closing_tpl = templates.get("storno_closing", "")
    elif is_credit:
        doc_type_for_pdf = "gutschrift"
        intro_tpl = templates.get("gutschrift_intro", "")
        closing_tpl = templates.get("gutschrift_closing", "")
        if not closing_tpl:
            # Ohne Vorlage wuerde die Notiz (= Grund) den Schlusstext ERSETZEN und
            # die Grussformel fiele weg — daher hier den Standardschluss anfuegen.
            _s_name = " ".join((getattr(inv.seller, "name", "") or "").split())
            closing_tpl = ("Der Gutschriftsbetrag wird mit offenen Forderungen verrechnet "
                           "bzw. auf das uns bekannte Konto erstattet.\n\n"
                           f"Mit freundlichen Grüßen\n{_s_name}")
    else:
        doc_type_for_pdf = "invoice"
        intro_tpl = templates.get("invoice_intro", "")
        closing_tpl = templates.get("invoice_closing", "")
    # Rechnungs-Notiz (inv.note, aus dem Notizfeld im Erstellen-Formular) als
    # Bemerkung aufs PDF — eigener Absatz vor dem Schlusstext; steht auch im XML.
    # Bei der Gutschrift traegt die Notiz den Grund — der gehoert aufs Papier.
    _note = (getattr(inv, "note", "") or "").strip()
    if _note and not is_storno:
        closing_tpl = _note + ("\n\n" + closing_tpl if closing_tpl else "")
    # Anzahlungen: dieselben Abschlaege, die in BT-113 der XRechnung stehen,
    # gehoeren auch aufs Papier — sonst fordert das PDF den vollen Betrag und
    # die XRechnung den Rest (oder umgekehrt). doc_generator liest sie ueber
    # das Feld _prepaid_invoices und zieht sie vom Bruttobetrag ab.
    _vorher = getattr(doc_gen, "_prepaid_invoices", None)
    try:
        _vorab = float(getattr(inv, "prepaid_amount", 0) or 0)
    except Exception:
        _vorab = 0.0
    if _vorab > 0 and doc_type_for_pdf == "invoice":
        doc_gen._prepaid_invoices = list(getattr(inv, "prepaid_details", None) or [{
            "reference": "", "title": "Bereits geleistete Anzahlungen",
            "date": "", "amount_gross": _vorab, "status": "PAID",
        }])
    else:
        doc_gen._prepaid_invoices = None
    pdf_entry = doc_gen.generate(
        doc_type=doc_type_for_pdf,
        recipient=buyer_data,
        positions=positions,
        step={"date": inv.invoice_date},
        reference=inv.invoice_number,
        subject="",
        doc_date=inv.invoice_date,
        due_date=(inv.payment.due_date if inv.payment and inv.payment.due_date else ""),
        delivery_date=(inv.tax_point_date or inv.period_end or ""),
        intro_text=intro_tpl,
        closing_text=closing_tpl,
        original_invoice_ref=(inv.preceding_invoice or "") if is_credit else "",
    )
    doc_gen._prepaid_invoices = _vorher      # Zustand nicht am Generator zuruecklassen
    pdf_path = doc_gen.get_filepath(pdf_entry["filename"])
    if not pdf_path or not pdf_path.exists():
        raise RuntimeError("PDF-Datei nach Generierung nicht gefunden")
    filename = f"{inv.invoice_number.replace('/', '_')}.pdf"
    return filename, pdf_path.read_bytes()
