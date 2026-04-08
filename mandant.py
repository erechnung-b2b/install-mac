"""
E-Rechnungssystem – Mandantenverwaltung
FR-010..FR-050: Mandanten, Stammdaten, Lieferantenerkennung, Freigaberegeln
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional
import json, uuid
from pathlib import Path

from models import Invoice, Seller, Buyer, Address, AuditEvent


@dataclass
class Supplier:
    """Lieferanten-Stammdaten (FR-030)"""
    supplier_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    email_domains: list[str] = field(default_factory=list)
    vat_id: str = ""
    tax_registration_id: str = ""
    creditor_number: str = ""
    iban: str = ""
    default_cost_center: str = ""
    default_account: str = ""
    notes: str = ""
    active: bool = True

    def matches_email(self, email: str) -> bool:
        domain = email.split("@")[-1].lower() if "@" in email else ""
        return domain in [d.lower() for d in self.email_domains]

    def matches_vat(self, vat_id: str) -> bool:
        return self.vat_id and self.vat_id.upper() == vat_id.upper()

    def matches_name(self, name: str) -> bool:
        return self.name.lower() in name.lower() or name.lower() in self.name.lower()


@dataclass
class ApprovalRule:
    """Betragsabhängige Freigaberegel pro Mandant (FR-050)"""
    rule_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    min_amount: Decimal = Decimal("0")
    max_amount: Decimal = Decimal("999999999")
    required_role: str = "buchhaltung"
    four_eyes: bool = False
    escalation_days: int = 5
    cost_center: str = ""  # Optional: nur für bestimmte Kostenstelle


@dataclass
class MandatoryReference:
    """Pflichtreferenzen pro Mandant (FR-040)"""
    field_name: str = ""  # z.B. "order_reference", "cost_center"
    label: str = ""
    required: bool = True
    block_on_missing: bool = False  # True = Blockade, False = nur Warnung


@dataclass
class ExportTarget:
    """Exportziel-Konfiguration pro Mandant"""
    target_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    format: str = "DATEV"  # DATEV, CSV, API
    path: str = ""
    active: bool = True
    config: dict = field(default_factory=dict)


@dataclass
class Mandant:
    """FR-010: Mandant mit getrennten Datenbereichen"""
    mandant_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    address: Address = field(default_factory=Address)
    vat_id: str = ""
    default_currency: str = "EUR"
    inbox_email: str = ""

    # Stammdaten
    suppliers: list[Supplier] = field(default_factory=list)
    approval_rules: list[ApprovalRule] = field(default_factory=list)
    mandatory_refs: list[MandatoryReference] = field(default_factory=list)
    export_targets: list[ExportTarget] = field(default_factory=list)

    # Benutzer/Rollen
    users: dict[str, str] = field(default_factory=dict)  # username → role

    # Audit
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def find_supplier(self, inv: Invoice) -> Optional[Supplier]:
        """FR-030: Automatische Lieferantenerkennung (≥95% Erkennungsquote)"""
        for s in self.suppliers:
            if not s.active:
                continue
            if inv.seller.vat_id and s.matches_vat(inv.seller.vat_id):
                return s
            if inv._sender_email and s.matches_email(inv._sender_email):
                return s
            if inv.seller.name and s.matches_name(inv.seller.name):
                return s
        return None

    def check_mandatory_refs(self, inv: Invoice) -> list[tuple[str, str, bool]]:
        """FR-040: Prüft Pflichtreferenzen. Gibt [(feld, meldung, blockiert)] zurück."""
        issues = []
        for ref in self.mandatory_refs:
            value = ""
            if ref.field_name == "order_reference":
                value = inv.order_reference
            elif ref.field_name == "cost_center":
                value = inv.project_reference  # Vereinfacht
            elif ref.field_name == "contract_reference":
                value = inv.contract_reference
            elif ref.field_name == "buyer_reference":
                value = inv.buyer_reference or inv.buyer.buyer_reference

            if ref.required and not value:
                msg = f"Pflichtreferenz '{ref.label}' fehlt."
                issues.append((ref.field_name, msg, ref.block_on_missing))
        return issues

    def get_approval_rule(self, amount: Decimal, cost_center: str = "") -> Optional[ApprovalRule]:
        """FR-050: Passende Freigaberegel nach Betrag und ggf. Kostenstelle"""
        for rule in self.approval_rules:
            if rule.min_amount <= amount <= rule.max_amount:
                if rule.cost_center and cost_center and rule.cost_center != cost_center:
                    continue
                return rule
        return None

    def add_supplier(self, supplier: Supplier):
        self.suppliers.append(supplier)

    def add_user(self, username: str, role: str):
        self.users[username] = role

    def has_permission(self, username: str, required_role: str) -> bool:
        """Einfache Rollenprüfung. In Produktion: feingranularer."""
        role_hierarchy = {
            "admin": 100, "geschaeftsfuehrung": 80,
            "buchhaltung": 60, "freigeber": 50,
            "fachabteilung": 30, "leser": 10,
        }
        user_role = self.users.get(username, "")
        return role_hierarchy.get(user_role, 0) >= role_hierarchy.get(required_role, 0)


class MandantManager:
    """Verwaltet mehrere Mandanten mit getrennten Datenbereichen (FR-010)"""

    def __init__(self, data_dir: str = "./mandanten"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.mandanten: dict[str, Mandant] = {}

    def create_mandant(self, name: str, vat_id: str = "", **kwargs) -> Mandant:
        m = Mandant(name=name, vat_id=vat_id, **kwargs)
        self.mandanten[m.mandant_id] = m
        self._save(m)
        return m

    def get_mandant(self, mandant_id: str) -> Optional[Mandant]:
        return self.mandanten.get(mandant_id)

    def list_mandanten(self) -> list[Mandant]:
        return list(self.mandanten.values())

    def assign_invoice_to_mandant(self, inv: Invoice, mandant_id: str) -> str:
        """Ordnet Rechnung einem Mandanten zu und führt Stammdatenabgleich durch."""
        m = self.mandanten.get(mandant_id)
        if not m:
            return f"Mandant {mandant_id} nicht gefunden."

        # Lieferant erkennen
        supplier = m.find_supplier(inv)
        if supplier:
            inv.add_audit("LIEFERANT_ERKANNT",
                          comment=f"Kreditor: {supplier.creditor_number} ({supplier.name})")
        else:
            inv.add_audit("LIEFERANT_UNBEKANNT",
                          comment=f"Kein Stammdatensatz für '{inv.seller.name}'")

        # Pflichtreferenzen prüfen
        ref_issues = m.check_mandatory_refs(inv)
        for fld, msg, blocks in ref_issues:
            inv.add_audit("PFLICHTREFERENZ_FEHLT" if blocks else "REFERENZ_WARNUNG",
                          comment=msg)

        return f"Zugewiesen an Mandant '{m.name}'"

    def _save(self, m: Mandant):
        """Persistiert Mandantendaten (vereinfacht als JSON)."""
        path = self.data_dir / f"{m.mandant_id}.json"
        data = {
            "mandant_id": m.mandant_id, "name": m.name,
            "vat_id": m.vat_id, "default_currency": m.default_currency,
            "inbox_email": m.inbox_email,
            "users": m.users,
            "supplier_count": len(m.suppliers),
            "rule_count": len(m.approval_rules),
            "created_at": m.created_at,
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def create_demo_mandant() -> Mandant:
    """Erstellt einen Demo-Mandanten mit Stammdaten für Tests."""
    m = Mandant(
        name="Demo GmbH",
        vat_id="DE111222333",
        default_currency="EUR",
        inbox_email="rechnungseingang@demo-gmbh.de",
        address=Address("Musterweg 1", "Hamburg", "20095", "DE"),
    )

    # Benutzer
    m.add_user("admin", "admin")
    m.add_user("mueller", "buchhaltung")
    m.add_user("schmidt", "geschaeftsfuehrung")
    m.add_user("weber", "fachabteilung")

    # Lieferanten
    m.add_supplier(Supplier(
        name="Muster GmbH",
        email_domains=["muster-gmbh.de"],
        vat_id="DE123456789",
        creditor_number="70001",
        iban="DE89370400440532013000",
        default_cost_center="IT",
        default_account="4900",
    ))
    m.add_supplier(Supplier(
        name="Bürobedarf Schmidt",
        email_domains=["buero-schmidt.de"],
        vat_id="DE555666777",
        creditor_number="70002",
        default_cost_center="ALLGEMEIN",
        default_account="6800",
    ))

    # Freigaberegeln (FR-050)
    m.approval_rules = [
        ApprovalRule("r1", "Kleinbetrag", Decimal("0"), Decimal("500"),
                     "buchhaltung", False, 7),
        ApprovalRule("r2", "Standard", Decimal("500.01"), Decimal("10000"),
                     "buchhaltung", True, 5),
        ApprovalRule("r3", "Großbetrag", Decimal("10000.01"), Decimal("999999999"),
                     "geschaeftsfuehrung", True, 3),
    ]

    # Pflichtreferenzen (FR-040)
    m.mandatory_refs = [
        MandatoryReference("buyer_reference", "Buyer Reference / Leitweg-ID", True, True),
        MandatoryReference("order_reference", "Bestellnummer", True, False),
    ]

    return m
