"""
E-Rechnungssystem – Fachliches Datenmodell
EN 16931 / XRechnung BT/BG-Felder
Basis: erechnung-erstellen.txt Abschnitt 6 + e-rechnung_pflichtenheft.docx Abschnitt 7
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
import json, uuid


class InvoiceStatus(Enum):
    NEU = "NEU"
    IN_PRUEFUNG = "IN_PRUEFUNG"
    IN_FREIGABE = "IN_FREIGABE"
    FREIGEGEBEN = "FREIGEGEBEN"
    EXPORTIERT = "EXPORTIERT"
    ZURUECKGEWIESEN = "ZURUECKGEWIESEN"
    ARCHIVIERT = "ARCHIVIERT"


class WorkflowAction(Enum):
    SACHLICHE_PRUEFUNG = "SACHLICHE_PRUEFUNG"
    FORMALE_PRUEFUNG = "FORMALE_PRUEFUNG"
    KAUFMAENNISCHE_FREIGABE = "KAUFMAENNISCHE_FREIGABE"
    ABLEHNUNG = "ABLEHNUNG"
    RUECKFRAGE = "RUECKFRAGE"
    WIEDERVORLAGE = "WIEDERVORLAGE"
    EXPORT = "EXPORT"


@dataclass
class Address:
    street: str = ""
    city: str = ""
    post_code: str = ""
    country_code: str = "DE"
    address_line_2: str = ""


@dataclass
class Contact:
    name: str = ""
    telephone: str = ""
    email: str = ""


@dataclass
class Seller:
    name: str = ""
    address: Address = field(default_factory=Address)
    electronic_address: str = ""
    electronic_address_scheme: str = "EM"
    contact: Contact = field(default_factory=Contact)
    vat_id: str = ""
    tax_registration_id: str = ""
    registration_name: str = ""
    company_id: str = ""


@dataclass
class Buyer:
    name: str = ""
    address: Address = field(default_factory=Address)
    electronic_address: str = ""
    electronic_address_scheme: str = "EM"
    buyer_reference: str = ""
    vat_id: str = ""
    company_id: str = ""
    contact: Optional[Contact] = None


@dataclass
class PaymentInfo:
    means_code: str = "58"
    due_date: Optional[date] = None
    payment_terms: str = ""
    iban: str = ""
    bic: str = ""
    bank_name: str = ""
    mandate_reference: str = ""
    creditor_id: str = ""
    debited_account: str = ""


@dataclass
class AllowanceCharge:
    is_charge: bool = False
    amount: Decimal = Decimal("0.00")
    base_amount: Decimal = Decimal("0.00")
    percentage: Decimal = Decimal("0.00")
    reason: str = ""
    reason_code: str = ""
    tax_category: str = "S"
    tax_rate: Decimal = Decimal("19.00")


@dataclass
class InvoiceLine:
    line_id: str = ""
    quantity: Decimal = Decimal("1")
    unit_code: str = "C62"
    line_net_amount: Decimal = Decimal("0.00")
    item_name: str = ""
    unit_price: Decimal = Decimal("0.00")
    tax_category: str = "S"
    tax_rate: Decimal = Decimal("19.00")
    item_description: str = ""
    item_id: str = ""
    price_base_quantity: Decimal = Decimal("1")
    allowances_charges: list[AllowanceCharge] = field(default_factory=list)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    order_reference: str = ""
    note: str = ""

    def compute_net(self) -> Decimal:
        base = self.quantity * self.unit_price / self.price_base_quantity
        for ac in self.allowances_charges:
            base = base + ac.amount if ac.is_charge else base - ac.amount
        return base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class TaxSubtotal:
    taxable_amount: Decimal = Decimal("0.00")
    tax_amount: Decimal = Decimal("0.00")
    category_code: str = "S"
    rate: Decimal = Decimal("19.00")
    exemption_reason: str = ""


@dataclass
class AuditEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    user: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    old_value: str = ""
    new_value: str = ""
    comment: str = ""
    source: str = ""


@dataclass
class Invoice:
    invoice_number: str = ""
    invoice_date: date = field(default_factory=date.today)
    invoice_type_code: str = "380"
    currency_code: str = "EUR"
    buyer_reference: str = ""
    tax_point_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    note: str = ""
    order_reference: str = ""
    contract_reference: str = ""
    project_reference: str = ""
    preceding_invoice: str = ""

    seller: Seller = field(default_factory=Seller)
    buyer: Buyer = field(default_factory=Buyer)
    payment: PaymentInfo = field(default_factory=PaymentInfo)
    lines: list[InvoiceLine] = field(default_factory=list)
    allowances_charges: list[AllowanceCharge] = field(default_factory=list)

    # Workflow (Eingangsrechnung)
    status: str = InvoiceStatus.NEU.value
    assigned_to: str = ""
    audit_trail: list[AuditEvent] = field(default_factory=list)

    # Meta
    _id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _direction: str = ""  # EINGANG / AUSGANG
    _source_file: str = ""
    _source_format: str = ""
    _received_at: str = ""
    _sender_email: str = ""

    def sum_line_net(self) -> Decimal:
        return sum((l.line_net_amount for l in self.lines), Decimal("0.00"))

    def sum_allowances(self) -> Decimal:
        return sum((ac.amount for ac in self.allowances_charges if not ac.is_charge), Decimal("0.00"))

    def sum_charges(self) -> Decimal:
        return sum((ac.amount for ac in self.allowances_charges if ac.is_charge), Decimal("0.00"))

    def tax_exclusive_amount(self) -> Decimal:
        return (self.sum_line_net() - self.sum_allowances() + self.sum_charges()
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def compute_tax_subtotals(self) -> list[TaxSubtotal]:
        buckets: dict[tuple[str, Decimal], Decimal] = {}
        for line in self.lines:
            key = (line.tax_category, line.tax_rate)
            buckets[key] = buckets.get(key, Decimal("0.00")) + line.line_net_amount
        for ac in self.allowances_charges:
            key = (ac.tax_category, ac.tax_rate)
            adj = ac.amount if ac.is_charge else -ac.amount
            buckets[key] = buckets.get(key, Decimal("0.00")) + adj
        result = []
        for (cat, rate), taxable in sorted(buckets.items()):
            taxable = taxable.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax = (taxable * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            result.append(TaxSubtotal(taxable_amount=taxable, tax_amount=tax, category_code=cat, rate=rate))
        return result

    def tax_amount(self) -> Decimal:
        return sum((t.tax_amount for t in self.compute_tax_subtotals()), Decimal("0.00"))

    def tax_inclusive_amount(self) -> Decimal:
        return self.tax_exclusive_amount() + self.tax_amount()

    def amount_due(self, prepaid: Decimal = Decimal("0.00"), rounding: Decimal = Decimal("0.00")) -> Decimal:
        return self.tax_inclusive_amount() - prepaid + rounding

    def add_audit(self, event_type: str, user: str = "system", comment: str = "",
                  old_value: str = "", new_value: str = ""):
        self.audit_trail.append(AuditEvent(
            event_type=event_type, user=user, comment=comment,
            old_value=old_value, new_value=new_value))

    def to_dict(self) -> dict:
        def _ser(obj):
            if isinstance(obj, (date, datetime)): return obj.isoformat()
            if isinstance(obj, Decimal): return str(obj)
            if isinstance(obj, Enum): return obj.value
            raise TypeError(f"Cannot serialize {type(obj)}")
        import dataclasses
        return json.loads(json.dumps(dataclasses.asdict(self), default=_ser))
