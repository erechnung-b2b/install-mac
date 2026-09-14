"""
E-Rechnungssystem – Mahnwesen (Phase 3)
Konfigurierbare Mahnregeln, automatische Fristprüfung,
Mahnstufen-Verwaltung mit Verknüpfung zu Vorgängen.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional


# ── Standard-Mahnregeln ──────────────────────────────────────────────

DEFAULT_RULES = {
    "grace_days": 14,
    "levels": [
        {"level": 1, "days_after_due": 14, "fee": 0.0,
         "subject": "Zahlungserinnerung"},
        {"level": 2, "days_after_due": 28, "fee": 5.0,
         "subject": "1. Mahnung"},
        {"level": 3, "days_after_due": 42, "fee": 10.0,
         "subject": "2. Mahnung – Letzte Aufforderung"},
    ]
}


def _today() -> date:
    return date.today()


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── DunningManager ───────────────────────────────────────────────────

class DunningManager:
    """Verwaltet Mahnregeln und prüft überfällige Rechnungen."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.rules_path = self.data_dir / "dunning_rules.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ── Regeln ──

    def get_rules(self) -> dict:
        if self.rules_path.exists():
            try:
                return json.loads(self.rules_path.read_text("utf-8"))
            except Exception:
                pass
        return DEFAULT_RULES.copy()

    def save_rules(self, rules: dict):
        self.rules_path.write_text(
            json.dumps(rules, indent=2, ensure_ascii=False), "utf-8"
        )

    # ── Frist-Prüfung ──

    def check_overdue(self, invoices: list[dict]) -> list[dict]:
        """
        Prüft eine Liste von Rechnungen/Vorgängen auf Fälligkeit.
        
        Erwartet dicts mit:
          - transaction_id
          - invoice_reference (Rechnungsnummer)
          - invoice_date
          - due_date (Fälligkeitsdatum)
          - amount (offener Betrag)
          - buyer_name
          - status (BEZAHLT → wird übersprungen)
          - dunning_history: [{"level":1,"date":"..."}, ...]
        
        Gibt eine Liste von Mahnvorschlägen zurück.
        """
        rules = self.get_rules()
        levels = sorted(rules.get("levels", []), key=lambda l: l["level"])
        today = _today()
        results = []

        for inv in invoices:
            # Bereits bezahlt oder storniert → überspringen
            status = inv.get("status", "")
            if status in ("BEZAHLT", "STORNIERT", "GUTGESCHRIEBEN"):
                continue

            due_str = inv.get("due_date", "")
            if not due_str:
                continue

            try:
                due = date.fromisoformat(due_str[:10])
            except (ValueError, TypeError):
                continue

            days_overdue = (today - due).days
            if days_overdue <= 0:
                continue

            # Welche Mahnstufe wurde zuletzt erzeugt?
            history = inv.get("dunning_history", [])
            last_level = max((h.get("level", 0) for h in history), default=0)

            # Nächste fällige Stufe bestimmen
            next_level = None
            next_fee = 0
            next_subject = ""
            for lv in levels:
                if lv["level"] > last_level and days_overdue >= lv["days_after_due"]:
                    next_level = lv["level"]
                    next_fee = lv.get("fee", 0)
                    next_subject = lv.get("subject", f"Mahnung Stufe {lv['level']}")

            if next_level is None and last_level == 0:
                # Noch keine Mahnung, aber überfällig → Stufe 1 vorschlagen
                # wenn die Tage die erste Schwelle noch nicht erreichen,
                # trotzdem als "bald fällig" markieren
                for lv in levels:
                    if lv["level"] == 1:
                        if days_overdue >= lv["days_after_due"]:
                            next_level = 1
                            next_fee = lv.get("fee", 0)
                            next_subject = lv.get("subject", "Zahlungserinnerung")
                        break

            results.append({
                "transaction_id": inv.get("transaction_id", ""),
                "invoice_reference": inv.get("invoice_reference", ""),
                "buyer_name": inv.get("buyer_name", ""),
                "amount": inv.get("amount", 0),
                "due_date": due_str,
                "days_overdue": days_overdue,
                "last_dunning_level": last_level,
                "next_dunning_level": next_level,
                "next_fee": next_fee,
                "next_subject": next_subject,
                "needs_action": next_level is not None,
            })

        # Sortieren: dringendste zuerst
        results.sort(key=lambda r: (-r["days_overdue"], -r.get("next_dunning_level") if r.get("next_dunning_level") else 0))
        return results

    def collect_invoices_from_transactions(self, transactions: list[dict]) -> list[dict]:
        """
        Extrahiert Rechnungsdaten aus Vorgängen für den Mahncheck.
        Nur Vorgänge berücksichtigen, bei denen Stufe 7 (invoice) freigegeben ist.
        """
        invoices = []
        for txn in transactions:
            inv_step = txn.get("steps", {}).get("invoice", {})
            if not inv_step.get("approved"):
                continue  # Nur freigegebene Rechnungen

            dunning_step = txn.get("steps", {}).get("dunning", {})
            dunning_history = dunning_step.get("levels", [])

            # Fälligkeitsdatum: aus invoice step oder 30 Tage nach Rechnungsdatum
            due = inv_step.get("due_date", "")
            if not due and inv_step.get("date"):
                try:
                    inv_date = date.fromisoformat(inv_step["date"][:10])
                    due = (inv_date + timedelta(days=30)).isoformat()
                except (ValueError, TypeError):
                    pass

            # Status: BEZAHLT wenn Mahnstufe "ERLEDIGT"
            status = ""
            if dunning_step.get("status") == "ERLEDIGT" and dunning_step.get("approved"):
                status = "BEZAHLT"

            invoices.append({
                "transaction_id": txn["id"],
                "invoice_reference": inv_step.get("reference", ""),
                "invoice_date": inv_step.get("date", ""),
                "due_date": due,
                "amount": inv_step.get("amount", 0),
                "buyer_name": txn.get("buyer_name", ""),
                "status": status,
                "dunning_history": dunning_history,
            })

        return invoices

    def get_overdue_summary(self, transactions: list[dict]) -> dict:
        """Kompakt-Zusammenfassung für Dashboard."""
        invoices = self.collect_invoices_from_transactions(transactions)
        overdue = self.check_overdue(invoices)

        needs_action = [o for o in overdue if o["needs_action"]]
        total_overdue_amount = sum(o["amount"] for o in overdue if o["days_overdue"] > 0)

        return {
            "total_overdue": len(overdue),
            "needs_action": len(needs_action),
            "total_overdue_amount": round(total_overdue_amount, 2),
            "items": overdue,
        }
