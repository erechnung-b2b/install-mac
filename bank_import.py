#!/usr/bin/env python3
"""
Bank-Import — CAMT.053 (ISO 20022) UND deutsche Bank-CSV-Exporte.

Liest XML-Kontoauszüge, PDF-Auszüge ODER CSV-Dateien ein und extrahiert Buchungen
als strukturierte Datensätze. CSV-Heuristik deckt typische deutsche
Banken ab (Postbank, Sparkasse, DKB, Commerzbank, ING, Volksbank, …).
"""
from __future__ import annotations
import csv
import io
import re
import uuid
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET


def _ln(tag: str) -> str:
    """Strippt Namespace, liefert reinen Tag-Namen ('{urn:...}Ntry' → 'Ntry')."""
    return tag.split('}')[1] if '}' in tag else tag


def _find(elem, *path):
    """Findet Kind-Element per Local-Name-Pfad. Erstes Treffer-Element zurück."""
    cur = elem
    for name in path:
        nxt = None
        for child in cur:
            if _ln(child.tag) == name:
                nxt = child
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _find_all(elem, name):
    """Alle Elemente mit Local-Name name (nicht rekursiv)."""
    return [c for c in elem if _ln(c.tag) == name]


def _find_all_deep(elem, name):
    """Alle Elemente mit Local-Name (rekursiv)."""
    return [e for e in elem.iter() if _ln(e.tag) == name]


def _text(elem, *path):
    """Liefert Text des Elements unter path oder None."""
    e = _find(elem, *path) if path else elem
    if e is None:
        return None
    t = e.text or ''
    return t.strip() or None


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _decimal(s):
    if not s:
        return None
    try:
        return Decimal(str(s).replace(',', '.'))
    except Exception:
        return None


def _join_text_list(items, sep=' '):
    """Reduziert eine Liste optionaler Strings auf einen Text."""
    return sep.join(filter(None, [s.strip() if s else '' for s in items])).strip() or None


def parse_camt053(xml_bytes_or_path):
    """Parst CAMT.053 — gibt Liste Buchungen + Statement-Header zurück.

    Returns:
        {
            "statement": {"account_iban": str|None, "creation_date": str|None,
                          "from_date": str|None, "to_date": str|None,
                          "opening_balance": Decimal|None, "closing_balance": Decimal|None},
            "transactions": [{...}]  # je Ntry ein dict
        }
    """
    if isinstance(xml_bytes_or_path, (bytes, bytearray)):
        root = ET.fromstring(xml_bytes_or_path)
    elif isinstance(xml_bytes_or_path, (str, Path)) and Path(xml_bytes_or_path).exists():
        root = ET.parse(str(xml_bytes_or_path)).getroot()
    else:
        # String (XML-Inhalt)
        root = ET.fromstring(xml_bytes_or_path)

    stmts = _find_all_deep(root, 'Stmt')
    if not stmts:
        raise ValueError("Keine <Stmt>-Elemente — ist das wirklich CAMT.053?")

    transactions = []
    statement_meta = {}

    for stmt in stmts:
        # Account-IBAN
        iban_el = _find_all_deep(stmt, 'IBAN')
        account_iban = iban_el[0].text.strip() if iban_el and iban_el[0].text else None

        # Statement Period
        from_dt = _text(stmt, 'FrToDt', 'FrDtTm')
        to_dt = _text(stmt, 'FrToDt', 'ToDtTm')
        if not statement_meta:
            statement_meta = {
                "account_iban": account_iban,
                "creation_date": _text(stmt, 'CreDtTm'),
                "from_date": (from_dt or '')[:10] or None,
                "to_date": (to_dt or '')[:10] or None,
            }

            # Opening / Closing Balance — über alle Bal-Elemente (CdOrType: OPBD, CLBD)
            for bal in _find_all(stmt, 'Bal'):
                tp_cd = _text(bal, 'Tp', 'CdOrPrtry', 'Cd')
                amt = _decimal(_text(bal, 'Amt'))
                if tp_cd == 'OPBD':
                    statement_meta['opening_balance'] = amt
                elif tp_cd in ('CLBD', 'CLAV'):
                    statement_meta['closing_balance'] = amt

        # Buchungen
        for ntry in _find_all(stmt, 'Ntry'):
            try:
                t = _parse_ntry(ntry, account_iban)
                if t:
                    transactions.append(t)
            except Exception as e:
                print(f"[bank-import] Warning: Buchung übersprungen: {e}")

    return {"statement": statement_meta, "transactions": transactions}


def _parse_ntry(ntry, account_iban):
    """Wandelt ein <Ntry>-Element in unser Buchungs-Dict um.

    Falls die Buchung mehrere TxDtls (Sammelbuchung) hat, geben wir trotzdem
    eine konsolidierte Buchung zurück — alle Details werden in den
    counterparties / purpose-Felder gemerged.
    """
    amt_el = _find(ntry, 'Amt')
    amount = _decimal(amt_el.text) if amt_el is not None else None
    currency = (amt_el.attrib.get('Ccy') or 'EUR') if amt_el is not None else 'EUR'
    cdt_dbt = _text(ntry, 'CdtDbtInd')  # CRDT (eingehend) oder DBIT (ausgehend)
    if amount is None or cdt_dbt is None:
        return None

    booking_date = _parse_date(_text(ntry, 'BookgDt', 'Dt')) or _parse_date(_text(ntry, 'BookgDt', 'DtTm'))
    value_date = _parse_date(_text(ntry, 'ValDt', 'Dt')) or _parse_date(_text(ntry, 'ValDt', 'DtTm'))
    bank_ref = _text(ntry, 'AcctSvcrRef')
    addtl_info = _text(ntry, 'AddtlNtryInf')

    # Aus NtryDtls/TxDtls die Counterparty + RmtInf extrahieren
    counterparty_name = None
    counterparty_iban = None
    counterparty_bic = None
    purpose_parts = []
    end_to_end_id = None
    transaction_id = None

    tx_dtls_list = _find_all_deep(ntry, 'TxDtls')
    is_credit = (cdt_dbt == 'CRDT')

    for tx in tx_dtls_list:
        # End-to-End-ID
        if not end_to_end_id:
            end_to_end_id = _text(tx, 'Refs', 'EndToEndId')
        if not transaction_id:
            transaction_id = _text(tx, 'Refs', 'TxId') or _text(tx, 'Refs', 'AcctSvcrRef')

        # Related Parties: bei CRDT ist die Counterparty der Debtor (zahlt an uns),
        # bei DBIT ist es der Creditor (empfängt von uns).
        rltd_pties = _find(tx, 'RltdPties')
        if rltd_pties is not None:
            party_key = 'Dbtr' if is_credit else 'Cdtr'
            party = _find(rltd_pties, party_key)
            if party is not None:
                counterparty_name = counterparty_name or _text(party, 'Pty', 'Nm') or _text(party, 'Nm')
            acct_key = 'DbtrAcct' if is_credit else 'CdtrAcct'
            acct = _find(rltd_pties, acct_key)
            if acct is not None:
                counterparty_iban = counterparty_iban or _text(acct, 'Id', 'IBAN')

        rltd_agts = _find(tx, 'RltdAgts')
        if rltd_agts is not None:
            agent_key = 'DbtrAgt' if is_credit else 'CdtrAgt'
            agt = _find(rltd_agts, agent_key)
            if agt is not None:
                counterparty_bic = counterparty_bic or _text(agt, 'FinInstnId', 'BICFI') or _text(agt, 'FinInstnId', 'BIC')

        # Verwendungszweck — Ustrd kann mehrfach vorkommen
        rmt = _find(tx, 'RmtInf')
        if rmt is not None:
            for ustrd in _find_all(rmt, 'Ustrd'):
                if ustrd.text and ustrd.text.strip():
                    purpose_parts.append(ustrd.text.strip())

    # Fallback: auch direkt unter Ntry kann ein "AddtlTxInf" stehen
    addtl_tx_inf_list = _find_all_deep(ntry, 'AddtlTxInf')
    for el in addtl_tx_inf_list:
        if el.text and el.text.strip() and el.text.strip() not in purpose_parts:
            purpose_parts.append(el.text.strip())

    purpose = _join_text_list(purpose_parts, ' ')

    return {
        "id": uuid.uuid4().hex,
        "booking_date": booking_date.isoformat() if booking_date else None,
        "value_date": value_date.isoformat() if value_date else None,
        "amount": float(amount),
        "currency": currency,
        "direction": "credit" if is_credit else "debit",  # eingehend / ausgehend
        "account_iban": account_iban,
        "counterparty_name": counterparty_name,
        "counterparty_iban": counterparty_iban,
        "counterparty_bic": counterparty_bic,
        "purpose": purpose,
        "additional_info": addtl_info,
        "end_to_end_id": end_to_end_id,
        "bank_reference": bank_ref,
        "transaction_id": transaction_id,
        "match_status": "open",
        "matched_invoice_id": None,
        "matched_at": None,
        "match_confidence": None,
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


# ─────────────────────────────────────────────────────────────────
# Auto-Match-Logik
# ─────────────────────────────────────────────────────────────────

INVOICE_NUMBER_PATTERNS = [
    re.compile(r'\b(?:RE|RG|R|INV|RNR)[-_/]?\s?(\d{2,4})[-_/]?\s?(\d{2,8})(?:[-_/]\s?[A-Z]\d{1,4})?\b', re.IGNORECASE),
    re.compile(r'\b(?:Rechnung(?:s-?Nr\.?)?|Rg-?Nr\.?|Invoice)\s*[:#]?\s*([A-Z0-9/_-]{3,30})', re.IGNORECASE),
]


def find_invoice_numbers_in_text(text):
    """Sucht im Verwendungszweck nach Rechnungsnummern. Gibt Liste der Treffer zurück."""
    if not text:
        return []
    candidates = set()
    for pat in INVOICE_NUMBER_PATTERNS:
        for m in pat.finditer(text):
            grp = m.group(0).strip()
            candidates.add(grp.upper())
    return sorted(candidates)


def _significant_numbers(s, min_len=3):
    """Alle Zahlenfolgen ab min_len Ziffern. Führende Nullen bleiben (00002 ≠ 2)."""
    if not s:
        return set()
    return set(re.findall(r'\d{' + str(min_len) + r',}', str(s)))


def _ist_jahreszahl(n):
    """1990–2099 als reine Jahresangabe — taugt nicht als Zuordnungsmerkmal."""
    t = str(n).lstrip("0") or "0"
    return len(str(n)) == 4 and t.isdigit() and 1990 <= int(t) <= 2099


def _nur_alnum(text):
    """Nur Buchstaben und Ziffern — macht Rechnungsnummern vergleichbar,
    egal ob der Kunde Punkt, Bindestrich oder Leerzeichen schreibt."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _number_overlap_score(invoice_no, purpose_text):
    """Vergleicht Zahlen-Komponenten zwischen Rechnungsnummer und Verwendungszweck.
    Liefert (score, reason).

    Strategie:
    - Aus invoice_no signifikante Zahlen extrahieren (>= 3 Ziffern)
    - Aus purpose dasselbe
    - Schnittmenge bewerten: laufende Nummer (oft 4-6 Ziffern) zählt mehr als Jahr (4 Ziffern)
    """
    if not invoice_no or not purpose_text:
        return 0, ""
    inv_nums = _significant_numbers(invoice_no, 3)
    pur_nums = _significant_numbers(purpose_text, 3)
    if not inv_nums or not pur_nums:
        return 0, ""
    common = inv_nums & pur_nums
    if not common:
        return 0, ""
    # Wenn alle Zahlen-Komponenten der Rechnungsnummer auch im Verwendungszweck stehen
    # → sehr starker Hinweis (z.B. inv "RE-2026-00002" → {2026, 00002}, beide drin)
    if inv_nums.issubset(pur_nums) and len(inv_nums) >= 2:
        return 55, f"alle Rechnungsnummer-Komponenten ({', '.join(sorted(common))}) im Verwendungszweck"
    # Eine längere Zahl (5+ Ziffern, typisch laufende Nummer) ist sehr aussagekräftig
    long_match = [n for n in common if len(n) >= 5]
    if long_match:
        return 50, f"laufende Nummer im Verwendungszweck ({', '.join(long_match)})"
    # Mehrere Zahlen-Matches — mindestens eine davon darf keine Jahreszahl sein
    if len(common) >= 2 and any(not _ist_jahreszahl(n) for n in common):
        return 40, f"mehrere Zahlen-Matches ({', '.join(sorted(common))})"
    # Nur eine 3-4-stellige Zahl. Eine Jahreszahl ist KEIN Hinweis: sie steht in
    # jeder Rechnungsnummer und in fast jedem Verwendungszweck und hat vorher
    # wahllos fremde Rechnungen als Kandidaten hochgezogen (01.09.2026).
    rest = {n for n in common if not _ist_jahreszahl(n)}
    if not rest:
        return 0, ""
    return 15, f"Zahl im Verwendungszweck ({', '.join(sorted(rest))})"


def auto_match(transaction, invoices_iter):
    """Versucht eine Buchung gegen vorhandene Rechnungen zu matchen.

    invoices_iter: Iterable über Invoice-Objekte.

    Returns: {
        "invoice_id": str|None,      # bester Match (nur bei confidence='high')
        "confidence": "high"|"medium"|"low"|None,
        "score": int,
        "reason": str,
        "candidates": [               # Top-Vorschläge zur User-Bestätigung
            {"invoice_id", "invoice_number", "score", "reasons", "amount_diff"},
            ...
        ]
    }
    """
    from datetime import date as _date
    is_credit = (transaction.get("direction") == "credit")
    expected_dir = "AUSGANG" if is_credit else "EINGANG"
    purpose = ' '.join(filter(None, [
        transaction.get("purpose"),
        transaction.get("additional_info"),
        transaction.get("end_to_end_id"),
        transaction.get("bank_reference"),
    ]))
    purpose_upper = purpose.upper()
    cp_iban = (transaction.get("counterparty_iban") or '').replace(' ', '').upper()
    cp_name = (transaction.get("counterparty_name") or '').strip().lower()
    amount = abs(float(transaction.get("amount") or 0))
    # Buchungsdatum für Date-Proximity-Score
    booking_date_str = transaction.get("booking_date") or transaction.get("value_date") or ""
    booking_date = None
    if booking_date_str:
        try:
            booking_date = _date.fromisoformat(booking_date_str[:10])
        except Exception:
            booking_date = None

    # Teilgutschriften (Typ 381 mit preceding_invoice) mindern den Betrag, den der
    # Kunde tatsaechlich ueberweist — die Rechnung wird sonst nie exakt gematcht.
    invoices_list = list(invoices_iter)
    gutgeschrieben = {}
    for g in invoices_list:
        if getattr(g, 'invoice_type_code', '380') != '381':
            continue
        prec = (getattr(g, 'preceding_invoice', '') or '').upper()
        if not prec:
            continue
        try:
            g_gross = float(sum((s.taxable_amount + s.tax_amount)
                                for s in g.compute_tax_subtotals()))
        except Exception:
            g_gross = 0.0
        gutgeschrieben[prec] = round(gutgeschrieben.get(prec, 0.0) + g_gross, 2)

    candidates = []
    for inv in invoices_list:
        inv_dir = getattr(inv, '_direction', '') or ''
        if inv_dir and inv_dir != expected_dir:
            continue
        # Rechnungen ausschließen, die nicht (mehr) bezahlt werden können:
        # bereits bezahlt, abgelehnt/zurückgewiesen, storniert, sowie Gutschriften (Type-Code 381)
        inv_status = getattr(inv, 'status', '') or ''
        if inv_status in ("ZURUECKGEWIESEN", "STORNIERT"):
            continue
        # BEZAHLT wurde frueher ebenfalls uebersprungen — damit konnte eine Buchung
        # ihre Rechnung nie mehr finden, sobald diese (z. B. beim Import) als bezahlt
        # markiert war. Von 121 Eingangsrechnungen tragen 102 diesen Status; die
        # zugehoerigen Buchungen blieben dadurch dauerhaft offen (Befund 31.08.2026).
        # Solche Rechnungen zaehlen jetzt als KANDIDAT, werden aber nie automatisch
        # verknuepft: gleich hohe Rechnungen (z. B. vier Belege ueber 32,75 EUR)
        # wuerden sonst falsch zugeordnet.
        schon_bezahlt = (inv_status == "BEZAHLT")
        if getattr(inv, 'invoice_type_code', '380') == "381":
            continue
        inv_no = (getattr(inv, 'invoice_number', '') or '').upper()
        # Stornorechnungs-Präfix "ST-" auch ausschließen (Gutschriften-Originale)
        if inv_no.startswith("ST-"):
            continue
        gross = float(getattr(inv, 'gross_total', None) or getattr(inv, 'gross', 0) or 0)
        if not gross:
            # Invoice-Objekt führt keinen Bruttobetrag als Feld → aus den
            # Steuer-Teilsummen ableiten (netto + USt je Satz).
            try:
                gross = float(sum((s.taxable_amount + s.tax_amount)
                                  for s in inv.compute_tax_subtotals()))
            except Exception:
                gross = 0.0
        _gut = gutgeschrieben.get(inv_no, 0.0)
        if _gut > 0 and gross > _gut + 0.01:
            gross = round(gross - _gut, 2)   # erwarteter Zahlbetrag nach Teilgutschrift

        # Score
        score = 0
        reasons = []

        # 1) Rechnungsnummer-Match — exakter Hit zuerst (mit Präfix wie RE-2026-00002)
        if inv_no and inv_no in purpose_upper:
            score += 60
            reasons.append("Rechnungsnummer exakt im Verwendungszweck")
        elif (len(_nur_alnum(inv_no)) >= 8
              and _nur_alnum(inv_no) in _nur_alnum(purpose_upper)):
            # Kunden schreiben die Nummer mit Punkt, Leerzeichen oder anderer
            # Trennung: „R.Manz-20260220-02" statt „R-Manz-20260220-02". Ohne
            # Trennzeichen verglichen ist es derselbe Beleg (01.09.2026).
            score += 58
            reasons.append("Rechnungsnummer im Verwendungszweck (andere Schreibweise)")
        else:
            # Toleranter: Zahlen-Komponenten-Match (z.B. "Rechnung 2026-00002" matcht "RE-2026-00002")
            ns, nr = _number_overlap_score(inv_no, purpose_upper)
            if ns > 0:
                score += ns
                reasons.append(nr)

        # 1b) Energieausweisnummer-Match: Vorgangs-/buyer_reference (z.B. EA-…) und
        #     vorläufige Registriernummer (RK-JJJJ-NNNNNN, steht im Rechnungs-Betreff).
        #     Kunden geben diese Nummer oft statt der Rechnungsnummer im Verwendungszweck an.
        ea_refs = []
        _br = (getattr(inv, 'buyer_reference', '') or '').strip().upper()
        if len(_br) >= 5:
            ea_refs.append(_br)
        _note_up = (getattr(inv, 'note', '') or '').upper()
        ea_refs.extend(re.findall(r'RK-\d{4}-\d{4,6}', _note_up))
        # EA-ID auch aus der Notiz ziehen (z.B. "· EA-ID EA-1781089983140"),
        # damit der Match auch greift, wenn buyer_reference eine Vorgangsnummer ist.
        ea_refs.extend(re.findall(r'EA-[0-9A-Z]+(?:-[0-9A-Z]+)*', _note_up))
        ea_hit = next((r for r in ea_refs if r and len(r) >= 6 and r in purpose_upper), None)
        if ea_hit:
            score += 60
            reasons.append(f"Energieausweisnummer im Verwendungszweck ({ea_hit})")

        # 2) Betrag — exakt, fast oder grob in der Nähe
        diff = abs(gross - amount)
        if diff <= 0.02:
            score += 35
            reasons.append(f"Betrag exakt ({amount:.2f}€)" if diff == 0 else f"Betrag ≈ ({diff*100:.0f} ct Rundung)")
        elif diff < 1.0:
            score += 20
            reasons.append(f"Betrag fast (Δ {diff:.2f}€)")
        elif gross > 0 and diff / gross < 0.05:  # < 5% Abweichung (z.B. Teilzahlung, Skonto)
            score += 8
            reasons.append(f"Betrag ähnlich (Δ {diff:.2f}€, ≈{diff/gross*100:.1f}%)")

        # 2b) Datum: Rechnungsdatum ist die Untergrenze, Fälligkeit der Zielpunkt.
        # Eine Zahlung kann nicht vor der Rechnung liegen — das war bisher egal, weil
        # nur der ABSTAND zählte (01.09.2026). Jetzt: vorher = unplausibel,
        # nachher = je näher an Rechnungsdatum/Fälligkeit, desto besser.
        vor_rechnungsdatum = False
        if booking_date:
            def _als_datum(v):
                if isinstance(v, str):
                    try:
                        return _date.fromisoformat(v[:10])
                    except Exception:
                        return None
                return v
            inv_d = _als_datum(getattr(inv, 'invoice_date', None))
            payment = getattr(inv, 'payment', None)
            due_d = _als_datum(getattr(payment, 'due_date', None)) if payment else None

            if inv_d and (booking_date - inv_d).days < -3:
                vor_rechnungsdatum = True
                score -= 25
                reasons.append(f"Zahlung {abs((booking_date - inv_d).days)}d VOR dem "
                               f"Rechnungsdatum ({inv_d.isoformat()})")
            else:
                # Bestes der beiden Bezugsdaten werten
                bester = None
                for ref_d, label in ((due_d, "Fälligkeit"), (inv_d, "Rechnungsdatum")):
                    if not ref_d:
                        continue
                    delta = abs((booking_date - ref_d).days)
                    if bester is None or delta < bester[0]:
                        bester = (delta, label)
                if bester:
                    delta, label = bester
                    if delta <= 7:
                        score += 12
                        reasons.append(f"{label} nahe (±{delta}d)")
                    elif delta <= 30:
                        score += 8
                        reasons.append(f"{label} im Monat (±{delta}d)")
                    elif delta <= 60:
                        score += 4
                        reasons.append(f"{label} innerhalb 60d")

        # 3) Name-Match per Token (Vor- und Nachnamen-Set, tolerant gegen Mittelnamen)
        def _name_score(rechnungs_name):
            if not cp_name or not rechnungs_name:
                return 0
            r = rechnungs_name.strip().lower()
            if cp_name == r:
                return 20  # exakter Match
            cp_tokens = set(re.findall(r"[a-zäöüß]+", cp_name))
            r_tokens = set(re.findall(r"[a-zäöüß]+", r))
            if not cp_tokens or not r_tokens:
                return 0
            common = cp_tokens & r_tokens
            # Wenn alle Tokens des Rechnungsnamens im CP-Namen sind (oder umgekehrt) → Top
            if r_tokens.issubset(cp_tokens) or cp_tokens.issubset(r_tokens):
                return 18
            # Mindestens 2 Tokens gemeinsam (typisch Vor- und Nachname)
            if len(common) >= 2:
                return 15
            # Substring-Fallback (legacy)
            if (len(cp_name) > 6 and cp_name in r) or (len(r) > 6 and r in cp_name):
                return 10
            return 0

        def _zweck_score(rechnungs_name):
            """Steht der Name im BUCHUNGSTEXT statt im Gegenpartei-Feld?

            Bei Lastschriften und Sammelüberweisungen trägt die Bank oft einen
            Zahlungsdienstleister als Gegenpartei ein; der eigentliche Lieferant
            steht nur im Verwendungszweck (01.09.2026). Zählt weniger als ein
            Treffer im Namensfeld, weil der Zweck auch Fremdtexte enthalten kann.
            """
            r = (rechnungs_name or '').strip().lower()
            if len(r) < 4 or not purpose_upper:
                return 0
            zweck = purpose_upper.lower()
            if r in zweck:
                return 12
            tokens = [w for w in re.findall(r"[a-zäöüß]{4,}", r)
                      if w not in ("gmbh", "mbh", "aktiengesellschaft", "kgaa",
                                   "gmbh&co", "limited", "inc", "ltd", "co")]
            treffer = [w for w in tokens if w in zweck]
            if len(treffer) >= 2:
                return 10
            if len(treffer) == 1 and len(treffer[0]) >= 6:
                return 7
            return 0

        name_match_score = 0
        partei = getattr(inv, 'buyer' if is_credit else 'seller', None)
        partei_label = "Käufername" if is_credit else "Verkäufername"
        if partei:
            partei_name = (getattr(partei, 'name', '') or '').strip().lower()
            ns = _name_score(partei_name)
            if ns:
                score += ns
                name_match_score = ns
                reasons.append(f"{partei_label} passt ({ns}p)")
            else:
                # Kein Treffer im Namensfeld → im Buchungstext nachsehen
                zs = _zweck_score(partei_name)
                if zs:
                    score += zs
                    name_match_score = zs
                    reasons.append(f"{partei_label} steht im Buchungstext ({zs}p)")

        # BONUS: Name-Match + exakter Betrag = sehr starkes Indiz, auch ohne
        # Rechnungsnummer im Verwendungszweck. Beispiel: Bank zeigt "Franz Munkhart",
        # Rechnung an "Herr Franz Munkhart" über exakt diesen Betrag.
        if name_match_score >= 15 and diff <= 0.02:
            score += 20
            reasons.append("Bonus: Name + Betrag exakt")

        # IBAN-Match
        if cp_iban:
            inv_iban = ''
            payment = getattr(inv, 'payment', None)
            if payment and getattr(payment, 'iban', None):
                inv_iban = payment.iban.replace(' ', '').upper()
            if inv_iban and cp_iban and inv_iban == cp_iban:
                score += 10
                reasons.append("IBAN-Match")

        # Untere Schwelle gesenkt: 20 reicht, um als Kandidat sichtbar zu werden.
        # Betrag-exakt allein (35) genügt damit für eine Suggestion zur Bestätigung.
        if score >= 20:
            if schon_bezahlt:
                reasons.append("Rechnung ist bereits als bezahlt geführt")
            candidates.append({
                "invoice_id": getattr(inv, '_id', None),
                "invoice_number": inv_no,
                "score": score,
                "reasons": reasons,
                "amount_diff": round(diff, 2),
                "gross": round(gross, 2),
                "schon_bezahlt": schon_bezahlt,
                "vor_rechnungsdatum": vor_rechnungsdatum,
            })

    if not candidates:
        return {"invoice_id": None, "confidence": None, "score": 0,
                 "reason": "kein Match", "candidates": []}

    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]
    # Top-N als Vorschlag mitliefern (max 5, oder solange Score >= 20)
    top_candidates = candidates[:5]

    # Schwellen: high nur wenn Score ≥ 65 UND keine andere Rechnung gleich hoch
    # (sonst Ambiguität → User soll bestätigen)
    second_score = candidates[1]["score"] if len(candidates) > 1 else 0
    ambiguous = (best["score"] - second_score) < 10

    if (best["score"] >= 65 and not ambiguous
            and not best.get("schon_bezahlt")
            and not best.get("vor_rechnungsdatum")):
        conf = "high"
    elif best["score"] >= 35:
        conf = "medium"
    else:
        conf = "low"

    return {
        "invoice_id": best["invoice_id"] if conf == "high" else None,
        "invoice_number": best["invoice_number"],
        "confidence": conf,
        "score": best["score"],
        "reason": "; ".join(best["reasons"]),
        "ambiguous": ambiguous,
        "candidates": top_candidates,
    }


def _inv_gross(inv):
    try:
        return round(float(sum((s.taxable_amount + s.tax_amount)
                               for s in inv.compute_tax_subtotals())), 2)
    except Exception:
        return 0.0


def _names_overlap(cp_name, inv_name):
    if not cp_name or not inv_name:
        return False
    a = set(re.findall(r"[a-zäöüß]+", cp_name.lower()))
    b = set(re.findall(r"[a-zäöüß]+", inv_name.lower()))
    if not a or not b:
        return False
    return (cp_name.lower() == inv_name.lower()
            or a.issubset(b) or b.issubset(a) or len(a & b) >= 2)


def detect_split(transaction, invoices_iter):
    """Erkennt eine Sammelzahlung (eine Buchung deckt MEHRERE offene Rechnungen).

    (a) Verwendungszweck nennt mehrere Rechnungs-/Energieausweisnummern, oder
    (b) mehrere offene Rechnungen DESSELBEN Kunden summieren sich auf den Betrag.

    Returns {invoice_ids, invoice_numbers, sum, difference, basis} oder None.
    """
    if transaction.get("direction") != "credit":
        return None  # Sammelzahlung nur für Zahlungseingänge (Ausgangsrechnungen)
    purpose = ' '.join(filter(None, [
        transaction.get("purpose"), transaction.get("additional_info"),
        transaction.get("end_to_end_id"), transaction.get("bank_reference"),
    ])).upper()
    cp_name = (transaction.get("counterparty_name") or '').strip().lower()
    amount = round(abs(float(transaction.get("amount") or 0)), 2)

    open_invs = []
    for inv in invoices_iter:
        if (getattr(inv, '_direction', '') or '') not in ('', 'AUSGANG'):
            continue
        if getattr(inv, 'status', '') in ('BEZAHLT', 'ZURUECKGEWIESEN', 'STORNIERT'):
            continue
        if getattr(inv, 'invoice_type_code', '380') == '381':
            continue
        if (getattr(inv, 'invoice_number', '') or '').upper().startswith('ST-'):
            continue
        open_invs.append(inv)

    def _refs(inv):
        no = (getattr(inv, 'invoice_number', '') or '').upper()
        refs = [no] if no else []
        br = (getattr(inv, 'buyer_reference', '') or '').strip().upper()
        if len(br) >= 5:
            refs.append(br)
        refs += re.findall(r'RK-\d{4}-\d{4,6}', (getattr(inv, 'note', '') or '').upper())
        return refs

    # (a) Mehrere Referenzen (Rechnungs-/Energieausweisnummer) im Verwendungszweck
    if purpose:
        hits = [inv for inv in open_invs if any(r and r in purpose for r in _refs(inv))]
        if len(hits) >= 2:
            s = round(sum(_inv_gross(i) for i in hits), 2)
            return {
                "invoice_ids": [getattr(i, '_id', None) for i in hits],
                "invoice_numbers": [getattr(i, 'invoice_number', '') for i in hits],
                "sum": s, "difference": round(amount - s, 2), "basis": "referenzen",
            }

    # (b) Gleicher Kunde, eine Kombination summiert sich exakt auf den Betrag
    if cp_name and amount > 0:
        import itertools
        same = [i for i in open_invs
                if _names_overlap(cp_name, (getattr(getattr(i, 'buyer', None), 'name', '') or ''))]
        same = same[:8]  # Kombinatorik begrenzen
        for n in range(2, min(len(same), 4) + 1):
            for combo in itertools.combinations(same, n):
                if abs(round(sum(_inv_gross(i) for i in combo), 2) - amount) < 0.01:
                    return {
                        "invoice_ids": [getattr(i, '_id', None) for i in combo],
                        "invoice_numbers": [getattr(i, 'invoice_number', '') for i in combo],
                        "sum": amount, "difference": 0.0, "basis": "betrag+kunde",
                    }
    return None


# ═══════════════════════════════════════════════════════════════
# CSV-Import — deutsche Banken-Exporte (Postbank, Sparkasse, DKB, …)
# ═══════════════════════════════════════════════════════════════

# Spalten-Aliase: pro canonical Feld eine Liste typischer Header-Bezeichnungen.
# Match wird case-insensitive + ohne Sonderzeichen verglichen.
_CSV_FIELD_ALIASES = {
    "booking_date": [
        "buchungstag", "buchungsdatum", "buchung", "datum", "transaktionsdatum",
        "booking date", "date", "auftragsdatum",
    ],
    "value_date": [
        "wertstellung", "wertstellungsdatum", "valutadatum", "valuta", "value date",
    ],
    "purpose": [
        # „zweck" fehlte — genau so heisst die Spalte im Outbank-Export, und damit
        # kam der komplette Bestand ohne Verwendungszweck herein (01.09.2026).
        "zweck", "verwendungszweck", "verwendungszweck 1", "buchungsdetails",
        "vorgangsbeschreibung", "beschreibung", "verwendung",
        # N26 exportiert englisch: der Verwendungszweck heisst „Payment Reference"
        "payment reference", "zahlungsreferenz", "reference",
        "subject", "purpose", "description", "notes",
    ],
    "additional_info": [   # Art der Buchung, NICHT der Verwendungszweck
        "buchungstext", "art der zahlung", "umsatzart", "buchungsart", "vorgang",
        "transaktionsart",
    ],
    "end_to_end_id": [
        "sepa referenz", "überweisungsreferenz", "ueberweisungsreferenz",
        "end-to-end", "end to end", "endtoend", "kundenreferenz",
    ],
    "mandate_reference": [
        "mandatsreferenz", "mandatsref", "mandat",
    ],
    "counterparty_name": [
        "auftraggeber", "empfänger", "empfaenger", "beguenstigter", "begünstigter",
        "auftraggeber/empfänger", "auftraggeber/empfaenger", "name",
        "begünstigter/zahlungspflichtiger", "beguenstigter/zahlungspflichtiger",
        "beneficiary", "kontoinhaber", "auftraggeber name", "empfänger name",
        # N26: „Partner Name" (neu) bzw. „Payee" (aeltere Exporte). BEWUSST ohne das
        # blosse „partner" — das steckt auch in „Partner Iban" und wuerde die IBAN
        # als Namen einlesen.
        "partner name", "payee",
    ],
    "own_account": [   # eigenes Konto des Auszugs (Sparkasse: „Auftragskonto")
        # „konto" gehoert hierher, nicht zur Gegenseite: im Outbank-Export steht dort
        # das EIGENE Konto. Bisher landete es in counterparty_iban und musste
        # nachtraeglich zurechtgerueckt werden.
        "konto", "auftragskonto", "eigenes konto", "kontonummer auftragskonto",
        "iban auftragskonto", "auftraggeberkonto",
    ],
    "counterparty_iban": [
        "partner iban", "iban", "iban auftraggeber", "iban empfänger",
        "kontonummer iban", "iban/kontonummer", "kontonummer", "nummer",
        "account number", "account",
    ],
    "counterparty_bic": [
        "bic", "blz", "bic/swift", "bankleitzahl", "bic auftraggeber", "bic empfänger",
    ],
    "amount": [
        "betrag", "buchungsbetrag", "umsatz", "amount", "wert", "betrag in eur",
    ],
    "amount_in_field": [  # Manche Banken: Betrag-In-Feld als getrennte Spalte
        "soll", "debit",
    ],
    "amount_out_field": [
        "haben", "credit",
    ],
    "currency": [
        "währung", "waehrung", "currency",
    ],
    "transaction_type": [
        "buchungstext", "umsatzart", "vorgang", "type", "buchungsart",
    ],
}


def _normalize_header(s):
    if not s:
        return ""
    s = str(s).lower().strip().lstrip("﻿")
    # Sonderzeichen normalisieren (ohne Umlaute zerstören)
    s = re.sub(r"[\.\:\;\(\)]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _match_header_to_field(header, fields=None):
    """Spaltenkopf → kanonisches Feld.

    In DREI Durchgaengen, damit ein exakter Treffer immer gewinnt: vorher entschied
    die Reihenfolge des Alias-Verzeichnisses. „Buchungstext" fiel dadurch auf das
    Teilwort „buchung" (Buchungsdatum) herein und war verbraucht, bevor das Feld
    `purpose` ueberhaupt geprueft wurde (01.09.2026).
    """
    h = _normalize_header(header)
    if not h:
        return None
    felder = fields or _CSV_FIELD_ALIASES
    for pruefung in (lambda h, a: h == a,
                      lambda h, a: h.startswith(a + " "),
                      lambda h, a: a in h):
        for field, aliases in felder.items():
            for alias in aliases:
                if pruefung(h, alias.lower()):
                    return field
    return None


def _detect_encoding_and_decode(raw_bytes):
    """Versucht UTF-8, dann CP1252, dann latin-1 — gibt (text, encoding) zurück."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "iso-8859-15", "latin-1"):
        try:
            return raw_bytes.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("latin-1", errors="replace"), "latin-1-fallback"


def _detect_csv_dialect(text):
    """Erkennt das Trennzeichen — bewusst OHNE csv.Sniffer.

    Der Sniffer hält bei deutschen Dateien gern das Dezimalkomma für den Trenner
    („…;-180,00;1.234,56" wurde am Komma zerlegt, 31.07.2026). Zuverlässiger
    ist: welches Zeichen zerlegt die Zeilen in gleich viele, mehr als zwei Felder?
    """
    zeilen = [z for z in text.split("\n")[:20] if z.strip()]
    if not zeilen:
        return ";"
    bester, bestwert = ";", 0
    for sep in (";", "\t", "|", ","):
        anzahlen = [z.count(sep) + 1 for z in zeilen]
        felder = min(anzahlen)
        if felder < 3:
            continue
        gleichmaessig = len(set(anzahlen)) == 1
        wert = felder * (2 if gleichmaessig else 1)
        if wert > bestwert:
            bester, bestwert = sep, wert
    return bester


def _find_header_row(rows, max_check=30):
    """Manche Banken haben Vorspann-Zeilen vor dem Header.
    Findet die erste Zeile, in der mindestens 3 bekannte Header erkannt werden."""
    best_idx = -1
    best_score = 0
    for i, row in enumerate(rows[:max_check]):
        if not row or len([c for c in row if (c or '').strip()]) < 3:
            continue
        matches = sum(1 for c in row if _match_header_to_field(c) is not None)
        if matches > best_score:
            best_score = matches
            best_idx = i
        # Sehr starker Match → sofort nehmen
        if matches >= 6:
            return i
    return best_idx if best_score >= 3 else 0


def _spalten_raten(rows):
    """Spaltenbedeutung aus dem INHALT ableiten — für CSV-Exporte ohne Kopfzeile.

    Manche Bank-Exporte liefern nur die Daten (31.07.2026, Postbank). Dann
    entscheidet, was in den Spalten steht: Datum, Betrag, IBAN, Text. Die Spalte mit
    den meisten deutschen Datumsangaben ist der Buchungstag, eine zweite solche die
    Wertstellung; die Spalte mit den meisten Beträgen ist der Umsatz (bei zwei
    Betragsspalten ist die zweite meist der Saldo und wird ignoriert).
    """
    proben = [r for r in rows[:80] if r and len([c for c in r if (c or '').strip()]) >= 3]
    if not proben:
        return {}
    breite = max(len(r) for r in proben)
    datum_treffer = [0] * breite
    betrag_treffer = [0] * breite
    iban_treffer = [0] * breite
    text_laenge = [0] * breite
    for r in proben:
        for i in range(breite):
            wert = (r[i] if i < len(r) else '') or ''
            wert = wert.strip()
            if not wert:
                continue
            if _parse_german_date(wert):
                datum_treffer[i] += 1
            elif _parse_german_amount(wert) is not None and any(c.isdigit() for c in wert):
                betrag_treffer[i] += 1
            if re.match(r'^[A-Z]{2}\d{2}[A-Z0-9 ]{10,}$', wert.replace(' ', '')[:34]):
                iban_treffer[i] += 1
            if any(c.isalpha() for c in wert):
                text_laenge[i] += len(wert)

    # Die Hälfte der Zeilen muss passen, mindestens aber eine — sonst scheitern
    # kurze Dateien mit zwei, drei Zeilen an einer zu hohen Schwelle.
    schwelle = max(1, (len(proben) + 1) // 2)
    datums = [i for i in range(breite) if datum_treffer[i] >= schwelle]
    betraege = [i for i in range(breite) if betrag_treffer[i] >= schwelle]
    ibans = [i for i in range(breite) if iban_treffer[i] >= schwelle]
    if not datums or not betraege:
        return {}

    col_map = {"booking_date": datums[0]}
    if len(datums) > 1:
        col_map["value_date"] = datums[1]
    col_map["amount"] = betraege[0]
    if ibans:
        col_map["counterparty_iban"] = ibans[0]
    # Textspalten unterscheiden: Die Gegenpartei ist fast in jeder Zeile eine andere
    # („Stadtwerke", „geohertz", „OBI"), die Umsatzart wiederholt sich („SEPA-Lastschrift").
    # Deshalb entscheidet die Vielfalt der Werte, nicht die Textlänge.
    text_spalten = [i for i in range(breite)
                    if i not in col_map.values() and text_laenge[i] > 0]
    def vielfalt(i):
        werte = [((r[i] if i < len(r) else '') or '').strip().lower() for r in proben]
        werte = [w for w in werte if w]
        return (len(set(werte)) / len(werte)) if werte else 0
    text_spalten.sort(key=lambda i: (-vielfalt(i), -text_laenge[i]))
    if text_spalten:
        col_map["counterparty_name"] = text_spalten[0]
    if len(text_spalten) > 1:
        col_map["purpose"] = text_spalten[1]
    return col_map


def _parse_german_amount(s):
    """'1.234,56' oder '-1.234,56' oder '1234.56' → Decimal."""
    if s is None:
        return None
    s = str(s).strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    # Vorzeichen
    sign = 1
    if s.startswith("-") or s.startswith("−"):
        sign = -1
        s = s.lstrip("-−")
    elif s.startswith("+"):
        s = s.lstrip("+")
    # Wenn sowohl Punkt als Komma vorkommt → letzte Position ist Dezimaltrenner
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # Deutsches Format: 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            # Englisches Format: 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # Nur Komma → deutsches Format
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s) * sign
    except Exception:
        return None


def _parse_german_date(s):
    if not s:
        return None
    s = str(s).strip()
    # Häufige Formate probieren
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d.%m"):
        try:
            d = datetime.strptime(s, fmt).date()
            if d.year < 2000 and fmt == "%d.%m":
                d = d.replace(year=date.today().year)
            return d
        except Exception:
            continue
    # ISO mit Zeit
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def parse_csv(raw_bytes, filename=""):
    """Parst eine deutsche Bank-CSV. Liefert {statement, transactions}.
    Auto-erkennung Encoding + Trennzeichen + Header-Zeile."""
    text, encoding = _detect_encoding_and_decode(raw_bytes)
    delimiter = _detect_csv_dialect(text)

    reader = csv.reader(io.StringIO(text), delimiter=delimiter, quotechar='"')
    all_rows = [r for r in reader if any((c or '').strip() for c in r)]
    if not all_rows:
        raise ValueError("CSV ist leer")

    header_idx = _find_header_row(all_rows)
    headers = all_rows[header_idx]
    data_rows = all_rows[header_idx + 1:]

    # Mapping Header-Index → canonical Feld
    col_map = {}  # field → col_idx
    for i, h in enumerate(headers):
        f = _match_header_to_field(h)
        if f and f not in col_map:  # erstes Match gewinnt
            col_map[f] = i

    hat_betrag = "amount" in col_map or "amount_in_field" in col_map or "amount_out_field" in col_map
    hat_datum = "booking_date" in col_map or "value_date" in col_map
    if not (hat_betrag and hat_datum):
        # Keine brauchbare Kopfzeile — Spalten aus dem Inhalt ableiten und ALLE
        # Zeilen als Daten behandeln (die vermeintliche Kopfzeile ist dann Zeile 1).
        geraten = _spalten_raten(all_rows)
        if geraten:
            col_map = geraten
            data_rows = all_rows
            header_idx = -1
            headers = [f"Spalte {i+1}" for i in range(max(len(r) for r in all_rows))]
            print(f"[bank-import] CSV ohne Kopfzeile — Spalten geraten: {col_map}", flush=True)
    if "amount" not in col_map and not ("amount_in_field" in col_map or "amount_out_field" in col_map):
        raise ValueError(
            f"Keine Betrag-Spalte erkannt. Erkannte Spalten: {list(col_map.keys())}. "
            f"Kopfzeile gefunden: {headers}"
        )
    if "booking_date" not in col_map and "value_date" not in col_map:
        raise ValueError(
            f"Keine Datum-Spalte erkannt. Erkannte Spalten: {list(col_map.keys())}"
        )

    transactions = []
    skipped = 0
    for row in data_rows:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        try:
            t = _row_to_txn(row, col_map)
            if t:
                transactions.append(t)
            else:
                skipped += 1
        except Exception:
            skipped += 1

    statement = {
        "account_iban": None,
        "creation_date": None,
        "from_date": min((t["booking_date"] for t in transactions if t.get("booking_date")), default=None),
        "to_date": max((t["booking_date"] for t in transactions if t.get("booking_date")), default=None),
        "opening_balance": None,
        "closing_balance": None,
        "import_format": "CSV",
        "import_encoding": encoding,
        "import_delimiter": delimiter,
        "import_header_row": header_idx,
        "skipped_rows": skipped,
    }
    return {"statement": statement, "transactions": transactions}


def _kontokennung(wert):
    """Nur plausible Konto-/IBAN-Angaben uebernehmen.

    Spaltenkoepfe wie „Nummer" tragen je nach Export die Gegenkonto-IBAN oder eine
    laufende Zaehlnummer — letztere hat im IBAN-Feld nichts verloren.
    """
    v = (wert or "").replace(" ", "").upper()
    if len(v) < 8 or not v.isalnum():
        return None
    return v


def _row_to_txn(row, col_map):
    def _val(field):
        idx = col_map.get(field)
        if idx is None or idx >= len(row):
            return None
        v = row[idx]
        return v.strip() if isinstance(v, str) else v

    # Betrag: entweder amount-Spalte ODER soll/haben getrennt
    amount = None
    direction = None
    if "amount" in col_map:
        amount = _parse_german_amount(_val("amount"))
        if amount is not None:
            if amount < 0:
                direction = "debit"
                amount = -amount
            else:
                direction = "credit"
    if amount is None and ("amount_in_field" in col_map or "amount_out_field" in col_map):
        soll = _parse_german_amount(_val("amount_in_field"))  # Soll = ausgehend
        haben = _parse_german_amount(_val("amount_out_field"))  # Haben = eingehend
        if haben and haben != 0:
            amount = abs(haben)
            direction = "credit"
        elif soll and soll != 0:
            amount = abs(soll)
            direction = "debit"

    if amount is None or direction is None:
        return None

    booking_dt = _parse_german_date(_val("booking_date")) or _parse_german_date(_val("value_date"))
    value_dt = _parse_german_date(_val("value_date")) or booking_dt

    purpose = _val("purpose") or ""
    # Manche Banken: Verwendungszweck über mehrere Zeilen wird einfach konkateniert eingelesen
    # — wir lassen das, kürzen nur extreme Mehrfach-Whitespaces
    purpose = re.sub(r"\s+", " ", purpose).strip()

    return {
        "id": uuid.uuid4().hex,
        "booking_date": booking_dt.isoformat() if booking_dt else None,
        "value_date": value_dt.isoformat() if value_dt else None,
        "amount": float(amount),
        "currency": (_val("currency") or "EUR").upper()[:3],
        "direction": direction,
        "account_iban": (_val("own_account") or "").replace(" ", "") or None,
        "counterparty_name": _val("counterparty_name") or None,
        "counterparty_iban": _kontokennung(_val("counterparty_iban")),
        "counterparty_bic": _val("counterparty_bic") or None,
        # Fehlt eine eigene Zweck-Spalte, dient der Buchungstext als Ersatz —
        # besser ein grober Text als gar keiner.
        "purpose": purpose or _val("additional_info") or _val("transaction_type") or None,
        "additional_info": _val("additional_info") or _val("transaction_type") or None,
        "end_to_end_id": _val("end_to_end_id") or None,
        "mandate_reference": _val("mandate_reference") or None,
        "bank_reference": None,
        "transaction_id": None,
        "match_status": "open",
        "matched_invoice_id": None,
        "matched_at": None,
        "match_confidence": None,
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# PDF-Kontoauszug (31.07.2026)
# ═══════════════════════════════════════════════════════════════════════════
# Banken liefern Kontoauszüge im Online-Banking meist als PDF. Die PDFs haben
# KEINE maschinenlesbare Struktur — wir lesen die Textschicht mit Positionen
# (pdfplumber) und rekonstruieren daraus die Buchungszeilen:
#   * Zeilen = Wörter mit ähnlichem y-Wert (Toleranz, weil Zahlen minimal
#     versetzt gesetzt werden)
#   * eine Buchung beginnt mit einem Datum (dd.mm. oder dd.mm.jjjj) und endet
#     mit einem Betrag; Folgezeilen ohne Datum gehören zum Verwendungszweck
#   * Vorzeichen: explizit (-/+ bzw. S/H) ODER über die Spaltenposition
#     (Soll-Spalte links, Haben-Spalte rechts) — bei Postbank/Sparkasse üblich
# Gegenprobe: Alter Kontostand + Summe = Neuer Kontostand. Stimmt das nicht,
# meldet der Import eine Warnung statt still falsche Zahlen zu übernehmen.

_PDF_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})?\.?$")
_PDF_AMOUNT_RE = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})*,\d{2}[+-]?$|^[+-]?\d+,\d{2}[+-]?$")
_PDF_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?)\b")
_PDF_BIC_RE = re.compile(r"\b([A-Z]{4}DE[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")

# Zeilen, die zwar wie Buchungen aussehen, aber Salden/Überträge sind
_PDF_SALDO_WORDS = (
    "kontostand", "saldo", "übertrag", "uebertrag", "zwischensumme",
    "summe der", "auszug nr", "seite ", "blatt ",
    "ausgehende transaktionen", "einkommende transaktionen", "eingehende transaktionen",
)
_PDF_OPEN_WORDS = ("alter kontostand", "anfangssaldo", "saldo vortrag", "saldovortrag",
                   "vortrag", "alter saldo", "kontostand am beginn")
_PDF_CLOSE_WORDS = ("neuer kontostand", "endsaldo", "schlusssaldo", "neuer saldo",
                    "kontostand am ende", "aktueller kontostand")
# Buchungsarten — stehen meist direkt hinter dem Datum
_PDF_BUCHUNGSARTEN = (
    "überweisung", "ueberweisung", "gutschrift", "lastschrift", "sepa", "dauerauftrag",
    "kartenzahlung", "kartenverfügung", "auszahlung", "einzahlung", "entgelt", "gebühren",
    "zinsen", "abschluss", "rücklastschrift", "ruecklastschrift", "scheck", "storno",
    "basislastschrift", "firmenlastschrift", "echtzeitüberweisung", "onlineüberweisung",
    "dauerauftragsgutschr", "lohn", "gehalt", "rente", "bargeldauszahlung", "geldautomat",
)


def _pdf_waehrung_weg(tok: str) -> str:
    """Währungszeichen abstreifen — N26 schreibt den Betrag als '-31,20€'."""
    t = (tok or "").strip()
    for z in ("€", "EUR", "eur"):
        t = t.replace(z, "")
    return t.strip()


def _pdf_ist_betrag(tok: str) -> bool:
    return bool(_PDF_AMOUNT_RE.match(_pdf_waehrung_weg(tok)))


def _pdf_name_aus_text(text, art=None):
    """Gegenpartei-Name aus einem Textstück — oder None, wenn nur Floskeln drinstehen.

    Streift die Buchungsart ab („SEPA-Überweisung", „Lastschrift") und nimmt die
    führenden Wörter bis zum ersten Referenz-Token (Rechnungs-/Mandatsnummern
    bestehen aus Ziffern bzw. Ziffern+Großbuchstaben). Bleibt nur eine Buchungsart
    übrig, liefert die Funktion None — dann steht der Name in der Folgezeile.
    """
    rest = (text or "").strip()
    if art and rest.lower().startswith(art.lower()):
        rest = rest[len(art):]
    rest = rest.strip(" .:-•|")
    if not rest or not re.search(r"[A-Za-zÄÖÜäöüß]{3}", rest):
        return None
    tokens = rest.split()
    if all(t.strip(" .,:;-").lower() in _PDF_BUCHUNGSARTEN for t in tokens):
        return None          # nur „Gutschrift" / „Überweisung" o. ä.
    namensteile = []
    for wort in tokens:
        if re.search(r"\d", wort) and (len(wort) >= 5 or "/" in wort or "-" in wort):
            break
        namensteile.append(wort)
        if len(namensteile) >= 6:
            break
    name = " ".join(namensteile) or rest
    return name[:120]


def _pdf_amount(tok: str):
    """'1.234,56-' / '-1.234,56' / '-31,20€' → (Decimal, explizites Vorzeichen|None)."""
    t = _pdf_waehrung_weg(tok)
    sign = None
    if t.endswith("-") or t.startswith("-"):
        sign = "debit"
    elif t.endswith("+") or t.startswith("+"):
        sign = "credit"
    t = t.strip("+-")
    try:
        return Decimal(t.replace(".", "").replace(",", ".")), sign
    except Exception:
        return None, None


def _pdf_pages(raw_bytes):
    """Seiten → Zeilen mit Wortpositionen. Wirft, wenn keine Textschicht da ist."""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise ValueError("PDF-Unterstützung fehlt (pdfplumber nicht installiert)") from e

    pages = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
            buckets = {}
            for w in words:
                buckets.setdefault(round(float(w["top"]) / 3.0), []).append(w)
            lines = []
            for key in sorted(buckets):
                ws = sorted(buckets[key], key=lambda w: float(w["x0"]))
                lines.append({"words": ws, "top": min(float(w["top"]) for w in ws)})
            # Feinschliff: Zahlen werden oft ein bis zwei Punkte höher gesetzt als ihr
            # Text ("Dein alter Kontostand" 155,2 / "+369,71€" 154,3) und landen sonst in
            # getrennten Zeilen — dann fehlt der Saldo. Sehr eng benachbarte Zeilen daher
            # zusammenführen; echte Tabellenzeilen liegen deutlich weiter auseinander.
            zusammen = []
            for zeile in lines:
                if zusammen and abs(zeile["top"] - zusammen[-1]["top"]) < 2.5:
                    zusammen[-1]["words"] = sorted(zusammen[-1]["words"] + zeile["words"],
                                                   key=lambda w: float(w["x0"]))
                    zusammen[-1]["top"] = min(zusammen[-1]["top"], zeile["top"])
                else:
                    zusammen.append(zeile)
            for zeile in zusammen:
                zeile["text"] = " ".join(w["text"] for w in zeile["words"])
            pages.append({"width": float(page.width), "lines": zusammen})
    return pages


# Kreditkarten-Abrechnungen tragen keine IBAN, sondern eine Kartennummer — die
# Buchungen landeten deshalb ohne Konto im Bestand (01.09.2026). Erkennbar am
# Text („Mastercard", „Kreditkartenabrechnung", „Einzug von Kto.") und an der
# maskierten Kartennummer 1234 5678 90XX XXXX bzw. 1234_5678 im Dateinamen.
# NUR echte Kreditkarten-Abrechnungen — nicht jedes Dokument, in dem „Mastercard"
# vorkommt. N26-Kontoauszuege nennen die Karte in jeder zweiten Zeile und wurden
# dadurch faelschlich als Kartenkonto gefuehrt (01.09.2026).
_PDF_KARTE_RE = re.compile(r"kreditkartenabrechnung|einzug von kto|"
                            r"abrechnung\s+(?:ihrer\s+)?(?:kredit)?karte|"
                            r"kartenabrechnung", re.I)
# Wortgrenzen taugen hier nicht: der Unterstrich in „5584_4789" zaehlt als
# Wortzeichen, \b greift dort nicht.
_PDF_KARTENNR_RE = re.compile(
    r"(?<!\d)(\d{4})[\s_-]?(\d{4})[\s_-]?[X\*]{2,4}[\s_-]?[X\*]{4}(?!\d)|"
    r"(?<!\d)(\d{4})[\s_-](\d{4})(?!\d)")


def _karten_konto(volltext, filename=""):
    """Kennung fuer ein Kreditkartenkonto: KARTE-<erste4><letzte4>."""
    for quelle in (filename or "", volltext[:4000]):
        m = _PDF_KARTENNR_RE.search(quelle)
        if m:
            teile = [g for g in m.groups() if g]
            if len(teile) >= 2:
                return f"KARTE-{teile[0]}{teile[1]}"
    return "KARTE-UNBEKANNT"


def _pdf_statement_meta(pages, filename=""):
    """IBAN, Zeitraum und Salden aus dem Kopf-/Fußbereich lesen."""
    meta = {"account_iban": None, "from_date": None, "to_date": None,
            "opening_balance": None, "closing_balance": None, "kartenkonto": False}
    volltext = "\n".join(l["text"] for p in pages for l in p["lines"])

    alle_ibans = [m.group(1).replace(" ", "") for m in _PDF_IBAN_RE.finditer(volltext)]
    if alle_ibans:
        from collections import Counter
        haeufigste, anzahl = Counter(alle_ibans).most_common(1)[0]
        # Kommt eine IBAN mehrfach vor, ist es die eigene (Kopf-/Fußzeile jeder Seite);
        # bei nur einem Treffer bleibt es dabei.
        meta["account_iban"] = haeufigste if anzahl > 1 else alle_ibans[0]

    # Datum aus dem Dateinamen als Rueckfall (…_ABRECHNUNG_2025-09-23_…)
    m_datei = re.search(r"(20\d\d)[-_.]?(\d{2})[-_.]?(\d{2})", filename or "")
    if m_datei:
        meta["datei_datum"] = f"{m_datei.group(1)}-{m_datei.group(2)}-{m_datei.group(3)}"

    if _PDF_KARTE_RE.search(volltext):
        # Die IBAN auf einer Kartenabrechnung ist das BELASTETE Girokonto, nicht das
        # Konto der Umsaetze. Die Karte bekommt ihre eigene Kennung.
        meta["kartenkonto"] = True
        meta["belastet_konto"] = meta.get("account_iban")
        meta["account_iban"] = _karten_konto(volltext, filename)

    m = re.search(r"(?:vom\s+)?(\d{1,2}\.\d{1,2}\.\d{2,4})\s*(?:bis|-|–)\s*(\d{1,2}\.\d{1,2}\.\d{2,4})",
                  volltext, re.I)
    if m:
        a, b = _parse_german_date(m.group(1)), _parse_german_date(m.group(2))
        meta["from_date"] = a.isoformat() if a else None
        meta["to_date"] = b.isoformat() if b else None

    for p in pages:
        for l in p["lines"]:
            low = l["text"].lower()
            betraege = [t for t in (w["text"] for w in l["words"]) if _pdf_ist_betrag(t)]
            if not betraege:
                continue
            wert, sign = _pdf_amount(betraege[-1])
            if wert is None:
                continue
            if sign == "debit":
                wert = -wert
            elif " s " in f" {low} " or low.rstrip().endswith(" s"):
                wert = -wert
            if any(k in low for k in _PDF_OPEN_WORDS) and meta["opening_balance"] is None:
                meta["opening_balance"] = wert
            elif any(k in low for k in _PDF_CLOSE_WORDS):
                meta["closing_balance"] = wert
    return meta


def _pdf_spalten(kandidaten):
    """Aus den x-Positionen der Beträge Soll-/Haben-Spalten ableiten.

    Zwei getrennte Spalten (Abstand > 20 pt) = klassisches Soll/Haben-Layout →
    links Soll (Ausgang), rechts Haben (Eingang). Sonst None (eine Spalte).
    """
    xs = sorted(k["amount_x"] for k in kandidaten if k.get("amount_x") is not None)
    if len(xs) < 2:
        return None
    gruppen, aktuell = [], [xs[0]]
    for x in xs[1:]:
        if x - aktuell[-1] > 20:
            gruppen.append(aktuell)
            aktuell = [x]
        else:
            aktuell.append(x)
    gruppen.append(aktuell)
    if len(gruppen) != 2:
        return None
    # Beide Spalten müssen nennenswert belegt sein, sonst ist es Streuung
    if min(len(g) for g in gruppen) < 1:
        return None
    return (sum(gruppen[0]) / len(gruppen[0]), sum(gruppen[1]) / len(gruppen[1]))


def _pdf_jahr_raten(monat, tag, meta):
    """Kontoauszüge kürzen das Jahr oft weg ('05.07.') — aus dem Zeitraum ergänzen.

    Fehlt der Zeitraum (Kreditkarten-Abrechnungen nennen keinen), zaehlt das Datum
    im Dateinamen. Ohne beides landete alles im laufenden Jahr: eine Abrechnung von
    2025 erzeugte Buchungen mit Datum 2026 — in der Zukunft (01.09.2026).
    """
    bezug = meta.get("to_date") or meta.get("from_date") or meta.get("datei_datum")
    if not bezug:
        return datetime.now().year
    jahr = int(bezug[:4])
    bezugs_monat = int(bezug[5:7])
    # Jahreswechsel: Auszug vom Januar enthält Dezember-Buchungen
    if monat == 12 and bezugs_monat == 1:
        return jahr - 1
    if monat == 1 and bezugs_monat == 12:
        return jahr + 1
    return jahr


# Alles ab diesen Marken gehoert zur Abrechnung, nicht mehr zum einzelnen Umsatz:
# Summenzeile, USt-ID der Bank, Anschrift des Abrechnungsdienstleisters, interne
# Nummern. Ohne den Schnitt hing der komplette Fussbereich am letzten Umsatz.
_PDF_FUSS_RE = re.compile(
    r"\s*-{6,}|\s*(?:Mastercard|Visa)\s+Summe|\s*Einzug von Kto|"
    r"\s*Ust-ID der|\s*Bei R(?:ü|ue)ckfragen wenden Sie sich|"
    r"\s*INT\.\s*ABRECHNUNGS-NR", re.I)


def _zweck_ohne_fuss(text):
    if not text:
        return text
    m = _PDF_FUSS_RE.search(text)
    return text[:m.start()].strip() if m else text.strip()


def parse_pdf(raw_bytes, filename=""):
    """Liest einen PDF-Kontoauszug (Textschicht) und gibt {statement, transactions}.

    Erkennt Buchungszeilen anhand von Datum + Betrag, hängt Folgezeilen als
    Verwendungszweck an und leitet das Vorzeichen aus Vorzeichenzeichen oder
    der Spaltenposition ab. Salden werden gegengerechnet (statement.saldo_check).
    """
    pages = _pdf_pages(raw_bytes)
    text_zeichen = sum(len(l["text"]) for p in pages for l in p["lines"])
    # Ein echter Scan hat GAR keine Textschicht. Nur dann diese Meldung — sonst
    # läuft die Datei weiter und bekommt die genauere „keine Buchungszeilen".
    if text_zeichen < 20 * max(1, len(pages)):
        raise ValueError(
            "Das PDF enthält keinen auslesbaren Text (vermutlich ein Scan). "
            "Bitte den Kontoauszug als CSV oder CAMT.053-XML aus dem Online-Banking laden."
        )

    meta = _pdf_statement_meta(pages, filename)
    warnungen = []

    # Kopf- und Fußzeilen erkennen: Sie stehen auf JEDER Seite gleich da (Name,
    # Anschrift, IBAN, „Nr. 05/2026", Seitenzahl). Ohne diesen Filter hängt der jeweils
    # letzte Umsatz einer Seite die halbe Fußzeile an seinen Verwendungszweck — im
    # N26-Auszug landete so „der Kontoinhaber" in fremden Buchungen und löste die
    # Privat-Regel aus (31.07.2026).
    from collections import Counter as _Counter
    seiten_je_text = _Counter()
    for p_ in pages:
        for t_ in {l["text"].strip() for l in p_["lines"] if l["text"].strip()}:
            seiten_je_text[t_] += 1
    wiederkehrend = {t_ for t_, n_ in seiten_je_text.items() if n_ >= 3 and len(pages) >= 3}
    eigene_iban = (meta.get("account_iban") or "").replace(" ", "")

    def ist_rahmenzeile(text):
        t_ = text.strip()
        if t_ in wiederkehrend:
            return True
        if eigene_iban and eigene_iban in t_.replace(" ", ""):
            return True
        return bool(re.match(r"^\d{1,3}\s*/\s*\d{1,3}$", t_))

    # ── 1. Durchgang: Zeilen einsammeln, Buchungsanfänge markieren ──
    kandidaten = []
    for p in pages:
        for l in p["lines"]:
            words = l["words"]
            if not words:
                continue
            text = l["text"]
            low = text.lower()
            # Das Datum steht NICHT bei jeder Bank vorn: Postbank/Sparkasse beginnen die
            # Zeile damit, N26 schreibt erst den Namen, dann Datum, dann Betrag. Deshalb
            # wird die Zeile als Buchung erkannt, sobald sie IRGENDWO ein Datum UND einen
            # Betrag trägt (31.07.2026, N26-Auszüge Juni/Juli).
            datum_idx = [i for i, w in enumerate(words) if _PDF_DATE_RE.match(w["text"])]
            betrag_wort, betrag_idx = None, None
            for i in range(len(words) - 1, -1, -1):
                if _pdf_ist_betrag(words[i]["text"]):
                    betrag_wort, betrag_idx = words[i], i
                    break
            ist_saldozeile = any(k in low for k in _PDF_SALDO_WORDS)

            if datum_idx and betrag_wort is not None and not ist_saldozeile:
                wert, sign = _pdf_amount(betrag_wort["text"])
                if wert is None or wert == 0:
                    continue
                # 'S'/'H' hinter dem Betrag (Sparkassen-Layout)
                if sign is None and betrag_idx + 1 < len(words):
                    kz = words[betrag_idx + 1]["text"].strip().upper()
                    if kz in ("S", "SOLL"):
                        sign = "debit"
                    elif kz in ("H", "HABEN"):
                        sign = "credit"
                # Zweites Datum direkt hinter dem ersten = Wertstellung
                d0 = datum_idx[0]
                wert_datum = None
                if d0 + 1 in datum_idx:
                    wert_datum = words[d0 + 1]["text"]
                # Beschreibung = alles vor dem Betrag, ohne die Datumsfelder
                rest = " ".join(w["text"] for i, w in enumerate(words)
                                if i < betrag_idx and i not in datum_idx).strip()
                kandidaten.append({
                    "typ": "buchung",
                    "datum": words[d0]["text"],
                    "wert_datum": wert_datum,
                    "amount": wert,
                    "sign": sign,
                    "amount_x": float(betrag_wort["x0"]),
                    "rest": rest,
                    "zusatz": [],
                })
            elif kandidaten and not (datum_idx and betrag_wort is not None):
                # Folgezeile → Verwendungszweck der letzten Buchung.
                # Salden-/Summenzeilen tragen IMMER einen Betrag; nur die werden verworfen
                # — sonst verlöre ein Zweck wie „Übertrag auf Rücklagen" seine Zeile.
                if (text.strip() and len(text.strip()) > 1
                        and not (betrag_wort is not None and ist_saldozeile)
                        and not ist_rahmenzeile(text)):
                    kandidaten[-1]["zusatz"].append(text.strip())
                    # „Wertstellung 31.05.2026" nachreichen, wenn die Zeile sie trägt
                    if not kandidaten[-1]["wert_datum"] and "wertstellung" in low and datum_idx:
                        kandidaten[-1]["wert_datum"] = words[datum_idx[0]]["text"]

    if not kandidaten:
        raise ValueError(
            "Im PDF wurden keine Buchungszeilen gefunden. Erwartet wird ein "
            "Kontoauszug mit Datum, Verwendungszweck und Betrag je Zeile."
        )

    spalten = _pdf_spalten(kandidaten)
    ohne_vorzeichen = 0

    # ── 2. Durchgang: in Buchungssätze umsetzen ──
    transactions = []
    for k in kandidaten:
        sign = k["sign"]
        if sign is None and spalten:
            links, rechts = spalten
            sign = "debit" if abs(k["amount_x"] - links) <= abs(k["amount_x"] - rechts) else "credit"
        if sign is None:
            sign = "debit"
            ohne_vorzeichen += 1

        dm = _PDF_DATE_RE.match(k["datum"])
        tag, monat, jahr = int(dm.group(1)), int(dm.group(2)), dm.group(3)
        if jahr:
            jahr = int(jahr)
            if jahr < 100:
                jahr += 2000
        else:
            jahr = _pdf_jahr_raten(monat, tag, meta)
        try:
            buchungs_datum = date(jahr, monat, tag)
            # Ein Umsatz kann nicht in der Zukunft liegen — dann war die
            # Jahresschaetzung zu hoch.
            if buchungs_datum > date.today():
                buchungs_datum = date(jahr - 1, monat, tag)
        except ValueError:
            warnungen.append(f"Zeile mit ungültigem Datum übersprungen: {k['datum']} {k['rest'][:40]}")
            continue
        wert_datum = _parse_german_date(k["wert_datum"]) if k["wert_datum"] else None
        if wert_datum is None and k["wert_datum"]:
            wd = _PDF_DATE_RE.match(k["wert_datum"])
            if wd:
                try:
                    wert_datum = date(_pdf_jahr_raten(int(wd.group(2)), int(wd.group(1)), meta),
                                      int(wd.group(2)), int(wd.group(1)))
                except ValueError:
                    wert_datum = None

        alle_zeilen = ([k["rest"]] if k["rest"] else []) + k["zusatz"]
        volltext = " ".join(alle_zeilen).strip()

        # Buchungsart = führendes Schlüsselwort, Gegenpartei = erste sinnvolle Folgezeile
        art = None
        if k["rest"]:
            low_rest = k["rest"].lower()
            for a in _PDF_BUCHUNGSARTEN:
                if low_rest.startswith(a):
                    art = k["rest"][:len(a)].strip()
                    break
        # Die Gegenpartei steht je nach Bank auf der Buchungszeile selbst (N26 schreibt
        # den Händler vor das Datum) oder erst in der Folgezeile (Postbank/Sparkasse
        # beginnen mit Datum + Buchungsart). Erst die Buchungszeile prüfen — steht dort
        # nur die Buchungsart, liefert der Helfer None und wir gehen in die Folgezeilen.
        gegenpartei = _pdf_name_aus_text(k["rest"], art)
        if gegenpartei is None:
            for z in k["zusatz"]:
                zl = z.lower()
                if _PDF_IBAN_RE.search(z) or zl.startswith(("svwz+", "eref+", "kref+",
                                                            "mref+", "cred+", "abwa+")):
                    continue
                gegenpartei = _pdf_name_aus_text(z)
                if gegenpartei:
                    break

        iban_m = _PDF_IBAN_RE.search(volltext)
        bic_m = _PDF_BIC_RE.search(volltext)
        gegen_iban = iban_m.group(1).replace(" ", "") if iban_m else None
        if gegen_iban and meta.get("account_iban") and gegen_iban == meta["account_iban"]:
            gegen_iban = None

        transactions.append({
            "id": uuid.uuid4().hex,
            "booking_date": buchungs_datum.isoformat(),
            "value_date": (wert_datum or buchungs_datum).isoformat(),
            "amount": float(k["amount"]),
            "currency": "EUR",
            "direction": sign,
            "account_iban": meta.get("account_iban"),
            "counterparty_name": gegenpartei,
            "counterparty_iban": gegen_iban,
            "counterparty_bic": bic_m.group(1) if bic_m else None,
            "purpose": _zweck_ohne_fuss(re.sub(r"\s+", " ", volltext))[:500] or None,
            "additional_info": art,
            "end_to_end_id": None,
            "bank_reference": None,
            "transaction_id": None,
            "match_status": "open",
            "matched_invoice_id": None,
            "matched_at": None,
            "match_confidence": None,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        })

    if ohne_vorzeichen:
        warnungen.append(
            f"{ohne_vorzeichen} Buchung(en) ohne erkennbares Vorzeichen — als Ausgang gewertet. "
            "Bitte in der Liste prüfen."
        )

    # ── Gegenprobe über die Salden ──
    saldo_check = None
    if meta.get("opening_balance") is not None and meta.get("closing_balance") is not None:
        summe = sum(Decimal(str(t["amount"])) * (1 if t["direction"] == "credit" else -1)
                    for t in transactions)
        erwartet = meta["opening_balance"] + summe
        diff = erwartet - meta["closing_balance"]
        saldo_check = {
            "opening": float(meta["opening_balance"]),
            "closing": float(meta["closing_balance"]),
            "summe_buchungen": float(summe),
            "differenz": float(diff),
            "ok": abs(diff) < Decimal("0.01"),
        }
        if not saldo_check["ok"]:
            warnungen.append(
                f"Saldo-Gegenprobe stimmt nicht: Alter Kontostand {float(meta['opening_balance']):.2f} € "
                f"+ Buchungen {float(summe):.2f} € = {float(erwartet):.2f} €, "
                f"laut Auszug {float(meta['closing_balance']):.2f} € (Differenz {float(diff):.2f} €). "
                "Vermutlich wurde eine Zeile nicht erkannt."
            )

    statement = dict(meta)
    statement["saldo_check"] = saldo_check
    statement["warnungen"] = warnungen
    statement["seiten"] = len(pages)
    statement["quelle"] = filename or "PDF"
    return {"statement": statement, "transactions": transactions}


def parse_auto(raw_bytes, filename=""):
    """Erkennt CAMT.053 (XML), PDF-Kontoauszug und CSV anhand der ersten Bytes."""
    head = raw_bytes[:200].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<Document"):
        return parse_camt053(raw_bytes), "camt053"
    if head.startswith(b"%PDF") or filename.lower().endswith(".pdf"):
        return parse_pdf(raw_bytes, filename), "pdf"
    return parse_csv(raw_bytes, filename), "csv"


# ── Dublettenerkennung (31.07.2026) ──────────────────────────────────
def grobschluessel(t):
    """Zweiter Schlüssel OHNE Verwendungszweck: Datum + Betrag + Richtung.

    Derselbe Umsatz sieht je Quelle anders aus — der CSV-Export liefert oft gar
    keinen Zweck, der FinTS-Abruf den vollen Text. Der Hauptschlüssel (mit Zweck)
    hält solche Paare für zwei Umsätze; genau so sind die Doppelbuchungen vom
    31.07.2026 entstanden. Das Konto steckt bewusst NICHT im Schlüssel — viele
    CSV-Exporte nennen es gar nicht; es wird erst beim Kandidaten verglichen.
    """
    try:
        amt = abs(float(t.get("amount") or 0))
    except (TypeError, ValueError):
        amt = 0.0
    return '|'.join([
        (t.get("booking_date") or '')[:10],
        f"{amt:.2f}",
        (t.get("direction") or ''),
    ])


def namensform(t):
    n = ' '.join((t.get("counterparty_name") or '').split()).lower()
    return n[:24]


def finde_dublette(neu_t, grob_index):
    """Wie ist_dublette(), gibt aber den vorhandenen Datensatz zurueck (oder None).

    Wird zum Anreichern gebraucht: die CSV-Importe (Outbank, Sparkasse, Postbank)
    kamen OHNE Verwendungszweck herein — 1.913 Buchungen ohne einen einzigen
    Buchungstext. Ein erneuter Import mit Text soll die vorhandene Buchung nicht
    verwerfen, sondern ihre leeren Felder fuellen (01.09.2026).
    """
    kandidaten = grob_index.get(grobschluessel(neu_t))
    if not kandidaten:
        return None
    neu_konto = (neu_t.get("account_iban") or '').replace(' ', '').upper()
    neu_name = namensform(neu_t)
    for alt_t in kandidaten:
        alt_konto = (alt_t.get("account_iban") or '').replace(' ', '').upper()
        if neu_konto and alt_konto and neu_konto != alt_konto:
            continue
        alt_name = namensform(alt_t)
        if not neu_name or not alt_name or neu_name == alt_name \
                or neu_name in alt_name or alt_name in neu_name:
            return alt_t
    return None


# Felder, die eine bestehende Buchung aus einem spaeteren Import uebernehmen darf,
# solange sie dort leer sind. Betrag, Datum und Richtung bleiben unangetastet.
ANREICHERBARE_FELDER = ("purpose", "additional_info", "counterparty_iban",
                        "counterparty_name", "counterparty_bic", "end_to_end_id",
                        "bank_reference", "transaction_id", "value_date",
                        "mandate_reference",
                        # Auch das Konto: die Outbank-Exporte kamen teils ohne
                        # Kontospalte herein (90 Buchungen ohne Zuordnung). Ein
                        # Auszug desselben Zeitraums traegt es nach — gefuellt wird
                        # weiterhin nur, was leer ist (01.09.2026).
                        "account_iban")


def anreichern(alt_t, neu_t):
    """Fuellt leere Felder einer vorhandenen Buchung aus einem neuen Datensatz.
    Gibt die Liste der ergaenzten Felder zurueck."""
    ergaenzt = []
    for feld in ANREICHERBARE_FELDER:
        neu_wert = neu_t.get(feld)
        if isinstance(neu_wert, str):
            neu_wert = neu_wert.strip()
        if not neu_wert:
            continue
        alt_wert = alt_t.get(feld)
        if isinstance(alt_wert, str):
            alt_wert = alt_wert.strip()
        if alt_wert:
            continue
        alt_t[feld] = neu_wert
        ergaenzt.append(feld)
    return ergaenzt


def ist_dublette(neu_t, grob_index):
    """True, wenn der Umsatz schon vorliegt — auch aus einer anderen Quelle.

    Bei gleichem Datum, Betrag und Richtung gilt er als derselbe Umsatz, sofern
    das Konto passt (oder eine Seite keines nennt) UND die Gegenpartei zusammenpasst
    (oder eine Seite keinen Namen trägt). Zwei echte gleich hohe Zahlungen am selben
    Tag — etwa zwei Energieausweise à 35 € — tragen verschiedene Namen und bleiben
    getrennt.
    """
    kandidaten = grob_index.get(grobschluessel(neu_t))
    if not kandidaten:
        return False
    neu_konto = (neu_t.get("account_iban") or '').replace(' ', '').upper()
    neu_name = namensform(neu_t)
    for alt_t in kandidaten:
        alt_konto = (alt_t.get("account_iban") or '').replace(' ', '').upper()
        if neu_konto and alt_konto and neu_konto != alt_konto:
            continue                      # anderes Konto = anderer Umsatz
        alt_name = namensform(alt_t)
        if not neu_name or not alt_name or neu_name == alt_name \
                or neu_name in alt_name or alt_name in neu_name:
            return True
    return False

def normalisiere_eigenkonto(records, eigene_ibans):
    """Rückt die eigene Konto-IBAN aus der Gegenseiten-Spalte an die richtige Stelle.

    Manche Exporte (Outbank) führen eine Spalte „IBAN"/„Konto", die das EIGENE Konto
    des Auszugs meint, nicht die Gegenpartei. Die Heuristik ist eindeutig: steht dort
    eine unserer eigenen IBANs und ist das Herkunftskonto leer, gehört sie dorthin.
    Ohne diese Korrektur fehlt die Kontozuordnung — und `ist_intern` hielte den Umsatz
    im Zweifel für einen Transfer zwischen eigenen Konten (31.07.2026).
    """
    eigene = {i.replace(' ', '').upper() for i in (eigene_ibans or set())}
    geaendert = 0
    for t in records:
        gegen = (t.get('counterparty_iban') or '').replace(' ', '').upper()
        if gegen and gegen in eigene and not (t.get('account_iban') or ''):
            t['account_iban'] = gegen
            t['counterparty_iban'] = None
            geaendert += 1
    return geaendert
