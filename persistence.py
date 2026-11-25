"""
E-Rechnungssystem – Persistenz
Speichert und laedt alle Rechnungsdaten als JSON auf der Festplatte.
Wird beim Beenden automatisch gespeichert und beim Start geladen.
"""
from __future__ import annotations
import json
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from models import (
    Invoice, Seller, Buyer, Address, Contact,
    PaymentInfo, InvoiceLine, AllowanceCharge, AuditEvent,
)



_SPEICHER_SPERRE = threading.RLock()


def _atomar_schreiben(ziel, daten):
    """JSON vollständig in eine eigene Zwischendatei (Prozess + Thread im Namen) schreiben, auf Platte bringen,
    dann atomar über das Ziel legen. Leser sehen immer die alte oder die neue, nie eine halbe Datei."""
    tmp = f"{ziel}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(ziel))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

class InvoiceStore:
    """Speichert und laedt Rechnungen als JSON-Dateien."""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.invoices_file = self.data_dir / "invoices.json"
        self.state_file = self.data_dir / "state.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ── Serialisierung ──

    def _serialize_invoice(self, inv: Invoice) -> dict:
        """Konvertiert ein Invoice-Objekt in ein JSON-faehiges dict."""
        return {
            "_id": inv._id,
            "_direction": inv._direction,
            # Alt-Ausgangsrechnungen aus der Zeit vor e-rechnung: im Register, damit
            # Zahlungen zugeordnet werden koennen — aber aus den laufenden Auswertungen
            # heraushaltbar (01.09.2026).
            "_altbestand": bool(getattr(inv, "_altbestand", False)),
            "_source_file": inv._source_file,
            "_source_format": inv._source_format,
            "_received_at": inv._received_at,
            "_sender_email": inv._sender_email,
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "invoice_type_code": inv.invoice_type_code,
            "currency_code": inv.currency_code,
            "buyer_reference": inv.buyer_reference,
            "order_reference": inv.order_reference,
            "contract_reference": inv.contract_reference,
            "project_reference": inv.project_reference,
            "preceding_invoice": inv.preceding_invoice,
            "preceding_invoice_date": (inv.preceding_invoice_date.isoformat()
                                       if getattr(inv, "preceding_invoice_date", None) else None),
            "note": inv.note,
            "tax_point_date": inv.tax_point_date.isoformat() if inv.tax_point_date else None,
            "period_start": inv.period_start.isoformat() if inv.period_start else None,
            "period_end": inv.period_end.isoformat() if inv.period_end else None,
            # 25.11.2026: BT-113 Vorauszahlung wurde geladen, aber nie gespeichert — nach dem nächsten Laden
            # forderte eine Schlussrechnung wieder den vollen Betrag.
            "prepaid_amount": str(getattr(inv, "prepaid_amount", None) or "0.00"),
            "prepaid_details": list(getattr(inv, "prepaid_details", None) or []),
            # Steuerfall, Befreiungsgrund BT-120, Code BT-121 (fehlt im Altbestand = „regel“)
            "steuerfall": getattr(inv, "steuerfall", "regel") or "regel",
            "steuerbefreiung_grund": getattr(inv, "steuerbefreiung_grund", "") or "",
            "steuerbefreiung_code": getattr(inv, "steuerbefreiung_code", "") or "",
            # Zusatzangaben (Lieferort, Zahlungsempfänger, weitere Bezüge, Anhänge als Verweis auf die Datei im
            # Archivordner, Steuerwährung, Rundung); None bei Belegen ohne solche Angaben
            "zusatz": __import__("rechnungszusatz").speichern(inv, self.data_dir),
            # Summen aus der eingelesenen Datei (BT-106 … BT-115), nur bei Eingangsbelegen
            "_datei_summen": ({k: str(v) for k, v in getattr(inv, "_datei_summen").items()}
                              if isinstance(getattr(inv, "_datei_summen", None), dict) and getattr(inv, "_datei_summen")
                              else None),
            "status": inv.status,
            "assigned_to": inv.assigned_to,
            "_paid_at": getattr(inv, '_paid_at', None),
            "_paid_amount": getattr(inv, '_paid_amount', None),
            "_paid_via_booking": getattr(inv, '_paid_via_booking', None),
            "_skonto_zahlung": getattr(inv, '_skonto_zahlung', None),
            # Teilzahlungen (25.11.2026): Zahlungen, die den Rechnungsbetrag noch nicht decken — je Bankbuchung ein Eintrag
            # {buchung, betrag, datum}. Solange die Rechnung nicht gedeckt ist, bleibt _paid_at leer und der Status offen.
            "_teilzahlungen": getattr(inv, '_teilzahlungen', None),
            # 25.11.2026 (Prüfung Punkte 6, 8, 10):
            # _mahnungen: erzeugte Mahnschreiben zu einer Rechnung OHNE Vorgang — [{level, date, reference, betrag, gebuehr, dokument, von}]
            # _kontierung: gespeicherte Kontierung {account, cost_center, project} — das Sachkonto geht als Gegenkonto in den DATEV-Export
            # _bezahlt_manuell: „Als bezahlt markieren“ von Hand — merkt den vorherigen Status für die Rücknahme
            "_mahnungen": getattr(inv, '_mahnungen', None),
            "_kontierung": getattr(inv, '_kontierung', None),
            "_bezahlt_manuell": getattr(inv, '_bezahlt_manuell', None),
            # Dauerrechnung Online-Zugang (11.10.2026): Bezug zum Abo der Haupt-App + die Monatszahlungen nach der ersten
            "_nutzerabo": getattr(inv, '_nutzerabo', None),
            "_kosit": getattr(inv, '_kosit', None),      # Ergebnis der amtlichen Prüfung (KoSIT), 24.11.2026
            "_dauer_zahlungen": getattr(inv, '_dauer_zahlungen', None),
            "seller": {
                "name": inv.seller.name,
                "street": inv.seller.address.street,
                "house_number": inv.seller.address.house_number,
                "city": inv.seller.address.city,
                "post_code": inv.seller.address.post_code,
                "country_code": inv.seller.address.country_code,
                "address_line_2": inv.seller.address.address_line_2,
                "electronic_address": inv.seller.electronic_address,
                "electronic_address_scheme": inv.seller.electronic_address_scheme,
                "vat_id": inv.seller.vat_id,
                "tax_registration_id": inv.seller.tax_registration_id,
                "contact_name": inv.seller.contact.name,
                "contact_phone": inv.seller.contact.telephone,
                "contact_email": inv.seller.contact.email,
                "registration_name": getattr(inv.seller, "registration_name", "") or "",
                "company_id": getattr(inv.seller, "company_id", "") or "",        # BT-30
                "seller_id": getattr(inv.seller, "seller_id", "") or "",          # BT-29
                "legal_info": getattr(inv.seller, "legal_info", "") or "",        # BT-33
            },
            "buyer": {
                "name": inv.buyer.name,
                "street": inv.buyer.address.street,
                "house_number": inv.buyer.address.house_number,
                "city": inv.buyer.address.city,
                "post_code": inv.buyer.address.post_code,
                "country_code": inv.buyer.address.country_code,
                "address_line_2": inv.buyer.address.address_line_2,
                "electronic_address": inv.buyer.electronic_address,
                "electronic_address_scheme": inv.buyer.electronic_address_scheme,
                "vat_id": inv.buyer.vat_id,
                "buyer_reference": inv.buyer.buyer_reference,
                "company_id": getattr(inv.buyer, "company_id", "") or "",         # BT-47
                "buyer_id": getattr(inv.buyer, "buyer_id", "") or "",             # BT-46
                "contact_name": (inv.buyer.contact.name if getattr(inv.buyer, "contact", None) else ""),       # BT-56
                "contact_phone": (inv.buyer.contact.telephone if getattr(inv.buyer, "contact", None) else ""), # BT-57
                "contact_email": (inv.buyer.contact.email if getattr(inv.buyer, "contact", None) else ""),     # BT-58
            },
            "payment": {
                "means_code": inv.payment.means_code,
                "iban": inv.payment.iban,
                "bic": inv.payment.bic,
                "bank_name": inv.payment.bank_name,
                "due_date": inv.payment.due_date.isoformat() if inv.payment.due_date else None,
                "payment_terms": inv.payment.payment_terms,
                "mandate_reference": inv.payment.mandate_reference,
                "creditor_id": inv.payment.creditor_id,
                "debited_account": inv.payment.debited_account,
                "skonto_percent": getattr(inv.payment, "skonto_percent", 0) or 0,
                "skonto_days": getattr(inv.payment, "skonto_days", 0) or 0,
                "skonto2_percent": getattr(inv.payment, "skonto2_percent", 0) or 0,
                "skonto2_days": getattr(inv.payment, "skonto2_days", 0) or 0,
                "remittance": getattr(inv.payment, "remittance", "") or "",       # BT-83
            },
            "lines": [{
                "line_id": l.line_id,
                "quantity": str(l.quantity),
                "unit_code": l.unit_code,
                "line_net_amount": str(l.line_net_amount),
                "item_name": l.item_name,
                "item_description": l.item_description,
                "unit_price": str(l.unit_price),
                "price_base_quantity": str(l.price_base_quantity) if l.price_base_quantity else None,
                "tax_category": l.tax_category,
                "tax_rate": str(l.tax_rate),
                "item_id": l.item_id,
                "period_start": l.period_start.isoformat() if l.period_start else None,
                "period_end": l.period_end.isoformat() if l.period_end else None,
                "note": getattr(l, "note", "") or "",                             # BT-127
                "order_reference": getattr(l, "order_reference", "") or "",       # BT-132
                # BT-136 … BT-145: ohne sie passt der Positionsbetrag nach dem Laden nicht mehr zu Menge × Preis (BR-CO-10)
                "allowances_charges": [{
                    "is_charge": bool(a.is_charge),
                    "amount": str(a.amount),
                    "base_amount": str(a.base_amount) if a.base_amount else None,
                    "percentage": str(a.percentage) if a.percentage else None,
                    "reason": a.reason,
                    "reason_code": a.reason_code,
                    "tax_category": a.tax_category,
                    "tax_rate": str(a.tax_rate),
                } for a in (getattr(l, "allowances_charges", None) or [])],
            } for l in inv.lines],
            "allowances_charges": [{
                "is_charge": ac.is_charge,
                "amount": str(ac.amount),
                "base_amount": str(ac.base_amount) if ac.base_amount else None,
                "percentage": str(ac.percentage) if ac.percentage else None,
                "reason": ac.reason,
                "reason_code": ac.reason_code,
                "tax_category": ac.tax_category,
                "tax_rate": str(ac.tax_rate),
            } for ac in inv.allowances_charges],
            "audit_trail": [{
                "event_type": e.event_type,
                "user": e.user,
                "timestamp": e.timestamp,
                "comment": e.comment,
                "old_value": e.old_value,
                "new_value": e.new_value,
            } for e in inv.audit_trail],
        }

    def _deserialize_invoice(self, d: dict) -> Invoice:
        """Konvertiert ein dict zurueck in ein Invoice-Objekt."""
        s = d["seller"]
        b = d["buyer"]
        p = d["payment"]

        inv = Invoice(
            invoice_number=d["invoice_number"],
            invoice_date=date.fromisoformat(d["invoice_date"]) if d.get("invoice_date") else date.today(),
            invoice_type_code=d.get("invoice_type_code", "380"),
            currency_code=d.get("currency_code", "EUR"),
            buyer_reference=d.get("buyer_reference", ""),
            order_reference=d.get("order_reference", ""),
            contract_reference=d.get("contract_reference", ""),
            project_reference=d.get("project_reference", ""),
            preceding_invoice=d.get("preceding_invoice", ""),
            preceding_invoice_date=(date.fromisoformat(d["preceding_invoice_date"])
                                    if d.get("preceding_invoice_date") else None),
            # Anzahlungen (BT-113) — fehlt bei Altbestaenden, dann 0,00
            prepaid_amount=Decimal(str(d.get("prepaid_amount") or "0.00")),
            prepaid_details=list(d.get("prepaid_details") or []),
            steuerfall=(d.get("steuerfall") or "regel"),
            steuerbefreiung_grund=(d.get("steuerbefreiung_grund") or ""),
            steuerbefreiung_code=(d.get("steuerbefreiung_code") or ""),
            note=d.get("note", ""),
            tax_point_date=date.fromisoformat(d["tax_point_date"]) if d.get("tax_point_date") else None,
            period_start=date.fromisoformat(d["period_start"]) if d.get("period_start") else None,
            period_end=date.fromisoformat(d["period_end"]) if d.get("period_end") else None,
            status=d.get("status", "NEU"),
            assigned_to=d.get("assigned_to", ""),
            seller=Seller(
                name=s["name"],
                address=Address(
                    street=s.get("street", ""),
                    house_number=s.get("house_number", ""),
                    city=s.get("city", ""),
                    post_code=s.get("post_code", ""),
                    country_code=s.get("country_code", "DE"),
                    address_line_2=s.get("address_line_2", ""),
                ),
                electronic_address=s.get("electronic_address", ""),
                electronic_address_scheme=s.get("electronic_address_scheme", "EM"),
                vat_id=s.get("vat_id", ""),
                tax_registration_id=s.get("tax_registration_id", ""),
                registration_name=s.get("registration_name", "") or "",
                company_id=s.get("company_id", "") or "",
                seller_id=s.get("seller_id", "") or "",
                legal_info=s.get("legal_info", "") or "",
                contact=Contact(s.get("contact_name", ""), s.get("contact_phone", ""),
                                s.get("contact_email", "")),
            ),
            buyer=Buyer(
                name=b["name"],
                address=Address(
                    street=b.get("street", ""),
                    house_number=b.get("house_number", ""),
                    city=b.get("city", ""),
                    post_code=b.get("post_code", ""),
                    country_code=b.get("country_code", "DE"),
                    address_line_2=b.get("address_line_2", ""),
                ),
                electronic_address=b.get("electronic_address", ""),
                electronic_address_scheme=b.get("electronic_address_scheme", "EM"),
                buyer_reference=b.get("buyer_reference", ""),
                vat_id=b.get("vat_id", ""),
                company_id=b.get("company_id", "") or "",
                buyer_id=b.get("buyer_id", "") or "",
                contact=(Contact(b.get("contact_name", "") or "", b.get("contact_phone", "") or "",
                                 b.get("contact_email", "") or "")
                         if (b.get("contact_name") or b.get("contact_phone") or b.get("contact_email")) else None),
            ),
            payment=PaymentInfo(
                means_code=p.get("means_code", "58"),
                iban=p.get("iban", ""),
                bic=p.get("bic", ""),
                bank_name=p.get("bank_name", ""),
                due_date=date.fromisoformat(p["due_date"]) if p.get("due_date") else None,
                payment_terms=p.get("payment_terms", ""),
                mandate_reference=p.get("mandate_reference", ""),
                creditor_id=p.get("creditor_id", ""),
                debited_account=p.get("debited_account", ""),
                skonto_percent=float(p.get("skonto_percent") or 0),
                skonto_days=int(p.get("skonto_days") or 0),
                skonto2_percent=float(p.get("skonto2_percent") or 0),
                skonto2_days=int(p.get("skonto2_days") or 0),
                remittance=p.get("remittance", "") or "",
            ),
        )

        # ID und Meta
        inv._id = d["_id"]
        inv._direction = d.get("_direction", "")
        inv._altbestand = bool(d.get("_altbestand", False))
        inv._source_file = d.get("_source_file", "")
        inv._source_format = d.get("_source_format", "")
        inv._received_at = d.get("_received_at", "")
        inv._sender_email = d.get("_sender_email", "")
        if isinstance(d.get("_datei_summen"), dict) and d["_datei_summen"]:
            try:
                inv._datei_summen = {k: Decimal(str(v)) for k, v in d["_datei_summen"].items()}
            except Exception:
                pass

        __import__("rechnungszusatz").laden(inv, d.get("zusatz"), self.data_dir)

        # Positionen
        for ld in d.get("lines", []):
            inv.lines.append(InvoiceLine(
                line_id=ld["line_id"],
                quantity=Decimal(ld["quantity"]),
                unit_code=ld.get("unit_code", "C62"),
                line_net_amount=Decimal(ld["line_net_amount"]),
                item_name=ld.get("item_name", ""),
                item_description=ld.get("item_description", ""),
                unit_price=Decimal(ld["unit_price"]),
                price_base_quantity=Decimal(ld["price_base_quantity"]) if ld.get("price_base_quantity") else None,
                tax_category=ld.get("tax_category", "S"),
                tax_rate=Decimal(ld["tax_rate"]),
                item_id=ld.get("item_id", ""),
                period_start=date.fromisoformat(ld["period_start"]) if ld.get("period_start") else None,
                period_end=date.fromisoformat(ld["period_end"]) if ld.get("period_end") else None,
                note=ld.get("note", "") or "",
                order_reference=ld.get("order_reference", "") or "",
                allowances_charges=[AllowanceCharge(
                    is_charge=bool(a.get("is_charge")),
                    amount=Decimal(str(a.get("amount") or "0")),
                    base_amount=Decimal(str(a.get("base_amount") or "0")),
                    percentage=Decimal(str(a.get("percentage") or "0")),
                    reason=a.get("reason", "") or "",
                    reason_code=a.get("reason_code", "") or "",
                    tax_category=a.get("tax_category", "S") or "S",
                    tax_rate=Decimal(str(a.get("tax_rate") or "0")),
                ) for a in (ld.get("allowances_charges") or [])],
            ))

        # Nachlasse/Zuschlaege
        for acd in d.get("allowances_charges", []):
            inv.allowances_charges.append(AllowanceCharge(
                is_charge=acd["is_charge"],
                amount=Decimal(acd["amount"]),
                base_amount=Decimal(acd["base_amount"]) if acd.get("base_amount") else None,
                percentage=Decimal(acd["percentage"]) if acd.get("percentage") else None,
                reason=acd.get("reason", ""),
                reason_code=acd.get("reason_code", ""),
                tax_category=acd.get("tax_category", "S"),
                tax_rate=Decimal(acd["tax_rate"]),
            ))

        # Audit-Trail
        for ed in d.get("audit_trail", []):
            inv.audit_trail.append(AuditEvent(
                event_type=ed["event_type"],
                user=ed.get("user", ""),
                timestamp=ed.get("timestamp", ""),
                comment=ed.get("comment", ""),
                old_value=ed.get("old_value", ""),
                new_value=ed.get("new_value", ""),
            ))

        # Bezahlt-Status (Bank-Match-Persistenz)
        if d.get("_paid_at"):
            inv._paid_at = d["_paid_at"]
        if d.get("_paid_amount") is not None:
            inv._paid_amount = d["_paid_amount"]
        if d.get("_nutzerabo"):
            inv._nutzerabo = d["_nutzerabo"]
        if d.get("_kosit"):
            inv._kosit = dict(d["_kosit"])
        if d.get("_dauer_zahlungen"):
            inv._dauer_zahlungen = list(d["_dauer_zahlungen"])
        if d.get("_paid_via_booking"):
            inv._paid_via_booking = d["_paid_via_booking"]
        if d.get("_skonto_zahlung"):
            inv._skonto_zahlung = dict(d["_skonto_zahlung"])
        if d.get("_teilzahlungen"):
            inv._teilzahlungen = [dict(z) for z in d["_teilzahlungen"] if isinstance(z, dict)]
        if d.get("_mahnungen"):
            inv._mahnungen = [dict(z) for z in d["_mahnungen"] if isinstance(z, dict)]
        if isinstance(d.get("_kontierung"), dict) and d["_kontierung"]:
            inv._kontierung = dict(d["_kontierung"])
        if isinstance(d.get("_bezahlt_manuell"), dict) and d["_bezahlt_manuell"]:
            inv._bezahlt_manuell = dict(d["_bezahlt_manuell"])

        return inv

    # ── Speichern / Laden ──

    def save(self, invoices: dict[str, Invoice]):
        """Speichert alle Rechnungen als JSON auf die Festplatte.

        Leerschutz (31.07.2026): Ein LEERER Store darf einen gefüllten Bestand
        NIE überschreiben. Genau so gehen Rechnungen verloren, wenn webapp in
        einem Zweitprozess importiert wird (Test-Client, Skript) und dort ein
        auto_save auslöst — der Zweitprozess hat einen leeren Store, gewinnt aber
        auf der Platte. Der Bestand bleibt dann einfach stehen.
        """
        # Sperre + eigene Zwischendatei je Thread (20.10.2026): Flask bedient Anfragen parallel; bei Scanner-Wellen
        # schrieben zwei auto_save gleichzeitig in DIESELBE invoices.json.tmp — einer scheiterte am os.replace
        # („No such file …tmp", 41× an einem Tag), und eine halb geschriebene Datei konnte übernommen werden
        # (db_pg las „Expecting value …"). Jetzt immer nur ein Schreiber, vollständig auf Platte, dann atomar ersetzen.
        with _SPEICHER_SPERRE:
            self._save_gesperrt(invoices)

    def _save_gesperrt(self, invoices: dict[str, Invoice]):
        data = {inv_id: self._serialize_invoice(inv) for inv_id, inv in list(invoices.items())}
        if not data and self.invoices_file.exists():
            try:
                with open(self.invoices_file, "r", encoding="utf-8") as f:
                    vorhanden = json.load(f) or {}
            except Exception:
                vorhanden = {}
            if vorhanden:
                print(f"  ABGEWEHRT: leerer Rechnungs-Store sollte {len(vorhanden)} "
                      f"gespeicherte Rechnung(en) überschreiben — Datei bleibt unverändert.",
                      flush=True)
                return
        _atomar_schreiben(self.invoices_file, data)

    def load(self) -> dict[str, Invoice]:
        """Laedt alle Rechnungen von der Festplatte."""
        if not self.invoices_file.exists():
            return {}
        try:
            with open(self.invoices_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            result = {}
            for inv_id, d in data.items():
                try:
                    result[inv_id] = self._deserialize_invoice(d)
                except Exception as e:
                    print(f"  WARNUNG: Rechnung {inv_id} konnte nicht geladen werden: {e}")
            return result
        except Exception as e:
            print(f"  WARNUNG: invoices.json konnte nicht gelesen werden: {e}")
            return {}

    def has_saved_data(self) -> bool:
        """Prueft ob gespeicherte Daten vorhanden sind."""
        return self.invoices_file.exists() and self.invoices_file.stat().st_size > 10

    def save_state(self, state: dict):
        """Speichert zusaetzlichen Zustand (Duplikate, Suggestions etc.)."""
        with _SPEICHER_SPERRE:
            _atomar_schreiben(self.state_file, state)

    def load_state(self) -> dict:
        """Laedt zusaetzlichen Zustand."""
        if not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
