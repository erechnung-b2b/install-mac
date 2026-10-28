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
    # Die Absetz-Zeilen der Abschlaege (fuer BT-22 der XRechnung) stehen auf dem
    # Papier als Tabelle — hier nicht noch einmal als Fliesstext.
    _note = "\n".join(z for z in _note.split("\n")
                      if not z.startswith(("Abzüglich Abschlagsrechnung",
                                           "Verbleibender Zahlbetrag der Schlussrechnung"))).strip()
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
    # Dauerrechnung Online-Zugang: eigenes Konto + Nutzercode als Verwendungszweck (siehe doc_generator._zahlung_override)
    import dauer_texte as _dt
    _na = getattr(inv, "_nutzerabo", None) or {}
    # Zahlkonto der Rechnung selbst (27.10.2026): steht in inv.payment ein Konto, druckt das PDF DIESES Konto — sonst
    # zeigte es die Stammdaten, während XRechnung und GiroCode das Konto der Rechnung trugen (Zweitkonto je Rechnung).
    _inv_iban = (inv.payment.iban if inv.payment else "") or ""
    doc_gen._zahlung_override = ({"iban": _inv_iban, "bic": (inv.payment.bic if inv.payment else "") or "",
                                  "verwendungszweck": _na.get("verwendungszweck", "")} if (_na.get("abo_id") or _inv_iban) else None)
    _lieferdatum = (inv.tax_point_date or inv.period_end or "")
    if _na.get("abo_id") and doc_type_for_pdf == "invoice":
        # Die Standardtexte (§ 4.2 AGB, „Rechnungsnummer als Verwendungszweck") widersprechen hier: gezahlt wird
        # monatlich im Voraus mit dem Nutzercode. Der Leistungszeitraum steht im Vermerk, nicht als ein Datum.
        _s_name = " ".join((getattr(inv.seller, "name", "") or "").split())
        _von = inv.period_start.strftime("%d.%m.%Y") if inv.period_start else ""
        _bis = inv.period_end.strftime("%d.%m.%Y") if inv.period_end else ""
        intro_tpl = ("Sehr geehrte Damen und Herren,\n\n"
                     f"für den Online-Zugang{_dt.dienst('von')} stellen wir Ihnen das vereinbarte Nutzungsentgelt in Rechnung"
                     + (f" (Leistungszeitraum {_von} bis {_bis}, danach monatlich)." if _von and _bis else ".")
                     + (("\n" + _dt.DAUER_TEXT["agb_satz"]) if _dt.DAUER_TEXT.get("agb_satz") else ""))
        closing_tpl = (_note + "\n\n" if _note else "") + (
            "Das Entgelt ist jeweils zum 28. für den folgenden Nutzungsmonat im Voraus fällig (§ N5 Abs. 2). "
            f"Bitte überweisen Sie auf das unten genannte Konto und geben Sie als Verwendungszweck nur Ihren Nutzercode "
            f"{_na.get('verwendungszweck', '')} an — nicht die Rechnungsnummer. Ein Dauerauftrag ist dafür am bequemsten.\n\n"
            f"Mit freundlichen Grüßen\n{_s_name}")
        _lieferdatum = ""
    pdf_entry = doc_gen.generate(
        doc_type=doc_type_for_pdf,
        recipient=buyer_data,
        positions=positions,
        step={"date": inv.invoice_date},
        reference=inv.invoice_number,
        subject="",
        doc_date=inv.invoice_date,
        due_date=(inv.payment.due_date if inv.payment and inv.payment.due_date else ""),
        delivery_date=_lieferdatum,
        intro_text=intro_tpl,
        closing_text=closing_tpl,
        original_invoice_ref=(inv.preceding_invoice or "") if is_credit else "",
    )
    doc_gen._prepaid_invoices = _vorher      # Zustand nicht am Generator zuruecklassen
    doc_gen._zahlung_override = None
    pdf_path = doc_gen.get_filepath(pdf_entry["filename"])
    if not pdf_path or not pdf_path.exists():
        raise RuntimeError("PDF-Datei nach Generierung nicht gefunden")
    filename = f"{inv.invoice_number.replace('/', '_')}.pdf"
    return filename, pdf_path.read_bytes()
