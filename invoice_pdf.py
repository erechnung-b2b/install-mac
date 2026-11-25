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
    "TNE": "t", "LTR": "l", "KWH": "kWh", "SET": "Satz", "P1": "%", "KMT": "km",
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


def _norm_name(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def verkaeufer2_name(mandant_settings) -> str:
    """Name des zweiten Verkäufers: Stammdaten-Name (oder eigener Name) + Zusatz in einer zweiten Zeile.
    Leer, wenn kein zweiter Verkäufer angelegt ist (mandant_settings.json → "verkaeufer2")."""
    ms = mandant_settings or {}
    v2 = ms.get("verkaeufer2") or {}
    zus = str(v2.get("zusatz") or "").strip()
    name = str(v2.get("name") or ms.get("name") or "").strip()
    if not (zus and name):
        return ""
    return name + "\n" + zus


def _de_datum(d) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _zeitraum_text(von, bis) -> str:
    if von and bis:
        return f"{_de_datum(von)} – {_de_datum(bis)}"
    return ("ab " + _de_datum(von)) if von else (("bis " + _de_datum(bis)) if bis else "")


def _eur(wert) -> str:
    t = f"{wert:,.2f}"
    return t.replace(",", "X").replace(".", ",").replace("X", ".")


def _positionstext(line) -> str:
    """Text der Position für das PDF: Bezeichnung (BT-153); darunter klein die Beschreibung (BT-154), der
    Leistungszeitraum der Position (BT-134/135) und Rabatt/Zuschlag (BG-27/28). Ohne diese Angaben bleibt es bei der
    Bezeichnung, unverändert wie bisher."""
    from xml.sax.saxutils import escape
    name = line.item_name
    zusatz = []
    beschr = " ".join(str(getattr(line, "item_description", "") or "").split())
    if beschr and beschr != " ".join(str(name or "").split()) and not beschr.startswith("Storno: "):
        zusatz.append(escape(beschr))
    zt = _zeitraum_text(getattr(line, "period_start", None), getattr(line, "period_end", None))
    if zt:
        zusatz.append("Leistungszeitraum: " + zt)
    for a in (getattr(line, "allowances_charges", None) or []):
        art = "zzgl." if a.is_charge else "abzgl."
        proz = f" {a.percentage.normalize():f} %".replace(".", ",") if a.percentage else ""
        zusatz.append(escape(f"{art} {a.reason or ('Zuschlag' if a.is_charge else 'Rabatt')}{proz}: {_eur(a.amount)} EUR"))
    if not zusatz:
        return name
    return f'{name}<br/><font size="7.5" color="#555555">' + "<br/>".join(zusatz) + "</font>"


def _rabatt_prozent(line):
    """Rabatt in Prozent für die Spalte „Rab.%“ — nur wenn die Position genau einen prozentualen Rabatt trägt."""
    acs = list(getattr(line, "allowances_charges", None) or [])
    if len(acs) == 1 and not acs[0].is_charge and acs[0].percentage:
        return float(acs[0].percentage)
    return 0


def _summen_angaben(inv) -> dict:
    """Angaben für den Summenblock und den Kopf, die es erst seit Runde 2 gibt. Leer bei einer Rechnung ohne sie."""
    aus = {}
    if inv.allowances_charges:
        nachl = [a for a in inv.allowances_charges if not a.is_charge]
        zuschl = [a for a in inv.allowances_charges if a.is_charge]
        def _label(liste, wort):
            gruende = []
            for a in liste:
                g = " ".join(str(a.reason or "").split())
                if a.percentage:
                    g = (g + " " if g else "") + f"{a.percentage.normalize():f} %".replace(".", ",")
                if g and g not in gruende:
                    gruende.append(g)
            from xml.sax.saxutils import escape
            return escape(f"{wort} ({', '.join(gruende)}):" if gruende and gruende != [wort] else f"{wort}:")
        if nachl:
            aus["doc_discount_amount"] = inv.sum_allowances()
            aus["doc_discount_label"] = _label(nachl, "Nachlass")
        if zuschl:
            aus["doc_surcharge_amount"] = inv.sum_charges()
            aus["doc_surcharge_label"] = _label(zuschl, "Zuschlag")
        aus["steuer_gruppen"] = [(str(t.rate), str(t.taxable_amount), str(t.tax_amount)) for t in inv.compute_tax_subtotals()]
    stufen = inv.payment.skonto_stufen() if (inv.payment and hasattr(inv.payment, "skonto_stufen")) else []
    if len(stufen) == 2:
        aus["skonto_percent"], aus["skonto_days"] = stufen[0]
        aus["skonto2_percent"], aus["skonto2_days"] = stufen[1]
    if str(inv.invoice_type_code) == "384":
        aus["titel"] = "Rechnungskorrektur"
    return aus


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
            "description": _positionstext(line),
            "quantity": line.quantity,
            "unit": _einheit(line.unit_code),
            "unit_price": line.unit_price,
            "net_amount": line.line_net_amount,
            "tax_rate": line.tax_rate,
            "discount_percent": _rabatt_prozent(line),
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
        "address_line_2": (getattr(inv.buyer.address, "address_line_2", "") or "") if inv.buyer.address else "",
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
                                  "verwendungszweck": _na.get("verwendungszweck", "") or (getattr(inv.payment, "remittance", "") or "")}
                                 if (_na.get("abo_id") or _inv_iban or getattr(inv.payment, "remittance", "")) else None)
    _lieferdatum = (inv.tax_point_date or inv.period_end or "")
    if _na.get("abo_id") and doc_type_for_pdf == "invoice":
        # Die Standardtexte (§ 4.2 AGB, „Rechnungsnummer als Verwendungszweck") widersprechen hier: gezahlt wird
        # monatlich im Voraus mit dem Nutzercode. Der Leistungszeitraum steht im Vermerk, nicht als ein Datum.
        _s_name = " ".join((getattr(inv.seller, "name", "") or "").split())
        _von = inv.period_start.strftime("%d.%m.%Y") if inv.period_start else ""
        _bis = inv.period_end.strftime("%d.%m.%Y") if inv.period_end else ""
        _eigen = str(_na.get("dienst") or "").strip()      # eigenes Abo-Produkt (dauer_texte.DAUER_PROFILE): eigener Name, ohne die Standard-Bedingungen
        _dienst_von = f" von {_eigen}" if _eigen else _dt.dienst('von')
        _agb_satz = str(_na.get("agb_satz") or "") if _eigen else (_dt.DAUER_TEXT.get("agb_satz") or "")
        intro_tpl = ("Sehr geehrte Damen und Herren,\n\n"
                     f"für den Online-Zugang{_dienst_von} stellen wir Ihnen das vereinbarte Nutzungsentgelt in Rechnung"
                     + (f" (Leistungszeitraum {_von} bis {_bis}, danach monatlich)." if _von and _bis else ".")
                     + (("\n" + _agb_satz) if _agb_satz else ""))
        closing_tpl = (_note + "\n\n" if _note else "") + (
            "Das Entgelt ist jeweils zum 28. für den folgenden Nutzungsmonat im Voraus fällig" + ("" if _eigen else " (§ N5 Abs. 2)") + ". "
            f"Bitte überweisen Sie auf das unten genannte Konto und geben Sie als Verwendungszweck nur Ihren Nutzercode "
            f"{_na.get('verwendungszweck', '')} an — nicht die Rechnungsnummer. Ein Dauerauftrag ist dafür am bequemsten.\n\n"
            f"Mit freundlichen Grüßen\n{_s_name}")
        _lieferdatum = ""
    # Zweiter Verkäufer (16.11.2026): lautet die Rechnung auf ihn, trägt auch das PDF seinen Namen
    _v2n = verkaeufer2_name(mandant_settings)
    _ist_v2 = bool(_v2n) and _norm_name(getattr(inv.seller, "name", "")) == _norm_name(_v2n)
    if _ist_v2:
        _zus = _v2n.split("\n", 1)[1].strip() if "\n" in _v2n else ""
        _zeilen = (closing_tpl or "").rstrip().split("\n")
        if _zus and _zeilen and _norm_name(_zeilen[-1]) == _norm_name((mandant_settings or {}).get("name", "")):
            closing_tpl = (closing_tpl or "").rstrip() + "\n" + _zus      # Grußformel: Zusatz unter dem Namen
        closing_tpl = (closing_tpl or "").replace(" ".join(_v2n.split()), _v2n)   # einzeilig gesetzter Name → zwei Zeilen
    doc_gen._seller_override = {"name": _v2n} if _ist_v2 else None
    import doc_generator as _dg
    _terms = str((inv.payment.payment_terms if inv.payment else "") or "")
    _dg._BEZAHLT_HINWEIS = _terms if (_terms.startswith("Bereits bezahlt") and not is_credit) else None
    # Lastschrift: Einzugshinweis mit Mandat und Gläubiger-ID statt Überweisungskonto und GiroCode
    _dg._BEZAHLT_HINWEIS = __import__("rechnungszusatz").pdf_zahlhinweis(inv, _dg._BEZAHLT_HINWEIS)
    # Steuerfall und Bezüge (25.11.2026): Hinweis nach § 19 / § 13b / Befreiungsgrund unter den Summen;
    # Bestell-, Vertrags-, Projektnummer und eine echte Leitweg-ID in der Kopfzeile.
    _steuer_pdf = {"steuer_hinweis": ""}
    try:
        _steuer_pdf["steuer_hinweis"] = inv.steuerhinweis() if hasattr(inv, "steuerhinweis") else ""
        _steuer_pdf["steuerfall"] = getattr(inv, "steuerfall", "regel") or "regel"
        _bez = [("Ihre Bestellung", getattr(inv, "order_reference", "") or ""),
                ("Vertrag", getattr(inv, "contract_reference", "") or ""),
                ("Projekt", getattr(inv, "project_reference", "") or "")]
        from validator import leitweg_pruefen as _lw
        _kref = (getattr(inv, "buyer_reference", "") or "").strip()
        if _kref and _lw(_kref) == (True, ""):
            _bez.append(("Leitweg-ID", _kref))
        if getattr(inv, "period_start", None) and not _na.get("abo_id"):      # BG-14 Rechnungszeitraum
            _bez.insert(0, ("Leistungszeitraum", _zeitraum_text(inv.period_start, inv.period_end)))
        if str(inv.invoice_type_code) == "384" and inv.preceding_invoice:
            _bez.insert(0, ("Berichtigt Rechnung", inv.preceding_invoice
                            + (" vom " + _de_datum(inv.preceding_invoice_date) if getattr(inv, "preceding_invoice_date", None) else "")))
        _steuer_pdf["bezuege"] = [(k, v) for k, v in _bez if v]
    except Exception:
        pass
    try:
        _steuer_pdf.update(_summen_angaben(inv))
        if getattr(inv, "period_start", None) and _lieferdatum and _lieferdatum == inv.period_end and not _na.get("abo_id"):
            _lieferdatum = ""       # der Zeitraum steht in der zweiten Kopfzeile, sein Ende nicht noch einmal als Leistungsdatum
    except Exception as _e_r2a:
        print(f"[invoice_pdf] Zusatzangaben nicht übernommen: {_e_r2a}", flush=True)
    closing_tpl = __import__("rechnungszusatz").pdf_angaben(inv, buyer_data, _steuer_pdf, closing_tpl)
    try:
      pdf_entry = doc_gen.generate(
        doc_type=doc_type_for_pdf,
        recipient=buyer_data,
        positions=positions,
        step={"date": inv.invoice_date,
              "skonto_percent": getattr(inv.payment, "skonto_percent", 0) if inv.payment else 0,
              "skonto_days": getattr(inv.payment, "skonto_days", 0) if inv.payment else 0,
              **_steuer_pdf},
        reference=inv.invoice_number,
        subject="",
        doc_date=inv.invoice_date,
        due_date=(inv.payment.due_date if inv.payment and inv.payment.due_date else ""),
        delivery_date=_lieferdatum,
        intro_text=intro_tpl,
        closing_text=closing_tpl,
        original_invoice_ref=(inv.preceding_invoice or "") if is_credit else "",
      )
    finally:
        _dg._BEZAHLT_HINWEIS = None
    doc_gen._prepaid_invoices = _vorher      # Zustand nicht am Generator zuruecklassen
    doc_gen._zahlung_override = None
    doc_gen._seller_override = None
    pdf_path = doc_gen.get_filepath(pdf_entry["filename"])
    if not pdf_path or not pdf_path.exists():
        raise RuntimeError("PDF-Datei nach Generierung nicht gefunden")
    filename = f"{inv.invoice_number.replace('/', '_')}.pdf"
    return filename, pdf_path.read_bytes()
