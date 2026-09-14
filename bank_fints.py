"""FinTS/HBCI-Anbindung (read-only): Umsätze + Saldo direkt von der Bank abrufen.

Nur Informations-Geschäftsvorfälle (Kontoumsätze HKKAZ, Saldo HKSAL) — KEINE
Überweisungen. Decoupled pushTAN/BestSign wird unterstützt: weil die App zustandslos
über HTTP läuft, wird der FinTS-Dialog zwischen "Start" und "Poll" pausiert und
fortgesetzt (client.pause_dialog/resume_dialog + NeedRetryResponse).

Öffentliche API für webapp.py:
    step(conn, pin, state|None) -> (status, new_state, public)
        status: "tan_pending" | "done"
        state:  serialisierbarer (bytes-haltiger) Fortschritts-Blob (in den
                In-Memory-Pending-Store gelegt; enthält NIE die PIN)
        public: bei tan_pending -> {challenge, decoupled}
                bei done        -> {transactions:[…], balance:{…}|None, account_iban, accounts:[…]}

Die PIN wird bei jedem Schritt frisch übergeben (aus der verschlüsselten Ablage
entschlüsselt) und niemals im State/Log gehalten.
"""
import datetime
import os
import json
import uuid

from fints.client import FinTS3PinTanClient, NeedTANResponse, NeedRetryResponse
from fints.models import SEPAAccount

FETCH_DAYS = 90  # PSD2-Fenster; Dedup fängt Überlappungen ab

# ── DIALOG-PROTOKOLL (28.07.2026) ───────────────────────────────────────
# Bank-Dialoge liefen bisher nur nach stdout (journalctl) — für die Sparkasse-Analyse
# zu flüchtig und nicht in der App sichtbar. Jetzt: append-only JSONL je Ereignis unter
# data/bank_fints_log.jsonl. PIN/TAN werden NIE geschrieben (auch nicht gekürzt);
# protokolliert werden Stufe, TAN-Verfahren/-Medium, Antwortklasse, Rückmeldecodes und
# Fehler samt Ursache. Datei rotiert bei > 2 MB (eine Vorgängerdatei .1).
BANK_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bank_fints_log.jsonl")
_GEHEIM = ("pin", "tan", "passwort", "password", "secret")


def _redigiere(obj):
    if isinstance(obj, dict):
        return {k: ("***" if any(g in str(k).lower() for g in _GEHEIM) else _redigiere(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redigiere(x) for x in obj]
    if isinstance(obj, str):
        return obj[:400]
    return obj


def protokoll(cid, ereignis, **felder):
    """Ein Protokolleintrag. Nie mit Geheimnissen aufrufen — wird zusätzlich redigiert."""
    try:
        eintrag = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "bank": cid or "?",
                   "ereignis": ereignis}
        eintrag.update(_redigiere(felder))
        os.makedirs(os.path.dirname(BANK_LOG), exist_ok=True)
        try:
            if os.path.exists(BANK_LOG) and os.path.getsize(BANK_LOG) > 2 * 1024 * 1024:
                os.replace(BANK_LOG, BANK_LOG + ".1")
        except Exception:
            pass
        with open(BANK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
        os.chmod(BANK_LOG, 0o600)
    except Exception as e:  # Protokoll darf den Abruf NIE stören
        print(f"[bank-fints] Protokoll fehlgeschlagen: {e}")


def protokoll_lesen(cid=None, limit=200):
    """Letzte Einträge (neueste zuerst), optional auf eine Bank gefiltert."""
    zeilen = []
    for datei in (BANK_LOG, BANK_LOG + ".1"):
        try:
            with open(datei, encoding="utf-8") as f:
                zeilen.extend(f.readlines())
        except Exception:
            pass
    aus = []
    for z in reversed(zeilen):
        try:
            e = json.loads(z)
        except Exception:
            continue
        if cid and e.get("bank") != cid:
            continue
        aus.append(e)
        if len(aus) >= limit:
            break
    return aus


# ── Robustheit-Patch: Sparkassen melden teils das Einschritt-Verfahren '999'.
# python-fints macht dann get_tan_mechanisms()[get_current_tan_mechanism()] und
# stürzt während des Dialog-Inits mit KeyError '999' ab (von der Lib als
# "Couldn't establish dialog with bank, Authentication data wrong?" getarnt).
# Wir ersetzen die zwei betroffenen Lookups durch eine fehlertolerante Variante
# (.get statt []). Bei Banken, deren aktuelles Verfahren im Dict steht (z. B.
# Postbank BestSign), ist das Verhalten identisch.
def _safe_is_challenge_structured(self):
    p = self.get_tan_mechanisms().get(self.get_current_tan_mechanism())
    return bool(getattr(p, 'challenge_structured', False)) if p is not None else False


def _safe_is_tan_media_required(self):
    try:
        p = self.get_tan_mechanisms().get(self.get_current_tan_mechanism())
        if p is None:
            return False
        from fints.formals import DescriptionRequired
        return (getattr(p, 'supported_media_number', None) is not None
                and p.supported_media_number > 1
                and p.description_required == DescriptionRequired.MUST)
    except Exception:
        return False


# ── Dritte unabgesicherte Stelle (28.07.2026, Sparkasse): _get_tan_segment
# macht ebenfalls get_tan_mechanisms()[get_current_tan_mechanism()]. Bei der Sparkasse ist
# das Verfahren-Dict beim Umsatzabruf LEER (die BPD kommt erst mit dem ersten echten
# Dialog, das anonyme fetch_tan_mechanisms liefert dort nichts) und das aktuelle Verfahren
# ist '' → KeyError('') mitten im HKTAN-Aufbau, in der App sichtbar als „FinTS-Fehler: ''".
# Ausweg: fehlt der Parametersatz, das HKTAN in der höchsten implementierten Version bauen
# (die Bank akzeptiert die Version aus ihrer eigenen BPD-Liste; 6 ist bei Sparkassen Standard).
def _safe_get_tan_segment(self, orig_seg, tan_process, tan_seg=None):
    mechs = self.get_tan_mechanisms() or {}
    aktuell = self.get_current_tan_mechanism()
    tan_mechanism = mechs.get(aktuell)
    if tan_mechanism is None:
        versionen = sorted(IMPLEMENTED_HKTAN_VERSIONS.keys())
        version = versionen[-1] if versionen else 6
        print(f"[bank-fints] _get_tan_segment: kein Parametersatz für Verfahren {aktuell!r} "
              f"(Dict hat {list(mechs.keys())}) → HKTAN v{version} als Rückfall")
        try:
            protokoll(getattr(self, "_bank_cid", None) or "?", "hktan_rueckfall",
                      verfahren=str(aktuell), verfuegbar=list(mechs.keys()), version=version)
        except Exception:
            pass
        hktan = IMPLEMENTED_HKTAN_VERSIONS[version]
        seg = hktan(tan_process=tan_process)
        if hasattr(seg, 'segment_type') and orig_seg is not None:
            try:
                seg.segment_type = orig_seg.header.type
            except Exception:
                pass
        if getattr(self, "selected_tan_medium", None):
            try:
                seg.tan_medium_name = self.selected_tan_medium
            except Exception:
                pass
        return seg
    return _orig_get_tan_segment(self, orig_seg, tan_process, tan_seg)


# ── Länderkennzeichen ohne BIC (28.07.2026, Sparkasse) ──────────────
# `BankIdentifier.from_sepa_account` liest das Länderkennzeichen aus der BIC
# (`acc.bic[4:6]`). Kommt das Konto nicht aus dem HKSPA-Abruf, sondern aus der IBAN
# (Postbank-Workaround, gilt auch für die Sparkasse), ist die BIC LEER → `''` als
# Dict-Schlüssel → KeyError('') beim Aufbau des Umsatz-Segments HKKAZ. In der App war
# das die nackte Meldung „FinTS-Fehler: ''". Die Länderinfo steckt aber auch in der
# IBAN (Stellen 1–2) — genau daraus leiten wir sie ab, wenn die BIC fehlt.
def _patch_kontoklassen():
    """Länderkennzeichen aus der IBAN ableiten, wenn die BIC fehlt.

    Betroffen sind DREI Formal-Klassen (KTZ1, Account2, Account3) — alle machen
    `BankIdentifier.COUNTRY_ALPHA_TO_NUMERIC[acc.bic[4:6]]`. Kommt das Konto aus der
    IBAN statt aus dem HKSPA-Abruf (Postbank-Workaround, gilt auch für die Sparkasse),
    ist die BIC leer → `''` als Schlüssel → KeyError('') beim HKKAZ-Aufbau; in der App
    sichtbar als „FinTS-Fehler: ''". Die Länderinfo steht auch in der IBAN (Stellen 1–2).
    """
    from fints import formals as _f
    from fints.formals import BankIdentifier
    gepatcht = []
    for name in ("KTZ1", "Account2", "Account3"):
        cls = getattr(_f, name, None)
        if cls is None or not hasattr(cls, "from_sepa_account"):
            continue
        _orig = cls.from_sepa_account.__func__

        def _macher(_orig=_orig, _name=name):
            def _from_sepa_account(kls, acc):
                bic = getattr(acc, "bic", "") or ""
                if len(bic) < 6:
                    land = (getattr(acc, "iban", "") or "")[:2].upper()
                    code = BankIdentifier.COUNTRY_ALPHA_TO_NUMERIC.get(land)
                    if code:
                        print(f"[bank-fints] {_name}: Länderkennzeichen aus IBAN ({land} → {code}), BIC fehlt")
                        # Einfachster tragfähiger Weg für ALLE drei Klassen (die Feldlisten
                        # unterscheiden sich: Account2 flach, Account3/KTZ1 mit verschachteltem
                        # bank_identifier): dem Original eine BIC-Attrappe unterschieben, deren
                        # Stellen 5–6 das richtige Länderkürzel tragen. Nur dieses Teilstück
                        # liest die Bibliothek aus — die Attrappe wandert NICHT in die Nachricht
                        # (Account2/3 übernehmen die BIC nicht; KTZ1 bekommt sie unten korrigiert).
                        class _Hilf:
                            pass
                        h = _Hilf()
                        for a in ("accountnumber", "subaccount", "blz", "iban"):
                            setattr(h, a, getattr(acc, a, ""))
                        h.bic = "XXXX" + land + "XX"
                        erg = _orig(kls, h)
                        # KTZ1 führt die BIC als eigenes Feld → Attrappe wieder entfernen
                        try:
                            if getattr(erg, "bic", None) == h.bic:
                                erg.bic = ""
                        except Exception:
                            pass
                        return erg
                return _orig(kls, acc)
            return _from_sepa_account

        cls.from_sepa_account = classmethod(_macher())
        gepatcht.append(name)
    print(f"[bank-fints] IBAN-Länder-Rückfall aktiv für: {', '.join(gepatcht) or '(keine)'}")


try:
    _patch_kontoklassen()
except Exception as _e:
    print(f"[bank-fints] Konto-Patch nicht aktiv ({type(_e).__name__}: {_e})")

FinTS3PinTanClient.is_challenge_structured = _safe_is_challenge_structured
FinTS3PinTanClient.is_tan_media_required = _safe_is_tan_media_required
# Der HKTAN-Rückfall hängt an einem Bibliotheks-Internum (IMPLEMENTED_HKTAN_VERSIONS).
# Fehlt es in einer künftigen python-fints-Version, darf das den Abruf NICHT abbrechen —
# dann bleibt einfach das Original-Verhalten aktiv (28.07.2026).
try:
    from fints.client import IMPLEMENTED_HKTAN_VERSIONS
    _orig_get_tan_segment = FinTS3PinTanClient._get_tan_segment
    FinTS3PinTanClient._get_tan_segment = _safe_get_tan_segment
except Exception as _e:
    print(f"[bank-fints] HKTAN-Rückfall nicht aktiv ({type(_e).__name__}: {_e}) — Original bleibt")

# python-fints setzt beim HTTP-POST KEIN Timeout → hängt endlos, wenn eine Bank
# die Verbindung offen hält oder stockt (beobachtet bei der Sparkasse-Anmeldung).
# Wir injizieren ein hartes Timeout, damit der Abruf sauber abbricht statt zu hängen.
FINTS_HTTP_TIMEOUT = 45
try:
    from fints.connection import FinTSHTTPSConnection as _FHConn
    _orig_fhc_init = _FHConn.__init__

    def _fhc_init_with_timeout(self, url):
        _orig_fhc_init(self, url)
        _orig_post = self.session.post

        def _post_with_timeout(*args, **kwargs):
            kwargs.setdefault("timeout", FINTS_HTTP_TIMEOUT)
            return _orig_post(*args, **kwargs)

        self.session.post = _post_with_timeout

    _FHConn.__init__ = _fhc_init_with_timeout
except Exception as _e:  # pragma: no cover
    print(f"[bank-fints] HTTP-Timeout-Patch nicht gesetzt: {_e}")


# ── Client-Aufbau ─────────────────────────────────────────────────────────

def _make_client(conn, pin, client_data=None):
    kwargs = {}
    if conn.get("product_id"):
        kwargs["product_id"] = conn["product_id"]
    if conn.get("tan_medium"):
        kwargs["tan_medium"] = conn["tan_medium"]
    if client_data is not None:
        kwargs["from_data"] = client_data
    return FinTS3PinTanClient(
        conn["blz"], conn["user_id"], pin, conn["fints_url"], **kwargs
    )


def _select_mechanism(client, conn):
    """TAN-Verfahren festlegen (vor Dialog-Eintritt). Bevorzugt decoupled."""
    try:
        client.fetch_tan_mechanisms()
    except Exception as e:
        cause = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
        print(f"[bank-fints] fetch_tan_mechanisms: {type(e).__name__}: {e}"
              + (f" || URSACHE: {type(cause).__name__}: {cause!r}" if cause else " || (keine __cause__)"))
        import traceback as _tb
        _tb.print_exc()
    mechs = client.get_tan_mechanisms() or {}
    want = conn.get("tan_mechanism")
    chosen = None
    if want and want in mechs:
        chosen = want
    else:
        # Bevorzugt: echtes decoupled-Flag → Name enthält "push" (z. B. BestSign-Push) → erstes
        decoupled = [k for k, v in mechs.items() if getattr(v, "decoupled", False)]
        push = [k for k, v in mechs.items() if "push" in (getattr(v, "name", "") or "").lower()]
        chosen = (decoupled or push or list(mechs.keys()) or [None])[0]
    if chosen:
        client.set_tan_mechanism(chosen)
    # TAN-Medium benennen, falls die Bank es verlangt (Postbank BestSign → Code 9210)
    medium_name = None
    # Decoupled-Verfahren (Sparkasse pushTAN 2.0, Postbank BestSign) brauchen KEINE
    # Medienbezeichnung — python-fints liefert dafuer ohnehin eine leere Liste und
    # merkt in seinem Quelltext an, dass "" als TAN-Medium in Ordnung ist. Der Abruf
    # kostet aber einen zusaetzlichen Dialog UND loest eine weitere Freigabe-Anfrage
    # auf dem Handy aus. Also bei decoupled ueberspringen.
    _mech = mechs.get(chosen) if chosen else None
    _decoupled = bool(getattr(_mech, "decoupled", False)) or \
        str(getattr(_mech, "zka_id", "") or "").lower() == "decoupled"
    try:
        if _decoupled:
            print(f"[bank-fints] {chosen}: decoupled — TAN-Medien-Abruf uebersprungen")
        elif client.is_tan_media_required():
            media = client.get_tan_media()
            media_list = list(media[1]) if isinstance(media, tuple) and len(media) > 1 else list(media or [])
            want_name = conn.get("tan_medium")
            chosen_m = None
            if want_name:
                chosen_m = next((m for m in media_list if getattr(m, "tan_medium_name", None) == want_name), None)
            chosen_m = chosen_m or (media_list[0] if media_list else None)
            if chosen_m is not None:
                client.set_tan_medium(chosen_m)
                medium_name = getattr(chosen_m, "tan_medium_name", None)
                if medium_name:
                    conn["tan_medium"] = medium_name  # für Persistenz nach oben reichen
    except Exception as e:
        print(f"[bank-fints] TAN-Medium-Auswahl: {type(e).__name__}: {e}")
    print(f"[bank-fints] TAN-Verfahren={chosen} Medium={medium_name}")


def account_dict_from_iban(iban, blz=None, bic=None):
    """Baut die Konto-Identifikation direkt aus der IBAN — umgeht den bei
    Postbank fehlerhaften HKSPA-Konten-Abruf. DE-IBAN: BBAN = 8 BLZ + 10 Konto."""
    iban = (iban or "").replace(" ", "").upper()
    acctno = ""
    if iban.startswith("DE") and len(iban) >= 22:
        bban = iban[4:]
        blz = blz or bban[:8]
        acctno = bban[8:]
    return {"iban": iban, "bic": (bic or ""), "accountnumber": acctno,
            "subaccount": "", "blz": (blz or "")}
    # Hinweis: eine leere BIC ist zulässig — das Länderkennzeichen leitet der
    # BankIdentifier-Patch oben aus der IBAN ab (Sparkassen liefern kein HKSPA-Konto).


def list_mechanisms(conn, pin):
    """Für die UI: verfügbare TAN-Verfahren {code: {name, decoupled}}."""
    client = _make_client(conn, pin)
    try:
        client.fetch_tan_mechanisms()
    except Exception:
        pass
    out = {}
    for k, v in (client.get_tan_mechanisms() or {}).items():
        out[k] = {"name": getattr(v, "name", str(k)),
                  "decoupled": bool(getattr(v, "decoupled", False))}
    return out


# ── mt940 → Buchungs-Dict (identisch zu bank_import._parse_ntry) ──────────

def _to_record(t, account_iban):
    d = getattr(t, "data", {}) or {}
    amt = d.get("amount")
    value = getattr(amt, "amount", None)
    currency = getattr(amt, "currency", None) or "EUR"
    if value is None:
        return None
    status = (d.get("status") or "").upper()  # 'C' / 'D'
    if status in ("C", "D"):
        is_credit = (status == "C")
    else:
        is_credit = (value >= 0)
    booking = d.get("entry_date") or d.get("date")
    val = d.get("date")
    purpose = (d.get("purpose") or "").strip()
    extra = (d.get("additional_purpose") or d.get("extra_details") or "").strip()
    if extra and extra not in purpose:
        purpose = (purpose + " " + extra).strip()
    return {
        "id": uuid.uuid4().hex,
        "booking_date": booking.isoformat() if hasattr(booking, "isoformat") else (str(booking) if booking else None),
        "value_date": val.isoformat() if hasattr(val, "isoformat") else (str(val) if val else None),
        "amount": abs(float(value)),
        "currency": currency,
        "direction": "credit" if is_credit else "debit",
        "account_iban": account_iban,
        "counterparty_name": d.get("applicant_name") or None,
        "counterparty_iban": d.get("applicant_iban") or None,
        "counterparty_bic": d.get("applicant_bin") or None,
        "purpose": purpose or None,
        "additional_info": d.get("posting_text") or None,
        "end_to_end_id": d.get("end_to_end_reference") or None,
        "bank_reference": d.get("bank_reference") or None,
        "transaction_id": d.get("customer_reference") or d.get("bank_reference") or None,
        "match_status": "open",
        "matched_invoice_id": None,
        "matched_at": None,
        "match_confidence": None,
        "source": "fints",
        "imported_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def _balance_dict(bal):
    amt = getattr(bal, "amount", bal)
    value = getattr(amt, "amount", amt)
    try:
        value = float(value)
    except Exception:
        return None
    return {
        "amount": value,
        "currency": getattr(amt, "currency", "EUR"),
        "date": getattr(bal, "date", None).isoformat() if hasattr(getattr(bal, "date", None), "isoformat") else None,
        "as_of": datetime.datetime.now().isoformat(timespec="seconds"),
    }


# ── Stage-Definitionen ────────────────────────────────────────────────────

def _account_from_state(state):
    a = state.get("account")
    return SEPAAccount(**a) if a else None


def _invoke(client, stage, conn, state):
    if stage == "info":
        return client.get_information()  # lokal aus BPD/UPD, nie TAN-pflichtig
    if stage == "accounts":
        return client.get_sepa_accounts()
    if stage == "transactions":
        acc = _account_from_state(state)
        end = datetime.date.today()
        start = end - datetime.timedelta(days=FETCH_DAYS)
        return client.get_transactions(acc, start, end)
    if stage == "balance":
        return client.get_balance(_account_from_state(state))
    raise ValueError(f"Unbekannte Stage: {stage}")


def _store_result(state, stage, value, conn):
    if stage == "info":
        accts = (value or {}).get("accounts", []) if isinstance(value, dict) else []
        want = (conn.get("iban") or "").replace(" ", "").upper()
        chosen = None
        for a in accts:
            if a.get("iban") and want and a["iban"].replace(" ", "").upper() == want:
                chosen = a
                break
        if not chosen:
            chosen = next((a for a in accts if a.get("iban")), None)
        if chosen:
            bi = chosen.get("bank_identifier")
            blz = getattr(bi, "bank_code", None) or conn.get("blz")
            state["account"] = {
                "iban": chosen.get("iban"),
                # Frueher stand hier "PBNKDEFFXXX" als Rueckfall — die Postbank-BIC
                # auf einem Sparkassen-Konto ergaebe eine falsche Kontokennung.
                # Fehlt die BIC, leitet der Patch oben das Land aus der IBAN ab.
                "bic": conn.get("bic") or getattr(bi, "bic", "") or "",
                "accountnumber": chosen.get("account_number"),
                "subaccount": chosen.get("subaccount_number") or "",
                "blz": blz,
            }
        elif not state.get("account") and conn.get("iban"):
            state["account"] = account_dict_from_iban(conn.get("iban"), conn.get("blz"), conn.get("bic"))
        # Ein Zugang fuehrt oft MEHRERE Konten (Sparkasse: drei). Frueher wurde nur
        # das eingetragene ausgelesen — Umsaetze und Saldo der anderen fehlten, und der
        # angezeigte „Saldo" passte zu keinem einzelnen Konto (02.09.2026).
        konten = []
        for a in accts:
            if not a.get("iban"):
                continue
            bi = a.get("bank_identifier")
            konten.append({
                "iban": a.get("iban"),
                "bic": getattr(bi, "bic", "") or conn.get("bic") or "",
                "accountnumber": a.get("account_number"),
                "subaccount": a.get("subaccount_number") or "",
                "blz": getattr(bi, "bank_code", None) or conn.get("blz"),
            })
        if konten:
            state["konten"] = konten
        return
    if stage == "accounts":
        accts = list(value or [])
        want = (conn.get("iban") or "").replace(" ", "").upper()
        chosen = None
        for a in accts:
            if want and (getattr(a, "iban", "") or "").replace(" ", "").upper() == want:
                chosen = a
                break
        chosen = chosen or (accts[0] if accts else None)
        if chosen is not None:
            state["account"] = {"iban": chosen.iban, "bic": chosen.bic,
                                "accountnumber": chosen.accountnumber,
                                "subaccount": chosen.subaccount, "blz": chosen.blz}
        state.setdefault("results", {})["accounts"] = [
            {"iban": a.iban, "bic": a.bic} for a in accts]
    elif stage == "transactions":
        acc_iban = (state.get("account") or {}).get("iban")
        recs = [r for r in (_to_record(t, acc_iban) for t in (value or [])) if r]
        state.setdefault("results", {})["transactions"] = recs
    elif stage == "balance":
        state.setdefault("results", {})["balance"] = _balance_dict(value) if value is not None else None


def _next_stage(stage):
    order = ["info", "transactions", "balance"]
    i = order.index(stage)
    return order[i + 1] if i + 1 < len(order) else None


def _paused_blob(client, resp):
    return {
        "dialog_data": client.pause_dialog(),
        "tan_data": resp.get_data(),
        "challenge": (getattr(resp, "challenge", "") or ""),
        "decoupled": bool(getattr(resp, "decoupled", False)),
        "mechanismus": (lambda: (client.get_current_tan_mechanism() if hasattr(client, "get_current_tan_mechanism") else None))(),
    }


# ── Ein-Dialog-Abruf (Sparkasse) ─────────────────────────────────────────
# Der Stufen-Abruf oben oeffnet PRO Stufe einen eigenen FinTS-Dialog und baut den
# Client dazwischen aus `deconstruct()`/`from_data` wieder auf. Die Postbank
# vertraegt das; die Sparkasse (banking-rl2.s-fints-pt-rl.de) nicht: der erste
# Dialog laeuft sauber, der zweite scheitert schon beim Dialogaufbau mit
#   9050 Die Nachricht enthaelt Fehler · 9800 Dialog abgebrochen
#   9340 Ungueltige Auftragsnachricht: Ungueltige Signatur
# (Befund 31.08.2026). python-fints meldet das nach oben als "PIN wrong?" —
# irrefuehrend, die PIN war nie das Problem.
#
# Der Weg hier macht es so, wie ein FinTS-Client es normalerweise tut: EIN Dialog,
# darin Kontoinfo, Umsaetze und Saldo. Damit entfallen der Wiederaufbau des
# Clients, der zweite TAN-Verfahrens-Abruf und die weiteren Dialog-Anmeldungen —
# aus drei Anmeldungen wird eine. Aktiv nur, wenn die Verbindung `ein_dialog`
# gesetzt hat; die Postbank bleibt auf dem erprobten Stufenweg.

def _ein_dialog(conn, pin, state, tan=None):
    _cid = conn.get("_id") or conn.get("bank_name") or "?"
    if state.get("mid_tan"):
        return _ein_dialog_fortsetzen(conn, pin, state, tan, _cid)

    client = _make_client(conn, pin, state.get("client_data"))
    _select_mechanism(client, conn)
    protokoll(_cid, "dialog_start", stufe="ein_dialog", verfahren=conn.get("tan_mechanism"),
              medium=conn.get("tan_medium"), url=conn.get("fints_url"))
    with client:
        try:
            info = client.get_information()      # lokal aus BPD/UPD, kein Bankverkehr
            _store_result(state, "info", info, conn)
            protokoll(_cid, "antwort", stufe="ein_dialog", phase="info",
                      typ=type(info).__name__, konto=(state.get("account") or {}).get("iban"))

            # ── Freigabe schon fuer die ANMELDUNG (Sparkasse pushTAN 2.0) ──
            # Die Bank beantwortet den Dialogaufbau mit 3955 „Auftrag empfangen —
            # bitte in Ihrer App freigeben" und behandelt den Dialog bis zur
            # Freigabe als nicht autorisiert. Wer das uebergeht und gleich den
            # Auftrag schickt, bekommt 9340 „Ungueltige Signatur" — genau das war
            # der Fehler am 31.08.2026. python-fints hinterlegt die offene
            # Freigabe in `init_tan_response`; hier wird sie abgewartet.
            offen = getattr(client, "init_tan_response", None)
            if offen is not None:
                blob = _paused_blob(client, offen)
                state["mid_tan"] = blob
                state["tan_phase"] = "anmeldung"
                state["client_data"] = client.deconstruct(including_private=True)
                protokoll(_cid, "freigabe_erforderlich", stufe="ein_dialog", phase="anmeldung",
                          decoupled=bool(blob.get("decoupled")), challenge=blob.get("challenge"))
                return ("tan_pending", state,
                        {"challenge": blob["challenge"], "decoupled": blob["decoupled"]})

            ende = datetime.date.today()
            beginn = ende - datetime.timedelta(days=FETCH_DAYS)
            resp = client.get_transactions(_account_from_state(state), beginn, ende)
        except Exception as e:
            _fehler_protokoll(_cid, "ein_dialog", e)
            raise
        protokoll(_cid, "antwort", stufe="ein_dialog", phase="umsaetze",
                  typ=type(resp).__name__, codes=_rueckmeldungen(resp))
        if isinstance(resp, NeedTANResponse):
            blob = _paused_blob(client, resp)
            state["mid_tan"] = blob
            state["tan_phase"] = "auftrag"
            state["client_data"] = client.deconstruct(including_private=True)
            return ("tan_pending", state,
                    {"challenge": blob["challenge"], "decoupled": blob["decoupled"]})
        _store_result(state, "transactions", resp, conn)
        _alle_konten_abrufen(client, conn, state, _cid)
    state["client_data"] = client.deconstruct(including_private=True)
    return ("done", state, _ergebnis(state))


def _ein_dialog_fortsetzen(conn, pin, state, tan, _cid):
    """Nach der Freigabe/TAN im selben Dialog weiterarbeiten."""
    mid = state["mid_tan"]
    client = _make_client(conn, pin, state.get("client_data"))
    _tanwert = str(tan or "").strip()
    phase = state.get("tan_phase") or "auftrag"
    protokoll(_cid, "tan_fortsetzen", stufe="ein_dialog", phase=phase,
              decoupled=bool(mid.get("decoupled")), eingabe_laenge=len(_tanwert),
              verfahren=mid.get("mechanismus"))
    with client.resume_dialog(mid["dialog_data"]):
        try:
            offen = NeedRetryResponse.from_data(mid["tan_data"])
            # `from_data` verliert das decoupled-Kennzeichen: python-fints baut das
            # Objekt in `_from_data_v1` ohne diesen Parameter wieder auf, er faellt
            # auf False zurueck. Dann schickt send_tan TAN-Prozess '2' — eine
            # TAN-EINREICHUNG mit leerer TAN — statt der Status-Abfrage 'S'.
            # Die Sparkasse quittiert das mit 9340 „Ungueltige Signatur"
            # (Befund 31.08.2026, im Mitschnitt als tan_process = '2' sichtbar).
            if mid.get("decoupled"):
                offen.decoupled = True
            resp = client.send_tan(offen, _tanwert)
        except Exception as e:
            _fehler_protokoll(_cid, "ein_dialog", e, phase=phase)
            raise
        codes = _rueckmeldungen(resp)
        protokoll(_cid, "antwort", stufe="ein_dialog", phase="freigabe",
                  typ=type(resp).__name__, codes=codes)

        # Noch nicht freigegeben (Bank antwortet 3956): weiter warten, erneut anhalten.
        if isinstance(resp, NeedTANResponse):
            blob = _paused_blob(client, resp)
            state["mid_tan"] = blob
            state["client_data"] = client.deconstruct(including_private=True)
            return ("tan_pending", state,
                    {"challenge": blob["challenge"], "decoupled": blob["decoupled"],
                     "warten": True})

        # Verfahren MIT angezeigter TAN: erst jetzt eine Eingabe verlangen.
        if (any(str(c).startswith(("9931", "3931", "9942", "9941")) for c in codes)
                and not _tanwert):
            protokoll(_cid, "tan_eingabe_erforderlich", stufe="ein_dialog", codes=codes)
            return ("tan_pending", state,
                    {"challenge": mid.get("challenge"), "decoupled": False,
                     "tan_erforderlich": True,
                     "hinweis": "Die Bank verlangt die Eingabe der angezeigten TAN."})

        state["_tan_freigegeben_am"] = datetime.datetime.now().isoformat(timespec="seconds")
        protokoll(_cid, "tan_akzeptiert", stufe="ein_dialog", phase=phase,
                  verfahren=mid.get("mechanismus"))
        state["mid_tan"] = None

        if phase == "anmeldung":
            # Die Freigabe galt der Anmeldung — der eigentliche Auftrag kommt erst jetzt.
            ende = datetime.date.today()
            beginn = ende - datetime.timedelta(days=FETCH_DAYS)
            try:
                resp = client.get_transactions(_account_from_state(state), beginn, ende)
            except Exception as e:
                _fehler_protokoll(_cid, "ein_dialog", e, phase="umsaetze")
                raise
            protokoll(_cid, "antwort", stufe="ein_dialog", phase="umsaetze",
                      typ=type(resp).__name__, codes=_rueckmeldungen(resp))
            if isinstance(resp, NeedTANResponse):
                blob = _paused_blob(client, resp)
                state["mid_tan"] = blob
                state["tan_phase"] = "auftrag"
                state["client_data"] = client.deconstruct(including_private=True)
                return ("tan_pending", state,
                        {"challenge": blob["challenge"], "decoupled": blob["decoupled"]})

        _store_result(state, "transactions", resp, conn)
        _alle_konten_abrufen(client, conn, state, _cid)
    state["client_data"] = client.deconstruct(including_private=True)
    state["mid_tan"] = None
    return ("done", state, _ergebnis(state))


def _fehler_protokoll(cid, stufe, e, **felder):
    ursache = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    import traceback as _tb
    zeilen = [z.strip() for z in _tb.format_exc().splitlines() if z.strip().startswith("File ")][-6:]
    protokoll(cid, "fehler", stufe=stufe, typ=type(e).__name__, meldung=str(e),
              ursache=(f"{type(ursache).__name__}: {ursache}" if ursache else None),
              spur=zeilen, **felder)


def _saldo_im_dialog(client, conn, state, cid):
    """Saldo im laufenden Dialog nachziehen — optional, darf nie den Abruf kippen."""
    try:
        bal = client.get_balance(_account_from_state(state))
        if isinstance(bal, NeedTANResponse):
            protokoll(cid, "saldo_uebersprungen", grund="TAN-pflichtig")
            return
        _store_result(state, "balance", bal, conn)
    except Exception as e:
        protokoll(cid, "saldo_uebersprungen", typ=type(e).__name__, meldung=str(e))


def _alle_konten_abrufen(client, conn, state, cid):
    """Umsaetze und Saldo ALLER Konten dieses Zugangs holen — im laufenden Dialog.

    Das eingetragene Konto ist zu diesem Zeitpunkt bereits abgerufen; hier kommen die
    uebrigen dazu. Ein Konto, das die Bank verweigert oder das eine eigene TAN
    verlangen wuerde, wird uebersprungen — der Abruf darf daran nie scheitern.
    Je Konto wird der Saldo einzeln gemerkt (`balances`), damit die Oberflaeche nicht
    eine Summe als Kontostand eines einzelnen Kontos ausweist.
    """
    konten = state.get("konten") or ([state["account"]] if state.get("account") else [])
    haupt = (state.get("account") or {}).get("iban")
    ende = datetime.date.today()
    beginn = ende - datetime.timedelta(days=FETCH_DAYS)
    umsaetze = list((state.get("results") or {}).get("transactions") or [])
    salden = []
    for k in konten:
        try:
            acc = SEPAAccount(**k)
        except Exception as e:
            protokoll(cid, "konto_uebersprungen", iban=k.get("iban"), meldung=str(e))
            continue
        if k.get("iban") != haupt:
            try:
                resp = client.get_transactions(acc, beginn, ende)
                if isinstance(resp, NeedTANResponse):
                    protokoll(cid, "konto_uebersprungen", iban=k.get("iban"),
                              grund="TAN-pflichtig")
                else:
                    neue = [r for r in (_to_record(t, k["iban"]) for t in (resp or [])) if r]
                    umsaetze += neue
                    protokoll(cid, "konto_umsaetze", iban=k.get("iban"), anzahl=len(neue))
            except Exception as e:
                protokoll(cid, "konto_fehler", iban=k.get("iban"),
                          typ=type(e).__name__, meldung=str(e))
        try:
            bal = client.get_balance(acc)
            if isinstance(bal, NeedTANResponse):
                protokoll(cid, "saldo_uebersprungen", iban=k.get("iban"),
                          grund="TAN-pflichtig")
                continue
            d = _balance_dict(bal)
            if d:
                d["iban"] = k.get("iban")
                salden.append(d)
                protokoll(cid, "konto_saldo", iban=k.get("iban"), betrag=d.get("amount"))
        except Exception as e:
            protokoll(cid, "saldo_uebersprungen", iban=k.get("iban"),
                      typ=type(e).__name__, meldung=str(e))
    ergebnis = state.setdefault("results", {})
    ergebnis["transactions"] = umsaetze
    ergebnis["balances"] = salden
    eigener = next((x for x in salden if x.get("iban") == haupt), None)
    if eigener:
        ergebnis["balance"] = eigener


def _ergebnis(state):
    res = state.get("results", {})
    return {"transactions": res.get("transactions", []),
            "balance": res.get("balance"),
            "balances": res.get("balances", []),
            "accounts": res.get("accounts", []),
            "account_iban": (state.get("account") or {}).get("iban")}


# ── Haupt-Schritt (Start ODER Poll, je nach state["mid_tan"]) ─────────────

def step(conn, pin, state=None, tan=None):
    """Treibt den Abruf eine Stufe weiter. Gibt (status, state, public) zurück.

    tan: vom Nutzer eingegebene TAN (Sparkassen-Workflow, 28.07.2026).
         Bei decoupled-Verfahren (Postbank BestSign) bleibt sie leer — dort genügt die
         Freigabe in der App und die Bank quittiert die leere TAN. Sparkassen nutzen
         pushTAN/chipTAN NICHT decoupled: dort MUSS die angezeigte TAN übermittelt werden,
         sonst antwortet die Bank mit „TAN ungültig/erforderlich" und der Dialog bricht ab.
    """
    if state is None:
        # Immer mit "info" starten: liefert die authoritativen Kontodaten der Bank
        # (korrektes account_number/subaccount) — zuverlässiger als aus der IBAN geraten.
        state = {"stage": "info", "client_data": None, "mid_tan": None,
                 "results": {}, "account": conn.get("account")}

    # Banken, die den Dialogwechsel nicht mitmachen (Sparkasse): alles in EINEM Dialog.
    if conn.get("ein_dialog"):
        return _ein_dialog(conn, pin, state, tan=tan)

    stage = state["stage"]
    client = _make_client(conn, pin, state.get("client_data"))
    mid = state.get("mid_tan")
    print(f"[bank-fints] step stage={stage} resume={bool(mid)}")

    _cid = conn.get("_id") or conn.get("bank_name") or "?"
    if mid:
        # Laufenden TAN-Vorgang fortsetzen: decoupled → leere TAN genügt (App-Freigabe),
        # sonst die eingegebene TAN übermitteln (Sparkasse pushTAN/chipTAN).
        # Freigabe-Modelle unterscheiden (28.07.2026):
        #  a) decoupled (Postbank BestSign): Freigabe in der App, leere TAN quittiert.
        #  b) Sparkasse S-pushTAN: die App zeigt „Auftrag freigeben" — ebenfalls OHNE
        #     eingetippte TAN. Der Server muss dann WARTEN und erneut senden, bis die
        #     Freigabe erteilt ist; die Bank antwortet bis dahin mit einer neuen
        #     TAN-Anforderung (→ pollt weiter).
        #  c) chipTAN/Verfahren MIT angezeigter TAN: erst dann ist eine Eingabe nötig.
        # Deshalb IMMER erst leer senden und nur dann eine TAN verlangen, wenn die Bank
        # eine ungültige/fehlende TAN ausdrücklich rügt (Codes 9931/3931/9942).
        _tanwert = str(tan or "").strip()
        protokoll(_cid, "tan_fortsetzen", stufe=stage, decoupled=bool(mid.get("decoupled")),
                  eingabe_laenge=len(_tanwert), verfahren=mid.get("mechanismus"))
        with client.resume_dialog(mid["dialog_data"]):
            resp = client.send_tan(NeedRetryResponse.from_data(mid["tan_data"]), _tanwert)
            print(f"[bank-fints] stage={stage} (resume) resp={type(resp).__name__}")
            _codes = _rueckmeldungen(resp)
            protokoll(_cid, "antwort", stufe=stage, phase="resume", typ=type(resp).__name__, codes=_codes)
            _tanRuege = any(str(c).startswith(("9931", "3931", "9942", "9941")) for c in _codes)
            if _tanRuege and not _tanwert:
                protokoll(_cid, "tan_eingabe_erforderlich", stufe=stage, codes=_codes)
                state["mid_tan"] = mid
                return ("tan_pending", state, {"challenge": mid.get("challenge"), "decoupled": False,
                                               "tan_erforderlich": True,
                                               "hinweis": "Die Bank verlangt die Eingabe der angezeigten TAN."})
            outcome = _classify(client, stage, resp)
    else:
        _select_mechanism(client, conn)
        protokoll(_cid, "dialog_start", stufe=stage, verfahren=conn.get("tan_mechanism"),
                  medium=conn.get("tan_medium"), url=conn.get("fints_url"))
        with client:
            try:
                resp = _invoke(client, stage, conn, state)
            except Exception as e:
                ursache = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
                # ── PIN-SCHUTZ (28.07.2026) ─────────────────────────────────────
                # Antwortet die Bank mit einem PIN-Fehler, DARF nicht weiter probiert werden:
                # Banken sperren den Zugang nach i. d. R. 3 Fehlversuchen. Wir markieren das
                # im Protokoll unmissverständlich; der Aufrufer (webapp) sperrt die Verbindung.
                if type(e).__name__ == "FinTSClientPINError" or "PIN" in str(e).upper():
                    protokoll(_cid, "PIN_FEHLER_ABBRUCH", stufe=stage, typ=type(e).__name__,
                              meldung=str(e),
                              warnung="Keine weiteren Versuche! Bankzugang kann nach 3 Fehlversuchen gesperrt werden.")
                    e.pin_fehler = True
                import traceback as _tb
                _spur = _tb.format_exc()
                # Nur die Bibliotheks-Zeilen behalten — die zeigen, WO es klemmt (28.07.2026)
                _zeilen = [z.strip() for z in _spur.splitlines() if z.strip().startswith("File ")][-6:]
                protokoll(_cid, "fehler", stufe=stage, typ=type(e).__name__, meldung=str(e),
                          ursache=(f"{type(ursache).__name__}: {ursache}" if ursache else None),
                          spur=_zeilen)
                print(_spur)
                raise
            print(f"[bank-fints] stage={stage} resp={type(resp).__name__}")
            protokoll(_cid, "antwort", stufe=stage, typ=type(resp).__name__, codes=_rueckmeldungen(resp))
            outcome = _classify(client, stage, resp)

    state["client_data"] = client.deconstruct(including_private=True)

    if outcome[0] == "tan":
        state["mid_tan"] = outcome[1]
        return ("tan_pending", state, {"challenge": outcome[1]["challenge"],
                                       "decoupled": outcome[1]["decoupled"]})

    # Stufe abgeschlossen
    if mid:
        # Die TAN wurde von der Bank akzeptiert → Zeitpunkt der starken Authentifizierung
        # festhalten (PSD2: danach ~90 Tage TAN-freie Abrufe). Wird oben persistiert.
        state["_tan_freigegeben_am"] = datetime.datetime.now().isoformat(timespec="seconds")
        protokoll(_cid, "tan_akzeptiert", stufe=stage, verfahren=mid.get("mechanismus"))
    state["mid_tan"] = None
    _store_result(state, stage, outcome[1], conn)
    nxt = _next_stage(stage)
    if nxt is None:
        res = state.get("results", {})
        return ("done", state, {
            "transactions": res.get("transactions", []),
            "balance": res.get("balance"),
            "accounts": res.get("accounts", []),
            "account_iban": (state.get("account") or {}).get("iban"),
        })
    state["stage"] = nxt
    # Nächste Stufe sofort versuchen (dank gespeicherter SCA meist TAN-frei)
    return step(conn, pin, state)


def _rueckmeldungen(resp):
    """Bank-Rückmeldecodes (HIRMS) aus einer Antwort ziehen — die eigentliche Fehlerquelle.
    Beispiele: 3920 (zugelassene Verfahren), 9931 (TAN falsch), 9010 (Auftrag abgelehnt)."""
    aus = []
    try:
        for seg in (getattr(resp, "responses", None) or []):
            code = getattr(seg, "code", None)
            text = getattr(seg, "text", None)
            if code is not None:
                aus.append(f"{code} {text or ''}".strip())
    except Exception:
        pass
    return aus[:12]


def _classify(client, stage, resp):
    """-> ('tan', paused) wenn TAN nötig, sonst ('ok', value).
    Für die Saldo-Stufe wird eine TAN-Anforderung übersprungen (Saldo ist optional)."""
    if isinstance(resp, NeedTANResponse):
        if stage == "balance":
            return ("ok", None)  # Saldo nicht TAN-pflichtig erzwingen
        return ("tan", _paused_blob(client, resp))
    return ("ok", resp)
