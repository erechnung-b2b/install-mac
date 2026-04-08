"""
E-Rechnungssystem – XRechnung/ZUGFeRD Parser
FR-200..FR-230: Eingangsrechnungen lesen, Format erkennen, Felder extrahieren
Parst UBL 2.1 XRechnung-XML in ein Invoice-Fachobjekt.
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal
from pathlib import Path
from lxml import etree
from models import (
    Invoice, Seller, Buyer, Address, Contact,
    PaymentInfo, InvoiceLine, AllowanceCharge, TaxSubtotal,
)

NS = {
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def _find(el, xpath: str) -> str:
    """XPath-Suche mit Namespace, gibt Text oder '' zurück."""
    node = el.find(xpath, NS)
    return (node.text or "").strip() if node is not None else ""


def _find_decimal(el, xpath: str) -> Decimal:
    txt = _find(el, xpath)
    return Decimal(txt) if txt else Decimal("0.00")


def _find_date(el, xpath: str):
    txt = _find(el, xpath)
    if txt:
        try:
            return date.fromisoformat(txt)
        except ValueError:
            pass
    return None


def _parse_address(party_el) -> Address:
    addr_el = party_el.find("cac:PostalAddress", NS)
    if addr_el is None:
        return Address()
    return Address(
        street=_find(addr_el, "cbc:StreetName"),
        city=_find(addr_el, "cbc:CityName"),
        post_code=_find(addr_el, "cbc:PostalZone"),
        country_code=_find(addr_el, "cac:Country/cbc:IdentificationCode") or "DE",
        address_line_2=_find(addr_el, "cbc:AdditionalStreetName"),
    )


def _parse_contact(party_el) -> Contact:
    c_el = party_el.find("cac:Contact", NS)
    if c_el is None:
        return Contact()
    return Contact(
        name=_find(c_el, "cbc:Name"),
        telephone=_find(c_el, "cbc:Telephone"),
        email=_find(c_el, "cbc:ElectronicMail"),
    )


def _parse_seller(root) -> Seller:
    party = root.find("cac:AccountingSupplierParty/cac:Party", NS)
    if party is None:
        return Seller()

    endpoint = party.find("cbc:EndpointID", NS)
    ea = endpoint.text.strip() if endpoint is not None and endpoint.text else ""
    ea_scheme = endpoint.get("schemeID", "EM") if endpoint is not None else "EM"

    vat_id = ""
    tax_reg = ""
    for pts in party.findall("cac:PartyTaxScheme", NS):
        scheme_id = _find(pts, "cac:TaxScheme/cbc:ID")
        cid = _find(pts, "cbc:CompanyID")
        if scheme_id == "VAT":
            vat_id = cid
        elif scheme_id == "FC":
            tax_reg = cid

    return Seller(
        name=_find(party, "cac:PartyName/cbc:Name"),
        address=_parse_address(party),
        electronic_address=ea,
        electronic_address_scheme=ea_scheme,
        contact=_parse_contact(party),
        vat_id=vat_id,
        tax_registration_id=tax_reg,
        registration_name=_find(party, "cac:PartyLegalEntity/cbc:RegistrationName"),
        company_id=_find(party, "cac:PartyLegalEntity/cbc:CompanyID"),
    )


def _parse_buyer(root) -> Buyer:
    party = root.find("cac:AccountingCustomerParty/cac:Party", NS)
    if party is None:
        return Buyer()

    endpoint = party.find("cbc:EndpointID", NS)
    ea = endpoint.text.strip() if endpoint is not None and endpoint.text else ""
    ea_scheme = endpoint.get("schemeID", "EM") if endpoint is not None else "EM"

    return Buyer(
        name=_find(party, "cac:PartyName/cbc:Name"),
        address=_parse_address(party),
        electronic_address=ea,
        electronic_address_scheme=ea_scheme,
        buyer_reference="",  # BT-10 steht im Header
        vat_id=_find(party, "cac:PartyTaxScheme/cbc:CompanyID"),
        company_id=_find(party, "cac:PartyLegalEntity/cbc:CompanyID"),
    )


def _parse_payment(root) -> PaymentInfo:
    pm = root.find("cac:PaymentMeans", NS)
    pt = root.find("cac:PaymentTerms", NS)

    info = PaymentInfo()
    if pm is not None:
        info.means_code = _find(pm, "cbc:PaymentMeansCode") or "58"
        info.iban = _find(pm, "cac:PayeeFinancialAccount/cbc:ID")
        info.bic = _find(pm, "cac:PayeeFinancialAccount/cac:FinancialInstitutionBranch/cbc:ID")
    if pt is not None:
        info.payment_terms = _find(pt, "cbc:Note")

    info.due_date = _find_date(root, "cbc:DueDate")
    return info


def _parse_allowance_charge(ac_el) -> AllowanceCharge:
    return AllowanceCharge(
        is_charge=_find(ac_el, "cbc:ChargeIndicator") == "true",
        amount=_find_decimal(ac_el, "cbc:Amount"),
        base_amount=_find_decimal(ac_el, "cbc:BaseAmount"),
        percentage=_find_decimal(ac_el, "cbc:MultiplierFactorNumeric"),
        reason=_find(ac_el, "cbc:AllowanceChargeReason"),
        reason_code=_find(ac_el, "cbc:AllowanceChargeReasonCode"),
        tax_category=_find(ac_el, "cac:TaxCategory/cbc:ID") or "S",
        tax_rate=_find_decimal(ac_el, "cac:TaxCategory/cbc:Percent"),
    )


def _parse_line(line_el) -> InvoiceLine:
    qty_el = line_el.find("cbc:InvoicedQuantity", NS)
    item_el = line_el.find("cac:Item", NS)
    price_el = line_el.find("cac:Price", NS)

    line = InvoiceLine(
        line_id=_find(line_el, "cbc:ID"),
        quantity=_find_decimal(line_el, "cbc:InvoicedQuantity"),
        unit_code=qty_el.get("unitCode", "C62") if qty_el is not None else "C62",
        line_net_amount=_find_decimal(line_el, "cbc:LineExtensionAmount"),
        note=_find(line_el, "cbc:Note"),
    )

    if item_el is not None:
        line.item_name = _find(item_el, "cbc:Name")
        line.item_description = _find(item_el, "cbc:Description")
        line.item_id = _find(item_el, "cac:SellersItemIdentification/cbc:ID")
        line.tax_category = _find(item_el, "cac:ClassifiedTaxCategory/cbc:ID") or "S"
        line.tax_rate = _find_decimal(item_el, "cac:ClassifiedTaxCategory/cbc:Percent")

    if price_el is not None:
        line.unit_price = _find_decimal(price_el, "cbc:PriceAmount")
        bq = _find_decimal(price_el, "cbc:BaseQuantity")
        if bq > 0:
            line.price_base_quantity = bq

    line.order_reference = _find(line_el, "cac:OrderLineReference/cbc:LineID")

    for ac_el in line_el.findall("cac:AllowanceCharge", NS):
        line.allowances_charges.append(_parse_allowance_charge(ac_el))

    return line


# ── Format-Erkennung (FR-200) ─────────────────────────────────────────

class FormatInfo:
    def __init__(self):
        self.format_type: str = "UNKNOWN"  # XRECHNUNG, ZUGFERD, UNKNOWN
        self.customization_id: str = ""
        self.profile_id: str = ""
        self.is_hybrid: bool = False

    def __repr__(self):
        return f"FormatInfo({self.format_type}, customization={self.customization_id[:40]}...)"


def detect_format(xml_bytes: bytes) -> FormatInfo:
    """Erkennt ob XRechnung, ZUGFeRD oder unbekannt."""
    info = FormatInfo()
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return info

    cust = _find(root, "cbc:CustomizationID")
    prof = _find(root, "cbc:ProfileID")
    info.customization_id = cust
    info.profile_id = prof

    if "xrechnung" in cust.lower() or "xeinkauf" in cust.lower():
        info.format_type = "XRECHNUNG"
    elif "factur-x" in cust.lower() or "zugferd" in cust.lower():
        info.format_type = "ZUGFERD"
        info.is_hybrid = True
    elif "en16931" in cust.lower():
        info.format_type = "XRECHNUNG"  # EN-16931-konform
    else:
        info.format_type = "UBL_GENERIC"

    return info


# ── Haupt-Parser ──────────────────────────────────────────────────────

def parse_xrechnung(xml_bytes: bytes, source_file: str = "") -> Invoice:
    """Parst XRechnung UBL 2.1 XML in ein Invoice-Fachobjekt."""
    root = etree.fromstring(xml_bytes)
    fmt = detect_format(xml_bytes)

    inv = Invoice(
        invoice_number=_find(root, "cbc:ID"),
        invoice_date=_find_date(root, "cbc:IssueDate") or date.today(),
        invoice_type_code=_find(root, "cbc:InvoiceTypeCode") or "380",
        currency_code=_find(root, "cbc:DocumentCurrencyCode") or "EUR",
        buyer_reference=_find(root, "cbc:BuyerReference"),
        note=_find(root, "cbc:Note"),
        seller=_parse_seller(root),
        buyer=_parse_buyer(root),
        payment=_parse_payment(root),
    )

    inv.buyer.buyer_reference = inv.buyer_reference

    # Leistungszeitraum
    period = root.find("cac:InvoicePeriod", NS)
    if period is not None:
        inv.period_start = _find_date(period, "cbc:StartDate")
        inv.period_end = _find_date(period, "cbc:EndDate")

    # Referenzen
    inv.order_reference = _find(root, "cac:OrderReference/cbc:ID")
    inv.contract_reference = _find(root, "cac:ContractDocumentReference/cbc:ID")
    inv.project_reference = _find(root, "cac:ProjectReference/cbc:ID")
    inv.preceding_invoice = _find(root, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID")

    # Dokument-Nachlässe/Zuschläge
    for ac_el in root.findall("cac:AllowanceCharge", NS):
        inv.allowances_charges.append(_parse_allowance_charge(ac_el))

    # Positionen
    for line_el in root.findall("cac:InvoiceLine", NS):
        inv.lines.append(_parse_line(line_el))

    # Meta
    inv._source_file = source_file
    inv._source_format = fmt.format_type
    inv._received_at = __import__("datetime").datetime.now().isoformat()

    return inv


def parse_file(filepath: str) -> Invoice:
    """Liest eine XML-Datei und parst sie."""
    path = Path(filepath)
    xml_bytes = path.read_bytes()
    return parse_xrechnung(xml_bytes, source_file=path.name)
