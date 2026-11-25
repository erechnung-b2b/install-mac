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


# Rechnungen in diesen Zuständen werden nie gemahnt (25.11.2026: ZURUECKGEWIESEN ergänzt)
ERLEDIGT_STATUS = ("BEZAHLT", "STORNIERT", "GUTGESCHRIEBEN", "ZURUECKGEWIESEN")


def _stufe(eintrag) -> int:
    try:
        return int(eintrag.get("level", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _fruehestens(history, levels, last_level, folge):
    """Frühester Tag für die Folgestufe: Datum der letzten Mahnung + Abstand der beiden Schwellen.
    None, wenn noch nie gemahnt wurde oder das Datum der letzten Mahnung fehlt."""
    if not last_level:
        return None
    daten = []
    for h in history:
        if _stufe(h) == last_level and h.get("date"):
            try:
                daten.append(date.fromisoformat(str(h["date"])[:10]))
            except (ValueError, TypeError):
                pass
    if not daten:
        return None
    vorher = next((lv for lv in levels if lv["level"] == last_level), None)
    try:
        abstand = int(folge["days_after_due"]) - int(vorher["days_after_due"]) if vorher else 0
    except (KeyError, TypeError, ValueError):
        abstand = 0
    if abstand <= 0:
        return None
    return max(daten) + timedelta(days=abstand)


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
            # Erledigt (bezahlt, voll gutgeschrieben, storniert, zurückgewiesen) → überspringen
            status = inv.get("status", "")
            if status in ERLEDIGT_STATUS:
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
            history = [h for h in (inv.get("dunning_history") or []) if isinstance(h, dict)]
            last_level = max((_stufe(h) for h in history), default=0)

            # Nächste Stufe bestimmen — immer nur die UNMITTELBAR FOLGENDE nach der zuletzt erzeugten (25.11.2026).
            # Bis dahin lief die Schleife ohne Abbruch bis zur höchsten erreichten Stufe: 45 Tage überfällig und noch
            # nie gemahnt ergab sofort Stufe 3 „Letzte Aufforderung“ mit Gebühr, ohne Erinnerung und 1. Mahnung.
            next_level = None
            next_fee = 0
            next_subject = ""
            wartet_bis = ""
            folge = next((lv for lv in levels if lv["level"] > last_level), None)
            if folge is not None and days_overdue >= folge["days_after_due"]:
                # Abstand zur letzten Mahnung: so viele Tage, wie die Schwellen der beiden Stufen auseinanderliegen
                # (Standard 14) — sonst stünde am Tag nach der Erinnerung schon die 1. Mahnung an.
                frueh = _fruehestens(history, levels, last_level, folge)
                if frueh is not None and today < frueh:
                    wartet_bis = frueh.isoformat()
                else:
                    next_level = folge["level"]
                    next_fee = folge.get("fee", 0)
                    next_subject = folge.get("subject", f"Mahnung Stufe {folge['level']}")

            results.append({
                "transaction_id": inv.get("transaction_id", ""),
                "invoice_id": inv.get("invoice_id", ""),          # gesetzt bei Rechnungen ohne Vorgang
                "invoice_reference": inv.get("invoice_reference", ""),
                "buyer_name": inv.get("buyer_name", ""),
                "amount": inv.get("amount", 0),                   # offener Betrag (nach Zahlungen und Gutschriften)
                "wartet_bis": wartet_bis,                         # nächste Stufe frühestens an diesem Tag
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

    def collect_invoices_from_transactions(self, transactions: list[dict],
                                           status_fn=None, offen_fn=None) -> list[dict]:
        """
        Extrahiert Rechnungsdaten aus Vorgängen für den Mahncheck.
        Nur Vorgänge berücksichtigen, bei denen Stufe 7 (invoice) freigegeben ist.

        status_fn(rechnungsnummer) liefert den Status der registrierten Rechnung
        (BEZAHLT / STORNIERT / …) oder "" — maßgeblich ist der Zahlstatus der
        Rechnung, nicht die Mahnstufe des Vorgangs. Ohne status_fn wie bisher.

        offen_fn(rechnungsnummer) liefert den OFFENEN Betrag der registrierten Rechnung (nach Teilzahlungen,
        Gutschriften, Skonto) oder None, wenn die Rechnung nicht im Register steht — dann gilt wie bisher der
        Betrag der Vorgangsstufe (25.11.2026).
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
            if not status and status_fn is not None:
                try:
                    echt = str(status_fn(inv_step.get("reference", "")) or "")
                except Exception:
                    echt = ""
                if echt in ERLEDIGT_STATUS:
                    status = echt

            betrag = inv_step.get("amount", 0)
            if offen_fn is not None:
                try:
                    offen = offen_fn(inv_step.get("reference", ""))
                except Exception:
                    offen = None
                if offen is not None:
                    betrag = offen

            invoices.append({
                "transaction_id": txn["id"],
                "invoice_reference": inv_step.get("reference", ""),
                "invoice_date": inv_step.get("date", ""),
                "due_date": due,
                "amount": betrag,
                "buyer_name": txn.get("buyer_name", ""),
                "status": status,
                "dunning_history": dunning_history,
            })

        return invoices

    def get_overdue_summary(self, transactions: list[dict], status_fn=None,
                            offen_fn=None, weitere=None) -> dict:
        """Kompakt-Zusammenfassung für Dashboard.

        weitere: zusätzliche Rechnungen im selben Aufbau wie collect_invoices_from_transactions — versendete
        Ausgangsrechnungen OHNE Vorgang („Rechnung erstellen“, CSV-Import), mit invoice_id statt transaction_id."""
        invoices = self.collect_invoices_from_transactions(transactions, status_fn, offen_fn)
        invoices += list(weitere or [])
        overdue = self.check_overdue(invoices)

        needs_action = [o for o in overdue if o["needs_action"]]
        total_overdue_amount = sum(o["amount"] for o in overdue if o["days_overdue"] > 0)

        return {
            "total_overdue": len(overdue),
            "needs_action": len(needs_action),
            "total_overdue_amount": round(total_overdue_amount, 2),
            "items": overdue,
        }
