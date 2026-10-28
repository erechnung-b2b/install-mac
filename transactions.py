"""
E-Rechnungssystem – Auftragsmanagement / Vorgänge
8-Stufen-Workflow: Lieferant → Angebot einholen → Bestellung → Kundenangebot
→ Auftragseingang → Lieferschein → E-Rechnung → Mahnung.
Jede Stufe: Datum, Häkchen-Freigabe, eigene Positionen, Audit-Trail.
"""
from __future__ import annotations
import copy, hashlib, json, uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional


def _satz(wert, standard=19.0):
    """Steuersatz als Zahl; nur fehlend/ungültig -> Standard. 0 % bleibt 0 %."""
    if wert is None or str(wert).strip() == "":
        return standard
    try:
        return float(str(wert).strip().replace(",", "."))
    except ValueError:
        return standard


# ── Konstanten ───────────────────────────────────────────────────────

STEP_KEYS = [
    "supplier_quote",      # Angebot Lieferant
    "purchase_order",      # Bestellung an Lieferant
    "supplier_invoice",    # Eingangsrechnung Lieferant
    "customer_quote",      # Angebot an Kunde
    "order_intake",        # Auftragseingang (Bestätigung vom Kunden)
    "order_confirmation",  # Auftragsbestätigung (von uns an Kunde)
    "delivery_note",       # Lieferschein
    "invoice",             # E-Rechnung
    "dunning",             # Mahnung
]

STEP_LABELS = {
    "supplier_quote":      "Anfrage",
    "purchase_order":      "Bestellung",
    "supplier_invoice":    "Eingangsrechnung",
    "customer_quote":      "Angebot Kunde",
    "order_intake":        "Auftragseingang",
    "order_confirmation":  "Auftragsbestätigung",
    "delivery_note":       "Lieferschein",
    "invoice":             "E-Rechnung",
    "dunning":             "Mahnung",
}

DOC_PREFIXES = {
    "supplier_quote":      "ANG-L",
    "purchase_order":      "BEST",
    "supplier_invoice":    "ER",
    "customer_quote":      "ANG-K",
    "order_intake":        "AUFT",
    "order_confirmation":  "AB",
    "delivery_note":       "LIEF",
    "invoice":             "RE",
    "dunning":             "MAH",
}


class SpaetereStufeFreigegeben(ValueError):
    """Anpassen/Stornieren berührt eine Stufe, nach der schon etwas freigegeben ist.

    Kein harter Fehler: die Oberfläche fragt nach und darf mit `erzwingen` fortfahren.
    """

    def __init__(self, nachricht: str, stufen: list):
        super().__init__(nachricht)
        self.stufen = stufen


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


# ── Positionen ───────────────────────────────────────────────────────

def make_position(pos_nr: int, description: str = "", quantity: float = 1,
                  unit: str = "Stk", unit_price: float = 0,
                  discount_percent: float = 0, discount_amount: float = 0,
                  tax_rate: float = 19) -> dict:
    """Erzeugt eine einzelne Position."""
    gross = round(quantity * unit_price, 2)
    disc = round(discount_amount or (gross * discount_percent / 100), 2)
    net = round(gross - disc, 2)
    return {
        "pos_nr": pos_nr,
        "description": description,
        "quantity": quantity,
        "unit": unit,
        "unit_price": unit_price,
        "discount_percent": discount_percent,
        "discount_amount": disc,
        "net_amount": net,
        "tax_rate": tax_rate,
    }


def calc_step_totals(step: dict) -> dict:
    """Berechnet Summen aus Positionen + Dokumentrabatt/-zuschlag."""
    positions = step.get("positions") or []
    net_sum = sum(p.get("net_amount", 0) for p in positions)

    doc_disc_pct = step.get("doc_discount_percent", 0)
    doc_disc_amt = step.get("doc_discount_amount", 0)
    doc_surcharge = step.get("doc_surcharge_amount", 0)

    if doc_disc_pct and not doc_disc_amt:
        doc_disc_amt = round(net_sum * doc_disc_pct / 100, 2)

    subtotal = round(net_sum - doc_disc_amt + doc_surcharge, 2)

    # Steuer nach Sätzen gruppieren
    tax_groups = {}
    for p in positions:
        rate = p.get("tax_rate", 19)
        tax_groups.setdefault(rate, 0)
        tax_groups[rate] += p.get("net_amount", 0)

    # Steuer auf Subtotal proportional verteilen (nach Dokumentrabatt)
    factor = subtotal / net_sum if net_sum else 0
    total_tax = 0
    for rate, base in tax_groups.items():
        adjusted_base = round(base * factor, 2)
        total_tax += round(adjusted_base * rate / 100, 2)

    total_gross = round(subtotal + total_tax, 2)

    return {
        "net_sum": net_sum,
        "doc_discount_amount": doc_disc_amt,
        "doc_surcharge_amount": doc_surcharge,
        "subtotal": subtotal,
        "total_tax": round(total_tax, 2),
        "total_gross": total_gross,
    }


# ── Leerer Step ──────────────────────────────────────────────────────

def _empty_step() -> dict:
    return {
        "status": "OFFEN",
        "date": None,
        "due_date": None,
        "reference": None,
        "document_id": None,
        "positions": [],
        "doc_discount_percent": 0,
        "doc_discount_amount": 0,
        "doc_surcharge_amount": 0,
        "amount": 0,
        "approved": False,
        "approved_at": None,
        "approved_by": None,
        "comment": "",
        "history": [],
    }


def _empty_delivery_step() -> dict:
    """Lieferschein-Step mit Teillieferungs-Array."""
    step = _empty_step()
    step["deliveries"] = []
    return step


def _empty_payment_invoice(position: int, ref: str) -> dict:
    """Eine Abschlagsrechnung (Anzahlung / Teilrechnung) im Vorgang."""
    return {
        "id": str(uuid.uuid4()),
        "reference": ref,            # z.B. AR-2026-0001
        "position": position,        # Reihenfolge 1, 2, 3, ...
        "title": f"Abschlagsrechnung {position}",
        "date": _today(),
        "amount_net": 0.0,
        "tax_rate": 19.0,
        "amount_gross": 0.0,
        "description": "",           # Was wird abgerechnet (Pos.-Hinweis)
        "status": "DRAFT",           # DRAFT, SENT, PAID
        "sent_at": "",
        # Wie und wohin verschickt wurde (07.09.2026: "es laesst sich nicht
        # erkennen wie die Abschlagrechnung versandt wurde"). Frueher gab es nur
        # das Flag SENT, ohne Empfaenger, ohne Nachweis.
        "sent_to": "",               # Empfaenger-Adresse
        "sent_channel": "",          # 'email' = vom System versandt, 'manuell' = ausserhalb
        "sent_message_id": "",       # Message-ID der Mail = Schluessel zum Versandprotokoll
        "sent_note": "",             # bei 'manuell': wie es hinausging
        # Die Abschlagsrechnung als pruefbare Rechnung im Register (XRechnung,
        # Validierung, GoBD-Archiv). Vorher lebte sie nur in dieser Datei.
        "invoice_id": "",
        "invoice_number": "",
        "paid_at": "",
        "paid_amount": 0.0,
        "paid_via_booking": "",
        "document_id": "",
        "notes": "",
        "history": [{"action": "ANGELEGT", "at": _ts(), "user": "system"}],
    }


def _recalc_payment_invoice_gross(p: dict) -> None:
    net = float(p.get("amount_net", 0) or 0)
    rate = float(p.get("tax_rate", 19) or 0)
    p["amount_gross"] = round(net * (1 + rate/100.0), 2)


def _empty_dunning_step() -> dict:
    """Mahnungs-Step mit Stufen-Array."""
    step = _empty_step()
    step["levels"] = []
    del step["positions"]  # Mahnungen haben keine eigenen Positionen
    return step


# ── Nummernkreise ────────────────────────────────────────────────────

class NumberSequence:
    """Verwaltet Nummernkreise je Prefix und Jahr."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text("utf-8"))
            except Exception:
                pass
        return {}

    def _save(self, data: dict):
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")

    def next(self, prefix: str, year: int = None) -> str:
        """Gibt die nächste Nummer zurück, z.B. 'BEST-2026-0001'."""
        year = year or date.today().year
        key = f"{prefix}-{year}"
        data = self._load()
        counter = data.get(key, 0) + 1
        data[key] = counter
        self._save(data)
        return f"{prefix}-{year}-{counter:04d}"

    def current(self, prefix: str, year: int = None) -> int:
        year = year or date.today().year
        key = f"{prefix}-{year}"
        return self._load().get(key, 0)


# ── TransactionManager ──────────────────────────────────────────────

class TransactionManager:
    """Verwaltet Vorgänge (Auftragsmanagement) in transactions.json."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "transactions.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.numbers = NumberSequence(self.data_dir / "number_sequences.json")

    # ── Persistenz ──

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text("utf-8"))
            except Exception:
                pass
        return []

    def _save(self, txns: list[dict]):
        self.path.write_text(
            json.dumps(txns, indent=2, ensure_ascii=False), "utf-8"
        )

    def _find(self, txns: list[dict], txn_id: str) -> Optional[dict]:
        for t in txns:
            if t["id"] == txn_id:
                return t
        return None

    # ── CRUD ──

    def list_all(self, status: str = None, supplier_id: str = None,
                 buyer_id: str = None) -> list[dict]:
        txns = self._load()
        if status:
            txns = [t for t in txns if t.get("status") == status]
        if supplier_id:
            txns = [t for t in txns if t.get("supplier_id") == supplier_id]
        if buyer_id:
            txns = [t for t in txns if t.get("buyer_id") == buyer_id]
        return txns

    def get(self, txn_id: str) -> Optional[dict]:
        txn = self._find(self._load(), txn_id)
        if txn:
            self._ensure_all_steps(txn)
        return txn

    def _ensure_all_steps(self, txn: dict):
        """Stellt sicher, dass alle STEP_KEYS im Vorgang existieren (Migration)."""
        steps = txn.get("steps", {})
        changed = False
        for key in STEP_KEYS:
            if key not in steps:
                if key == "delivery_note":
                    steps[key] = _empty_delivery_step()
                elif key == "dunning":
                    steps[key] = _empty_dunning_step()
                else:
                    steps[key] = _empty_step()
                changed = True
        if changed:
            txn["steps"] = steps
            txns = self._load()
            for i, t in enumerate(txns):
                if t.get("id") == txn["id"]:
                    txns[i] = txn
                    self._save(txns)
                    break

    def create(self, data: dict) -> dict:
        txns = self._load()
        txn_id = self.numbers.next("V")

        txn = {
            "id": txn_id,
            "subject": data.get("subject", "").strip(),
            "supplier_id": data.get("supplier_id", ""),
            "buyer_id": data.get("buyer_id", ""),
            "supplier_name": data.get("supplier_name", ""),
            "buyer_name": data.get("buyer_name", ""),
            "status": "NEU",
            "notes": data.get("notes", ""),
            "created_at": _ts(),
            "updated_at": _ts(),
            "steps": {},
        }

        # Steps initialisieren
        for key in STEP_KEYS:
            if key == "delivery_note":
                txn["steps"][key] = _empty_delivery_step()
            elif key == "dunning":
                txn["steps"][key] = _empty_dunning_step()
            else:
                txn["steps"][key] = _empty_step()

        txns.append(txn)
        self._save(txns)
        return txn

    def update(self, txn_id: str, data: dict) -> Optional[dict]:
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            return None
        for key in ("subject", "supplier_id", "buyer_id", "supplier_name",
                     "buyer_name", "notes", "status", "checklist"):
            if key in data:
                txn[key] = data[key]
        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    def delete(self, txn_id: str) -> bool:
        txns = self._load()
        before = len(txns)
        txns = [t for t in txns if t["id"] != txn_id]
        if len(txns) < before:
            self._save(txns)
            return True
        return False

    # ── Aktueller Step ──

    def current_step(self, txn: dict) -> Optional[str]:
        """Gibt den Key des nächsten offenen Steps zurück.
        Ohne Lieferant ist es ein reines Kundenangebot → die Lieferanten-Steps
        (Anfrage/Bestellung/Eingangsrechnung) werden als Standard übersprungen,
        damit der Vorgang direkt beim Kundenangebot bleibt."""
        supplier_steps = ("supplier_quote", "purchase_order", "supplier_invoice")
        has_supplier = bool(str(txn.get("supplier_name") or txn.get("supplier_id") or "").strip())
        for key in STEP_KEYS:
            step = txn["steps"].get(key, {})
            # Ohne Lieferant sind die Lieferanten-Steps nicht der Einstieg → überspringen
            # (die Daten bleiben erhalten und bleiben über den Stepper erreichbar)
            if not has_supplier and key in supplier_steps and not step.get("approved"):
                continue
            if not step.get("approved") and step.get("status") != "UEBERSPRUNGEN":
                return key
        return None  # Alle erledigt

    # ── Step bearbeiten ──

    def update_step(self, txn_id: str, step_key: str, data: dict,
                    user: str = "system") -> dict:
        """Aktualisiert einen Step (Positionen, Datum, Betrag etc.)."""
        if step_key not in STEP_KEYS:
            raise ValueError(f"Unbekannter Step: {step_key}")

        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")

        step = txn["steps"].get(step_key)
        if not step:
            raise ValueError(f"Step {step_key} nicht im Vorgang.")

        # Felder aktualisieren
        for key in ("date", "due_date", "reference", "document_id", "comment",
                     "positions", "doc_discount_percent", "doc_discount_amount",
                     "doc_surcharge_amount", "intro_text", "closing_text",
                     "payment_terms", "attachments",
                     # Eingangsrechnung als offener Posten (supplier_invoice-Schritt)
                     "eingangsrechnung_id", "eingang_invoice_number",
                     "eingang_supplier", "eingang_amount",
                     # Dienstvertrag fuer DIESES Angebot unterdruecken (19.08.2026).
                     # Ohne Flag haengt der Generator ihn an JEDES Kundenangebot, sobald
                     # keine Position ein "Vertrag separat"-Produkt ist — beim TGA-Paket
                     # ist das nie der Fall, dort gehoert er aber nicht dran.
                     "kein_vertrag",
                     # Vertragswahl + Gebäude für den ausgefüllten Dienstvertrag (16.10.2026) —
                     # standen bisher nicht in der Liste und wurden still verworfen
                     "vertrag_dateien", "vertrag_datei", "vertrag_gebaeude"):
            if key in data:
                step[key] = data[key]

        # Betrag aus Positionen berechnen
        if step.get("positions"):
            totals = calc_step_totals(step)
            step["amount"] = totals["total_gross"]

        if step["status"] == "OFFEN":
            step["status"] = "IN_BEARBEITUNG"

        step["history"].append({
            "action": "BEARBEITET",
            "user": user,
            "at": _ts(),
            "detail": f"Felder aktualisiert: {', '.join(data.keys())}",
        })

        txn["status"] = "IN_BEARBEITUNG"
        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    # ── Freigabe (Häkchen) ──

    def can_approve(self, txn: dict, step_key: str) -> tuple[bool, str]:
        """
        Prüft ob ein Step freigegeben werden kann.
        Freie Reihenfolge: jeder Step darf jederzeit freigegeben werden.
        Frühere offene Steps werden beim Freigeben automatisch als ÜBERSPRUNGEN markiert.
        """
        if step_key not in STEP_KEYS:
            return False, f"Unbekannter Step: {step_key}"
        return True, ""

    def approve_step(self, txn_id: str, step_key: str,
                     user: str = "system", comment: str = "") -> dict:
        """Setzt das Häkchen für einen Step."""
        if step_key not in STEP_KEYS:
            raise ValueError(f"Unbekannter Step: {step_key}")

        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")

        # Eine stornierte Stufe wird nicht stillschweigend wieder freigegeben —
        # sonst liefe der Vorgang nach einer Absage doch wieder mit (21.09.2026).
        if (txn["steps"].get(step_key) or {}).get("status") == "STORNIERT":
            raise ValueError(
                f"'{STEP_LABELS.get(step_key, step_key)}' ist storniert — "
                "erst das Storno aufheben (Vorgang wieder öffnen)."
            )

        ok, reason = self.can_approve(txn, step_key)
        if not ok:
            raise ValueError(reason)

        # Frühere unberührte Steps automatisch als ÜBERSPRUNGEN markieren
        idx = STEP_KEYS.index(step_key)
        for prev_key in STEP_KEYS[:idx]:
            prev = txn["steps"].get(prev_key, {})
            # Nur Steps überspringen die bisher gar nicht angefasst wurden
            if (not prev.get("approved")
                    and prev.get("status") != "UEBERSPRUNGEN"
                    and prev.get("status") != "ERLEDIGT"
                    and not prev.get("reference")
                    and not prev.get("date")):
                prev["status"] = "UEBERSPRUNGEN"
                prev["history"].append({
                    "action": "UEBERSPRUNGEN_AUTO",
                    "user": user,
                    "at": _ts(),
                    "comment": f"Automatisch übersprungen bei Freigabe von {STEP_LABELS.get(step_key, step_key)}",
                })

        step = txn["steps"][step_key]
        step["approved"] = True
        step["approved_at"] = _ts()
        step["approved_by"] = user
        step["status"] = "ERLEDIGT"
        if comment:
            step["comment"] = comment
        if not step["date"]:
            step["date"] = _today()

        step["history"].append({
            "action": "FREIGEGEBEN",
            "user": user,
            "at": _ts(),
            "comment": comment,
        })

        # Auto-Referenz setzen
        if not step.get("reference"):
            # Rechnungen ziehen ihre Nummer aus DEMSELBEN Kreis wie Energieausweis-/
            # Direktrechnungen (einheitlich, GoBD) — via injizierter invoice_number_fn
            # (webapp: _next_invoice_number, 5-stellig). Andere Belegarten unverändert.
            if step_key == "invoice" and getattr(self, "invoice_number_fn", None):
                step["reference"] = self.invoice_number_fn()
            else:
                prefix = DOC_PREFIXES.get(step_key, "DOK")
                step["reference"] = self.numbers.next(prefix)

        # ── Positionen an nächsten Step weiterreichen ──
        # Wenn der nächste Step noch keine Positionen hat, kopieren
        FORWARD_MAP = {
            "supplier_quote":     "purchase_order",      # Angebotsanfrage → Bestellung
            "purchase_order":     "supplier_invoice",    # Bestellung → Eingangsrechnung
            "customer_quote":     "order_intake",        # Kundenangebot → Auftragseingang
            "order_intake":       "order_confirmation",  # Auftragseingang → Auftragsbestätigung
            "order_confirmation": "delivery_note",       # Auftragsbestätigung → Lieferschein
            "delivery_note":      "invoice",             # Lieferschein → Rechnung
        }
        next_key = FORWARD_MAP.get(step_key)
        if next_key and step.get("positions"):
            next_step = txn["steps"].get(next_key, {})
            self._positionen_weiterreichen(step, step_key, next_step, next_key, user)

        # Gesamtstatus prüfen
        if all(txn["steps"][k].get("approved") or
               txn["steps"][k].get("status") == "UEBERSPRUNGEN"
               for k in STEP_KEYS):
            txn["status"] = "ABGESCHLOSSEN"

        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    def unapprove_step(self, txn_id: str, step_key: str,
                       user: str = "system") -> dict:
        """Nimmt ein Häkchen zurück (nur wenn Nachfolger noch nicht freigegeben)."""
        if step_key not in STEP_KEYS:
            raise ValueError(f"Unbekannter Step: {step_key}")

        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")

        idx = STEP_KEYS.index(step_key)
        # Prüfen ob Nachfolger schon freigegeben ist
        if idx < len(STEP_KEYS) - 1:
            next_key = STEP_KEYS[idx + 1]
            if txn["steps"][next_key].get("approved"):
                raise ValueError(
                    f"Kann nicht zurücknehmen: '{STEP_LABELS[next_key]}' ist bereits freigegeben."
                )

        step = txn["steps"][step_key]
        step["approved"] = False
        step["approved_at"] = None
        step["approved_by"] = None
        step["status"] = "IN_BEARBEITUNG"

        step["history"].append({
            "action": "FREIGABE_ZURUECKGENOMMEN",
            "user": user,
            "at": _ts(),
        })

        txn["status"] = "IN_BEARBEITUNG"
        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    # ── Angebot anpassen und stornieren (21.09.2026) ──
    #
    # „Der Vorgang Angebot an Kunde braucht zwei Ergänzungen: einmal anpassen
    #  und eine Stornierung, damit es nicht immer mitläuft."
    #
    # ANPASSEN erzeugt eine neue FASSUNG: die freigegebene Fassung wandert
    # unverändert nach step["versions"], die Belegnummer bekommt „-V2", „-V3" …
    # So bleibt belegbar, was der Kunde zuerst bekommen hat — ein bloßes
    # Zurücknehmen der Freigabe würde die erste Fassung überschreiben.
    #
    # STORNIEREN schließt standardmäßig den ganzen Vorgang (der Regelfall ist
    # die Absage des Kunden). Der Vorgang verschwindet damit aus den offenen
    # Vorgängen, bleibt vollständig erhalten und kann wieder geöffnet werden.

    # ── Positionen zwischen den Stufen (21.09.2026) ──
    #
    # „Bei Änderung des Angebots muss sich der Wert für Auftragseingang auch ändern"
    # und „bei Stornierung muss Auftragseingang gelöscht werden."
    #
    # Bis dahin wurden Positionen NUR weitergereicht, wenn die Folgestufe leer war.
    # Nach einer Anpassung blieb dort der alte Betrag stehen — der Vorgang zeigte
    # zwei verschiedene Summen. Jetzt gilt: übernommene Werte werden mitgeführt,
    # von Hand geänderte werden NICHT überschrieben, sondern gemeldet.

    @staticmethod
    def _positions_fingerprint(positions) -> str:
        roh = json.dumps(positions or [], sort_keys=True, default=str)
        return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]

    def _positionen_weiterreichen(self, step: dict, step_key: str,
                                  next_step: dict, next_key: str,
                                  user: str = "system") -> None:
        """Reicht Positionen an die Folgestufe weiter bzw. hält sie aktuell."""
        if not next_step or next_step.get("approved"):
            return                      # freigegebener Beleg wird nie still geändert

        quelle = next_step.get("positions_quelle") or {}
        vorhanden = next_step.get("positions") or []
        leer = not vorhanden
        unveraendert = (quelle.get("step") == step_key
                        and quelle.get("fingerprint") == self._positions_fingerprint(vorhanden))

        # Altbestand: vor dem 21.09.2026 wurde die Herkunft nicht vermerkt. Stimmen die
        # Positionen der Folgestufe mit dieser oder einer früheren Fassung der Vorstufe
        # überein, stammen sie von dort — dann dürfen sie mitgezogen werden. Sonst bliebe
        # in jedem Altvorgang der alte Betrag stehen (V-2026-0047: Angebot 2.975 €,
        # Auftragseingang noch 4.165 € vom 18.08.).
        if not leer and not unveraendert and not quelle:
            fp = self._positions_fingerprint(vorhanden)
            frueher = [v.get("positions") for v in (step.get("versions") or [])]
            frueher.append(step.get("positions"))
            unveraendert = any(p and self._positions_fingerprint(p) == fp for p in frueher)

        if not (leer or unveraendert):
            # Von Hand angepasst → nicht überschreiben, aber sichtbar machen.
            next_step["positions_abweichung"] = {
                "quelle": step_key,
                "seit": _ts(),
                "hinweis": f"{STEP_LABELS.get(step_key, step_key)} wurde geändert; "
                           f"die Positionen hier stammen aus einer eigenen Eingabe.",
            }
            next_step["history"].append({
                "action": "POSITIONEN_ABWEICHEND", "user": user, "at": _ts(),
                "comment": f"{STEP_LABELS.get(step_key, step_key)} geändert — "
                           f"eigene Positionen hier beibehalten",
            })
            return

        next_step["positions"] = copy.deepcopy(step.get("positions") or [])
        totals = calc_step_totals(next_step)
        next_step["amount"] = totals["total_gross"]
        next_step["positions_quelle"] = {
            "step": step_key,
            "fingerprint": self._positions_fingerprint(next_step["positions"]),
            "at": _ts(),
            "version": step.get("version", 1),
        }
        next_step.pop("positions_abweichung", None)
        next_step["history"].append({
            "action": "POSITIONEN_UEBERNOMMEN" if leer else "POSITIONEN_AKTUALISIERT",
            "user": user,
            "at": _ts(),
            "comment": (f"Positionen aus {step_key} übernommen" if leer else
                        f"Positionen aus {step_key} aktualisiert "
                        f"(Fassung {step.get('version', 1)}) — neuer Betrag "
                        f"{next_step['amount']:.2f}"),
        })

    def _folgestufen_leeren(self, txn: dict, step_key: str, user: str,
                            grund: str) -> list[str]:
        """Löscht übernommene Werte in allen nachfolgenden, nicht freigegebenen Stufen."""
        geleert = []
        idx = STEP_KEYS.index(step_key)
        for key in STEP_KEYS[idx + 1:]:
            nxt = txn["steps"].get(key) or {}
            if nxt.get("approved"):
                continue
            quelle = nxt.get("positions_quelle") or {}
            if not nxt.get("positions"):
                continue
            if not quelle:
                # Eigene Eingabe → stehen lassen, aber vermerken.
                nxt["history"].append({
                    "action": "EIGENE_POSITIONEN_BEHALTEN", "user": user, "at": _ts(),
                    "comment": f"{grund} — eigene Positionen hier nicht gelöscht",
                })
                continue
            nxt["positions"] = []
            nxt["amount"] = 0
            nxt["date"] = None
            nxt.pop("positions_quelle", None)
            nxt.pop("positions_abweichung", None)
            if nxt.get("status") in ("IN_BEARBEITUNG", "ERLEDIGT"):
                nxt["status"] = "OFFEN"
            nxt["history"].append({
                "action": "POSITIONEN_GELOESCHT", "user": user, "at": _ts(),
                "comment": grund,
            })
            geleert.append(STEP_LABELS.get(key, key))
        return geleert

    FORWARD_MAP = {
        "supplier_quote":     "purchase_order",
        "purchase_order":     "supplier_invoice",
        "customer_quote":     "order_intake",
        "order_intake":       "order_confirmation",
        "order_confirmation": "delivery_note",
        "delivery_note":      "invoice",
    }

    def sync_positions(self, txn_id: str, step_key: str, user: str = "system") -> dict:
        """Übernimmt die Positionen der Vorstufe ausdrücklich (überschreibt auch Eigenes)."""
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")
        quelle_key = next((v for v, n in self.FORWARD_MAP.items() if n == step_key), None)
        if not quelle_key:
            raise ValueError(f"Für '{STEP_LABELS.get(step_key, step_key)}' gibt es keine Vorstufe.")
        step = txn["steps"].get(step_key) or {}
        if step.get("approved"):
            raise ValueError("Stufe ist freigegeben — erst die Freigabe zurücknehmen.")
        vor = txn["steps"].get(quelle_key) or {}
        if not vor.get("positions"):
            raise ValueError(f"{STEP_LABELS.get(quelle_key, quelle_key)} hat keine Positionen.")

        step["positions"] = copy.deepcopy(vor["positions"])
        totals = calc_step_totals(step)
        step["amount"] = totals["total_gross"]
        step["positions_quelle"] = {
            "step": quelle_key, "fingerprint": self._positions_fingerprint(step["positions"]),
            "at": _ts(), "version": vor.get("version", 1),
        }
        step.pop("positions_abweichung", None)
        step["history"].append({
            "action": "POSITIONEN_UEBERNOMMEN_MANUELL", "user": user, "at": _ts(),
            "comment": f"Positionen aus {STEP_LABELS.get(quelle_key, quelle_key)} "
                       f"übernommen — neuer Betrag {step['amount']:.2f}",
        })
        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    def revise_step(self, txn_id: str, step_key: str,
                    user: str = "system", reason: str = "",
                    erzwingen: bool = False) -> dict:
        """Legt eine neue Fassung eines Steps an (Angebot anpassen)."""
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")
        step = txn["steps"].get(step_key)
        if step is None:
            raise ValueError(f"Stufe {step_key} unbekannt.")
        if step.get("status") == "STORNIERT":
            raise ValueError("Stornierte Stufe kann nicht angepasst werden — erst wieder öffnen.")

        # Nicht anpassen, wenn SPÄTER schon etwas freigegeben ist. Bis 22.09.2026 wurde
        # nur die unmittelbar folgende Stufe geprüft — war die übersprungen, ließ sich das
        # Angebot auch nach einer versendeten Auftragsbestätigung noch ändern. Genau das
        # ist am 21.09. passiert (V-2026-0047: AB um 13:21, Angebot V3 um 13:22).
        idx = STEP_KEYS.index(step_key)
        spaeter = [STEP_LABELS.get(k, k) for k in STEP_KEYS[idx + 1:]
                   if txn["steps"].get(k, {}).get("approved")]
        if spaeter and not erzwingen:
            raise SpaetereStufeFreigegeben(
                f"'{STEP_LABELS.get(step_key, step_key)}' anpassen, obwohl "
                + " und ".join(spaeter) + " bereits freigegeben "
                + ("ist" if len(spaeter) == 1 else "sind") + "?", spaeter)

        fassung = int(step.get("version") or 1)
        basis = step.get("reference_base") or step.get("reference") or ""

        # Alte Fassung sichern — ohne Historie und Fassungsliste, sonst wächst
        # der Datensatz bei jeder Anpassung quadratisch.
        archiv = {k: v for k, v in step.items() if k not in ("history", "versions")}
        archiv["fassung"] = fassung
        archiv["ersetzt_am"] = _ts()
        archiv["ersetzt_von"] = user
        archiv["ersetzt_grund"] = reason
        step.setdefault("versions", []).append(archiv)

        fassung += 1
        step["version"] = fassung
        if basis:
            step["reference_base"] = basis
            step["reference"] = f"{basis}-V{fassung}"
        step["approved"] = False
        step["approved_at"] = None
        step["approved_by"] = None
        step["status"] = "IN_BEARBEITUNG"
        step["date"] = _today()
        step["sent_at"] = None
        step["history"].append({
            "action": "ANGEPASST",
            "user": user,
            "at": _ts(),
            "reason": reason,
            "detail": f"Fassung {fassung - 1} → {fassung}"
                      + (f" — trotz Freigabe von {', '.join(spaeter)}" if spaeter else ""),
        })
        if spaeter:
            txn.setdefault("warnungen", []).append({
                "art": "angepasst_nach_freigabe", "at": _ts(), "user": user,
                "stufe": step_key, "betrifft": spaeter,
                "text": f"{STEP_LABELS.get(step_key, step_key)} wurde geändert, obwohl "
                        + ", ".join(spaeter) + " bereits freigegeben war.",
            })

        if txn.get("status") in ("ABGESCHLOSSEN", "STORNIERT"):
            txn["status"] = "IN_BEARBEITUNG"
        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    def cancel_step(self, txn_id: str, step_key: str, user: str = "system",
                    reason: str = "", close_transaction: bool = True) -> dict:
        """Storniert eine Stufe; schließt auf Wunsch den ganzen Vorgang."""
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")
        step = txn["steps"].get(step_key)
        if step is None:
            raise ValueError(f"Stufe {step_key} unbekannt.")

        # Was danach schon freigegeben ist, darf nicht stillschweigend entwertet werden.
        idx = STEP_KEYS.index(step_key)
        spaeter = [STEP_LABELS.get(k, k) for k in STEP_KEYS[idx + 1:]
                   if txn["steps"].get(k, {}).get("approved")]
        if spaeter:
            raise ValueError(
                "Kann nicht stornieren: " + ", ".join(spaeter) + " ist bereits freigegeben."
            )

        step["status"] = "STORNIERT"
        step["approved"] = False
        step["approved_at"] = None
        step["approved_by"] = None
        step["cancelled_at"] = _ts()
        step["cancelled_by"] = user
        step["cancel_reason"] = reason
        step["history"].append({
            "action": "STORNIERT",
            "user": user,
            "at": _ts(),
            "reason": reason,
        })

        # Was aus dieser Stufe weitergereicht wurde, darf nicht stehen bleiben —
        # sonst hinge am stornierten Angebot weiter ein Auftragseingang mit Betrag.
        geleert = self._folgestufen_leeren(
            txn, step_key, user,
            f"{STEP_LABELS.get(step_key, step_key)} storniert"
            + (f": {reason}" if reason else ""))
        if geleert:
            step["history"].append({
                "action": "FOLGESTUFEN_GELEERT", "user": user, "at": _ts(),
                "comment": "geleert: " + ", ".join(geleert),
            })

        if close_transaction:
            txn["status"] = "STORNIERT"
            txn["cancelled_at"] = _ts()
            txn["cancelled_by"] = user
            txn["cancel_reason"] = reason or f"{STEP_LABELS.get(step_key, step_key)} storniert"
        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    def reopen_transaction(self, txn_id: str, user: str = "system",
                           reason: str = "") -> dict:
        """Hebt die Stornierung auf — der Vorgang läuft wieder mit."""
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")
        if txn.get("status") != "STORNIERT":
            raise ValueError("Vorgang ist nicht storniert.")

        for key in STEP_KEYS:
            step = txn["steps"].get(key) or {}
            if step.get("status") == "STORNIERT":
                step["status"] = "IN_BEARBEITUNG" if (step.get("reference") or step.get("date")) else "OFFEN"
                step["reopened_at"] = _ts()
                step["history"].append({
                    "action": "STORNO_AUFGEHOBEN", "user": user, "at": _ts(), "reason": reason,
                })
        txn["status"] = "IN_BEARBEITUNG"
        txn["reopened_at"] = _ts()
        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    def skip_step(self, txn_id: str, step_key: str,
                  user: str = "system", reason: str = "") -> dict:
        """Überspringt einen Step."""
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")

        step = txn["steps"][step_key]
        step["status"] = "UEBERSPRUNGEN"
        step["comment"] = reason or "Übersprungen"
        step["history"].append({
            "action": "UEBERSPRUNGEN",
            "user": user,
            "at": _ts(),
            "reason": reason,
        })

        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    # ── Teillieferungen (Step 6) ──

    def add_delivery(self, txn_id: str, delivery_data: dict,
                     user: str = "system") -> dict:
        """Fügt eine Teillieferung zum Lieferschein-Step hinzu."""
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")

        step = txn["steps"]["delivery_note"]
        if "deliveries" not in step:
            step["deliveries"] = []

        delivery_ref = self.numbers.next("LIEF")
        delivery = {
            "id": delivery_ref,
            "date": delivery_data.get("date", _today()),
            "positions": delivery_data.get("positions", []),
            "document_id": delivery_data.get("document_id"),
            "notes": delivery_data.get("notes", ""),
            "approved": False,
            "approved_at": None,
            "approved_by": None,
        }
        step["deliveries"].append(delivery)
        step["status"] = "IN_BEARBEITUNG"

        step["history"].append({
            "action": "TEILLIEFERUNG_HINZUGEFUEGT",
            "user": user,
            "at": _ts(),
            "delivery_id": delivery_ref,
        })

        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    def approve_delivery(self, txn_id: str, delivery_id: str,
                         user: str = "system") -> dict:
        """Gibt eine einzelne Teillieferung frei."""
        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")

        step = txn["steps"]["delivery_note"]
        for d in step.get("deliveries", []):
            if d["id"] == delivery_id:
                d["approved"] = True
                d["approved_at"] = _ts()
                d["approved_by"] = user
                break
        else:
            raise ValueError(f"Lieferung {delivery_id} nicht gefunden.")

        # Gesamt-Step prüfen
        if all(d.get("approved") for d in step.get("deliveries", [])):
            step["approved"] = True
            step["approved_at"] = _ts()
            step["approved_by"] = user
            step["status"] = "ERLEDIGT"

        txn["updated_at"] = _ts()
        self._save(txns)
        return txn

    # ── Timeline ──

    # ── Abschlagsrechnungen (Anzahlungen / Teilrechnungen) ──────────────

    def list_payment_invoices(self, txn_id: str) -> list[dict]:
        txn = self.get(txn_id)
        if not txn:
            return []
        return list(txn.get("payment_invoices", []))

    def add_payment_invoice(self, txn_id: str, data: dict, user: str = "system") -> dict:
        txns = self._load()
        for i, t in enumerate(txns):
            if t.get("id") != txn_id:
                continue
            payments = t.setdefault("payment_invoices", [])
            position = len(payments) + 1
            ref = self.numbers.next("AR")
            p = _empty_payment_invoice(position, ref)
            if data.get("title"): p["title"] = str(data["title"]).strip()
            if data.get("date"):  p["date"] = str(data["date"])
            if "amount_net" in data:
                p["amount_net"] = float(data.get("amount_net") or 0)
            if "tax_rate" in data:
                p["tax_rate"] = _satz(data.get("tax_rate"))
            if "description" in data:
                p["description"] = str(data.get("description") or "")
            _recalc_payment_invoice_gross(p)
            payments.append(p)
            t["updated_at"] = _ts()
            txns[i] = t
            self._save(txns)
            return p
        raise ValueError(f"Vorgang {txn_id} nicht gefunden")

    def update_payment_invoice(self, txn_id: str, pid: str, data: dict, user: str = "system") -> dict:
        txns = self._load()
        for i, t in enumerate(txns):
            if t.get("id") != txn_id:
                continue
            payments = t.get("payment_invoices", [])
            for j, p in enumerate(payments):
                if p.get("id") != pid:
                    continue
                if p.get("status") == "PAID":
                    # Bezahlte Abschläge bleiben fix (außer Notes/document_id)
                    changed = False
                    if "notes" in data:
                        p["notes"] = str(data.get("notes") or ""); changed = True
                    if "document_id" in data:
                        p["document_id"] = data["document_id"]; changed = True
                    if changed:
                        t["updated_at"] = _ts(); txns[i] = t; self._save(txns)
                        return p
                    raise ValueError("Abschlag ist bezahlt — nur Notiz / PDF-Link änderbar")
                for key in ("title", "date", "description", "notes", "document_id"):
                    if key in data:
                        p[key] = str(data[key]).strip() if isinstance(data[key], str) else data[key]
                if "amount_net" in data:
                    p["amount_net"] = float(data.get("amount_net") or 0)
                if "tax_rate" in data:
                    p["tax_rate"] = _satz(data.get("tax_rate"))
                _recalc_payment_invoice_gross(p)
                p.setdefault("history", []).append({"action": "GEAENDERT", "at": _ts(), "user": user})
                t["updated_at"] = _ts()
                txns[i] = t
                self._save(txns)
                return p
            raise ValueError(f"Abschlag {pid} nicht gefunden")
        raise ValueError(f"Vorgang {txn_id} nicht gefunden")

    def delete_payment_invoice(self, txn_id: str, pid: str) -> bool:
        txns = self._load()
        for i, t in enumerate(txns):
            if t.get("id") != txn_id:
                continue
            payments = t.get("payment_invoices", [])
            before = len(payments)
            payments = [p for p in payments if p.get("id") != pid]
            # Bezahlte Abschläge nicht löschen
            for p in t.get("payment_invoices", []):
                if p.get("id") == pid and p.get("status") == "PAID":
                    raise ValueError("Bezahlter Abschlag kann nicht gelöscht werden")
            if len(payments) < before:
                # Positionen neu nummerieren
                for idx, p in enumerate(payments, 1):
                    p["position"] = idx
                t["payment_invoices"] = payments
                t["updated_at"] = _ts()
                txns[i] = t
                self._save(txns)
                return True
            return False
        return False

    def mark_payment_invoice_sent(self, txn_id: str, pid: str, user: str = "system",
                                    document_id: str = "", to: str = "",
                                    channel: str = "manuell", message_id: str = "",
                                    note: str = "", invoice_id: str = "",
                                    invoice_number: str = "") -> dict:
        """Setzt den Abschlag auf SENT und haelt fest, WIE er hinausging.

        `channel` 'email' = ueber den Mailweg des Systems (mit Message-ID im
        Versandprotokoll), 'manuell' = ausserhalb verschickt; dann traegt `note`
        oder `to`, worauf sich das stuetzt. Ohne diese Angaben war der Status
        eine Behauptung ohne Beleg.
        """
        return self._set_payment_status(txn_id, pid, "SENT", {
            "sent_at": _ts(), "document_id": document_id,
            "sent_to": to, "sent_channel": channel, "sent_message_id": message_id,
            "sent_note": note, "invoice_id": invoice_id, "invoice_number": invoice_number,
        }, user)

    def mark_payment_invoice_paid(self, txn_id: str, pid: str, user: str = "system",
                                    paid_at: str = "", paid_amount: float = None,
                                    booking_id: str = "") -> dict:
        extra = {"paid_at": paid_at or _today(), "paid_via_booking": booking_id}
        if paid_amount is not None:
            extra["paid_amount"] = float(paid_amount)
        return self._set_payment_status(txn_id, pid, "PAID", extra, user)

    def _set_payment_status(self, txn_id: str, pid: str, new_status: str,
                              extra: dict, user: str) -> dict:
        txns = self._load()
        for i, t in enumerate(txns):
            if t.get("id") != txn_id:
                continue
            for p in t.get("payment_invoices", []):
                if p.get("id") != pid:
                    continue
                old = p.get("status")
                p["status"] = new_status
                for k, v in (extra or {}).items():
                    if v not in (None, ""):
                        p[k] = v
                p.setdefault("history", []).append({
                    "action": f"STATUS_{new_status}", "at": _ts(), "user": user,
                    "detail": f"{old} → {new_status}",
                })
                t["updated_at"] = _ts()
                txns[i] = t
                self._save(txns)
                return p
            raise ValueError(f"Abschlag {pid} nicht gefunden")
        raise ValueError(f"Vorgang {txn_id} nicht gefunden")

    def get_timeline(self, txn_id: str) -> list[dict]:
        """Gibt alle Ereignisse chronologisch sortiert zurück."""
        txn = self.get(txn_id)
        if not txn:
            return []

        events = []
        events.append({
            "at": txn["created_at"],
            "action": "VORGANG_ERSTELLT",
            "step": None,
            "user": "system",
            "detail": txn.get("subject", ""),
        })

        for key in STEP_KEYS:
            step = txn["steps"].get(key, {})
            label = STEP_LABELS.get(key, key)
            ref = step.get("reference", "")
            doc_id = step.get("document_id", "")
            for h in step.get("history", []):
                ev = {
                    "at": h.get("at", ""),
                    "action": h.get("action", ""),
                    "step": label,
                    "step_key": key,
                    "user": h.get("user", ""),
                    "detail": h.get("detail", h.get("comment", h.get("reason", ""))),
                }
                # Dokument-Referenz mitgeben — falls ein PDF erzeugt/versendet wurde,
                # kann das Frontend einen Link zum Lesen/Drucken anbieten.
                action = (h.get("action") or "").upper()
                if doc_id and any(t in action for t in ("PDF", "GESENDET", "GENERIERT", "FREIGEGEBEN_UND_GESENDET")):
                    ev["document_id"] = doc_id
                    ev["reference"] = ref
                events.append(ev)

        events.sort(key=lambda e: e.get("at", ""))
        return events

    # ── Statistiken ──

    def stats(self) -> dict:
        txns = self._load()
        return {
            "total": len(txns),
            "neu": sum(1 for t in txns if t["status"] == "NEU"),
            "in_bearbeitung": sum(1 for t in txns if t["status"] == "IN_BEARBEITUNG"),
            "abgeschlossen": sum(1 for t in txns if t["status"] == "ABGESCHLOSSEN"),
            "storniert": sum(1 for t in txns if t["status"] == "STORNIERT"),
        }
