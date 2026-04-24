"""
E-Rechnungssystem – Auftragsmanagement / Vorgänge
8-Stufen-Workflow: Lieferant → Angebot einholen → Bestellung → Kundenangebot
→ Auftragseingang → Lieferschein → E-Rechnung → Mahnung.
Jede Stufe: Datum, Häkchen-Freigabe, eigene Positionen, Audit-Trail.
"""
from __future__ import annotations
import json, uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional


# ── Konstanten ───────────────────────────────────────────────────────

STEP_KEYS = [
    "supplier_quote",    # Angebot Lieferant
    "purchase_order",    # Bestellung an Lieferant
    "supplier_invoice",  # Eingangsrechnung Lieferant
    "customer_quote",    # Angebot an Kunde
    "order_intake",      # Auftragseingang
    "delivery_note",     # Lieferschein
    "invoice",           # E-Rechnung
    "dunning",           # Mahnung
]

STEP_LABELS = {
    "supplier_quote":   "Angebot Lieferant",
    "purchase_order":   "Bestellung",
    "supplier_invoice": "Eingangsrechnung",
    "customer_quote":   "Angebot Kunde",
    "order_intake":     "Auftragseingang",
    "delivery_note":    "Lieferschein",
    "invoice":          "E-Rechnung",
    "dunning":          "Mahnung",
}

DOC_PREFIXES = {
    "supplier_quote":   "ANG-L",
    "purchase_order":   "BEST",
    "supplier_invoice": "ER",
    "customer_quote":   "ANG-K",
    "order_intake":     "AUFT",
    "delivery_note":    "LIEF",
    "invoice":          "RE",
    "dunning":          "MAH",
}


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
                     "buyer_name", "notes", "status"):
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
        """Gibt den Key des nächsten offenen Steps zurück."""
        for key in STEP_KEYS:
            step = txn["steps"].get(key, {})
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
                     "payment_terms", "attachments"):
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
        """Prüft ob ein Step freigegeben werden kann."""
        idx = STEP_KEYS.index(step_key)
        if idx == 0:
            return True, ""

        prev_key = STEP_KEYS[idx - 1]
        prev = txn["steps"].get(prev_key, {})
        if prev.get("approved") or prev.get("status") == "UEBERSPRUNGEN":
            return True, ""

        return False, f"Vorheriger Schritt '{STEP_LABELS[prev_key]}' muss zuerst freigegeben werden."

    def approve_step(self, txn_id: str, step_key: str,
                     user: str = "system", comment: str = "") -> dict:
        """Setzt das Häkchen für einen Step."""
        if step_key not in STEP_KEYS:
            raise ValueError(f"Unbekannter Step: {step_key}")

        txns = self._load()
        txn = self._find(txns, txn_id)
        if not txn:
            raise ValueError(f"Vorgang {txn_id} nicht gefunden.")

        ok, reason = self.can_approve(txn, step_key)
        if not ok:
            raise ValueError(reason)

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
            prefix = DOC_PREFIXES.get(step_key, "DOK")
            step["reference"] = self.numbers.next(prefix)

        # ── Positionen an nächsten Step weiterreichen ──
        # Wenn der nächste Step noch keine Positionen hat, kopieren
        FORWARD_MAP = {
            "supplier_quote": "purchase_order",     # Angebotsanfrage → Bestellung
            "purchase_order": "supplier_invoice",   # Bestellung → Eingangsrechnung
            "customer_quote": "order_intake",       # Kundenangebot → Auftragseingang
            "order_intake": "delivery_note",        # Auftragseingang → Lieferschein
            "delivery_note": "invoice",             # Lieferschein → Rechnung
        }
        next_key = FORWARD_MAP.get(step_key)
        if next_key and step.get("positions"):
            next_step = txn["steps"].get(next_key, {})
            if not next_step.get("positions"):
                import copy
                next_step["positions"] = copy.deepcopy(step["positions"])
                next_step["history"].append({
                    "action": "POSITIONEN_UEBERNOMMEN",
                    "user": "system",
                    "at": _ts(),
                    "comment": f"Positionen aus {step_key} übernommen",
                })
                # Betrag berechnen
                totals = calc_step_totals(next_step)
                next_step["amount"] = totals["total_gross"]

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
            for h in step.get("history", []):
                events.append({
                    "at": h.get("at", ""),
                    "action": h.get("action", ""),
                    "step": label,
                    "step_key": key,
                    "user": h.get("user", ""),
                    "detail": h.get("detail", h.get("comment", h.get("reason", ""))),
                })

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
