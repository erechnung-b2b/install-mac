"""
E-Rechnungssystem – Persistenz
Speichert und laedt alle Rechnungsdaten als JSON auf der Festplatte.
Wird beim Beenden automatisch gespeichert und beim Start geladen.
"""
from __future__ import annotations
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from models import (
    Invoice, Seller, Buyer, Address, Contact,
    PaymentInfo, InvoiceLine, AllowanceCharge, AuditEvent,
)


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
            "note": inv.note,
            "tax_point_date": inv.tax_point_date.isoformat() if inv.tax_point_date else None,
            "period_start": inv.period_start.isoformat() if inv.period_start else None,
            "period_end": inv.period_end.isoformat() if inv.period_end else None,
            "status": inv.status,
            "assigned_to": inv.assigned_to,
            "seller": {
                "name": inv.seller.name,
                "street": inv.seller.address.street,
                "city": inv.seller.address.city,
                "post_code": inv.seller.address.post_code,
                "country_code": inv.seller.address.country_code,
                "electronic_address": inv.seller.electronic_address,
                "electronic_address_scheme": inv.seller.electronic_address_scheme,
                "vat_id": inv.seller.vat_id,
                "tax_registration_id": inv.seller.tax_registration_id,
                "contact_name": inv.seller.contact.name,
                "contact_phone": inv.seller.contact.telephone,
                "contact_email": inv.seller.contact.email,
            },
            "buyer": {
                "name": inv.buyer.name,
                "street": inv.buyer.address.street,
                "city": inv.buyer.address.city,
                "post_code": inv.buyer.address.post_code,
                "country_code": inv.buyer.address.country_code,
                "electronic_address": inv.buyer.electronic_address,
                "electronic_address_scheme": inv.buyer.electronic_address_scheme,
                "vat_id": inv.buyer.vat_id,
                "buyer_reference": inv.buyer.buyer_reference,
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
            note=d.get("note", ""),
            tax_point_date=date.fromisoformat(d["tax_point_date"]) if d.get("tax_point_date") else None,
            period_start=date.fromisoformat(d["period_start"]) if d.get("period_start") else None,
            period_end=date.fromisoformat(d["period_end"]) if d.get("period_end") else None,
            status=d.get("status", "NEU"),
            assigned_to=d.get("assigned_to", ""),
            seller=Seller(
                name=s["name"],
                address=Address(s.get("street", ""), s.get("city", ""), s.get("post_code", ""),
                                s.get("country_code", "DE")),
                electronic_address=s.get("electronic_address", ""),
                electronic_address_scheme=s.get("electronic_address_scheme", "EM"),
                vat_id=s.get("vat_id", ""),
                tax_registration_id=s.get("tax_registration_id", ""),
                contact=Contact(s.get("contact_name", ""), s.get("contact_phone", ""),
                                s.get("contact_email", "")),
            ),
            buyer=Buyer(
                name=b["name"],
                address=Address(b.get("street", ""), b.get("city", ""), b.get("post_code", ""),
                                b.get("country_code", "DE")),
                electronic_address=b.get("electronic_address", ""),
                electronic_address_scheme=b.get("electronic_address_scheme", "EM"),
                buyer_reference=b.get("buyer_reference", ""),
                vat_id=b.get("vat_id", ""),
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
            ),
        )

        # ID und Meta
        inv._id = d["_id"]
        inv._direction = d.get("_direction", "")
        inv._source_file = d.get("_source_file", "")
        inv._source_format = d.get("_source_format", "")
        inv._received_at = d.get("_received_at", "")
        inv._sender_email = d.get("_sender_email", "")

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

        return inv

    # ── Speichern / Laden ──

    def save(self, invoices: dict[str, Invoice]):
        """Speichert alle Rechnungen als JSON auf die Festplatte."""
        data = {inv_id: self._serialize_invoice(inv) for inv_id, inv in invoices.items()}
        tmp = str(self.invoices_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Atomarer Schreibvorgang
        os.replace(tmp, str(self.invoices_file))

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
        tmp = str(self.state_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(self.state_file))

    def load_state(self) -> dict:
        """Laedt zusaetzlichen Zustand."""
        if not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
