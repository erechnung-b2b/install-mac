"""
E-Rechnungssystem – CII-Parser (UN/CEFACT Cross Industry Invoice D16B)

Liest ZUGFeRD 2.x / Factur-X / XRechnung-CII in das Fachobjekt Invoice.
Gegenstueck zu cii_generator.py; die Feldzuordnung entspricht
xrechnung_parser.py (UBL), damit beide Syntaxen dasselbe Ergebnis liefern.

Profile MINIMUM und BASIC WL enthalten keine Positionen — sie werden gelesen,
das Ergebnis hat dann keine Rechnungszeilen (und ist nach § 14 UStG keine
vollstaendige E-Rechnung).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from lxml import etree

from models import (
    Invoice, Seller, Buyer, Address, Contact, PaymentInfo, InvoiceLine, AllowanceCharge,
)

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
}


def is_cii(xml_bytes: bytes) -> bool:
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return False
    return etree.QName(root.tag).localname == "CrossIndustryInvoice"


def _t(el, xpath: str) -> str:
    if el is None:
        return ""
    node = el.find(xpath, NS)
    return (node.text or "").strip() if node is not None and node.text else ""


def _d(el, xpath: str) -> Decimal:
    txt = _t(el, xpath)
    try:
        return Decimal(txt) if txt else Decimal("0.00")
    except InvalidOperation:
        return Decimal("0.00")


def _datum(el, xpath: str):
    """Liest udt:DateTimeString (Format 102 = JJJJMMTT, toleriert ISO)."""
    txt = _t(el, xpath + "/udt:DateTimeString") or _t(el, xpath + "/qdt:DateTimeString")
    if not txt:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt[:10] if "-" in txt else txt[:8], fmt).date()
        except ValueError:
            continue
    return None


def _adresse(party) -> Address:
    pa = party.find("ram:PostalTradeAddress", NS) if party is not None else None
    if pa is None:
        return Address()
    return Address(
        street=_t(pa, "ram:LineOne"),
        address_line_2=_t(pa, "ram:LineTwo"),
        city=_t(pa, "ram:CityName"),
        post_code=_t(pa, "ram:PostcodeCode"),
        country_code=_t(pa, "ram:CountryID") or "DE",
    )


def _kontakt(party) -> Contact:
    c = party.find("ram:DefinedTradeContact", NS) if party is not None else None
    if c is None:
        return Contact()
    return Contact(
        name=_t(c, "ram:PersonName") or _t(c, "ram:DepartmentName"),
        telephone=_t(c, "ram:TelephoneUniversalCommunication/ram:CompleteNumber"),
        email=_t(c, "ram:EmailURIUniversalCommunication/ram:URIID"),
    )


def _steuer_ids(party):
    vat, fc = "", ""
    if party is None:
        return vat, fc
    for reg in party.findall("ram:SpecifiedTaxRegistration/ram:ID", NS):
        scheme = (reg.get("schemeID") or "").upper()
        val = (reg.text or "").strip()
        if scheme == "VA":
            vat = val
        elif scheme == "FC":
            fc = val
    return vat, fc


def _elektronisch(party):
    ep = party.find("ram:URIUniversalCommunication/ram:URIID", NS) if party is not None else None
    if ep is None or not ep.text:
        return "", "EM"
    return ep.text.strip(), ep.get("schemeID", "EM")


def _verkaeufer(agr) -> Seller:
    p = agr.find("ram:SellerTradeParty", NS) if agr is not None else None
    if p is None:
        return Seller()
    vat, fc = _steuer_ids(p)
    ea, scheme = _elektronisch(p)
    return Seller(
        name=_t(p, "ram:Name"),
        address=_adresse(p),
        electronic_address=ea,
        electronic_address_scheme=scheme,
        contact=_kontakt(p),
        vat_id=vat,
        tax_registration_id=fc,
        registration_name=_t(p, "ram:SpecifiedLegalOrganization/ram:TradingBusinessName") or _t(p, "ram:Name"),
        company_id=_t(p, "ram:SpecifiedLegalOrganization/ram:ID"),
    )


def _kaeufer(agr) -> Buyer:
    p = agr.find("ram:BuyerTradeParty", NS) if agr is not None else None
    if p is None:
        return Buyer()
    vat, _ = _steuer_ids(p)
    ea, scheme = _elektronisch(p)
    kontakt = _kontakt(p)
    return Buyer(
        name=_t(p, "ram:Name"),
        address=_adresse(p),
        electronic_address=ea,
        electronic_address_scheme=scheme,
        vat_id=vat,
        company_id=_t(p, "ram:SpecifiedLegalOrganization/ram:ID"),
        contact=kontakt if (kontakt.name or kontakt.email or kontakt.telephone) else None,
    )


def _nachlass(el) -> AllowanceCharge:
    return AllowanceCharge(
        is_charge=_t(el, "ram:ChargeIndicator/udt:Indicator").lower() == "true",
        amount=_d(el, "ram:ActualAmount"),
        base_amount=_d(el, "ram:BasisAmount"),
        percentage=_d(el, "ram:CalculationPercent"),
        reason=_t(el, "ram:Reason"),
        reason_code=_t(el, "ram:ReasonCode"),
        tax_category=_t(el, "ram:CategoryTradeTax/ram:CategoryCode") or "S",
        tax_rate=_d(el, "ram:CategoryTradeTax/ram:RateApplicablePercent"),
    )


def _position(li) -> InvoiceLine:
    doc = li.find("ram:AssociatedDocumentLineDocument", NS)
    prod = li.find("ram:SpecifiedTradeProduct", NS)
    agr = li.find("ram:SpecifiedLineTradeAgreement", NS)
    dlv = li.find("ram:SpecifiedLineTradeDelivery", NS)
    st = li.find("ram:SpecifiedLineTradeSettlement", NS)

    qty_el = dlv.find("ram:BilledQuantity", NS) if dlv is not None else None
    line = InvoiceLine(
        line_id=_t(doc, "ram:LineID"),
        note=_t(doc, "ram:IncludedNote/ram:Content"),
        item_name=_t(prod, "ram:Name"),
        item_description=_t(prod, "ram:Description"),
        item_id=_t(prod, "ram:SellerAssignedID"),
        quantity=_d(dlv, "ram:BilledQuantity") if dlv is not None else Decimal("1"),
        unit_code=(qty_el.get("unitCode") if qty_el is not None else None) or "C62",
        line_net_amount=_d(st, "ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount"),
        tax_category=_t(st, "ram:ApplicableTradeTax/ram:CategoryCode") or "S",
        tax_rate=_d(st, "ram:ApplicableTradeTax/ram:RateApplicablePercent"),
        order_reference=_t(agr, "ram:BuyerOrderReferencedDocument/ram:LineID"),
    )
    net = agr.find("ram:NetPriceProductTradePrice", NS) if agr is not None else None
    if net is not None:
        line.unit_price = _d(net, "ram:ChargeAmount")
        bq = _d(net, "ram:BasisQuantity")
        if bq > 0:
            line.price_base_quantity = bq
    if st is not None:
        line.period_start = _datum(st, "ram:BillingSpecifiedPeriod/ram:StartDateTime")
        line.period_end = _datum(st, "ram:BillingSpecifiedPeriod/ram:EndDateTime")
        for ac in st.findall("ram:SpecifiedTradeAllowanceCharge", NS):
            line.allowances_charges.append(_nachlass(ac))
    return line


def guideline_id(xml_bytes: bytes) -> str:
    root = etree.fromstring(xml_bytes)
    return _t(root, "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID")


def parse_cii(xml_bytes: bytes, source_file: str = "") -> Invoice:
    root = etree.fromstring(xml_bytes)
    doc = root.find("rsm:ExchangedDocument", NS)
    tx = root.find("rsm:SupplyChainTradeTransaction", NS)
    agr = tx.find("ram:ApplicableHeaderTradeAgreement", NS) if tx is not None else None
    dlv = tx.find("ram:ApplicableHeaderTradeDelivery", NS) if tx is not None else None
    st = tx.find("ram:ApplicableHeaderTradeSettlement", NS) if tx is not None else None

    notes = [(_t(n, "ram:Content")) for n in (doc.findall("ram:IncludedNote", NS) if doc is not None else [])]
    inv = Invoice(
        invoice_number=_t(doc, "ram:ID"),
        invoice_date=_datum(doc, "ram:IssueDateTime") or date.today(),
        invoice_type_code=_t(doc, "ram:TypeCode") or "380",
        currency_code=_t(st, "ram:InvoiceCurrencyCode") or "EUR",
        buyer_reference=_t(agr, "ram:BuyerReference"),
        note="\n".join(n for n in notes if n),
        seller=_verkaeufer(agr),
        buyer=_kaeufer(agr),
    )
    inv.buyer.buyer_reference = inv.buyer_reference
    inv.order_reference = _t(agr, "ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID")
    inv.contract_reference = _t(agr, "ram:ContractReferencedDocument/ram:IssuerAssignedID")
    inv.project_reference = _t(agr, "ram:SpecifiedProcuringProject/ram:ID")
    inv.tax_point_date = _datum(dlv, "ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime")

    pay = PaymentInfo()
    if st is not None:
        pm = st.find("ram:SpecifiedTradeSettlementPaymentMeans", NS)
        if pm is not None:
            pay.means_code = _t(pm, "ram:TypeCode") or "58"
            pay.iban = (_t(pm, "ram:PayeePartyCreditorFinancialAccount/ram:IBANID")
                        or _t(pm, "ram:PayeePartyCreditorFinancialAccount/ram:ProprietaryID"))
            pay.bic = _t(pm, "ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID")
            pay.debited_account = _t(pm, "ram:PayerPartyDebtorFinancialAccount/ram:IBANID")
        pt = st.find("ram:SpecifiedTradePaymentTerms", NS)
        if pt is not None:
            pay.payment_terms = _t(pt, "ram:Description")
            pay.due_date = _datum(pt, "ram:DueDateDateTime")
            pay.mandate_reference = _t(pt, "ram:DirectDebitMandateID")
        pay.creditor_id = _t(st, "ram:CreditorReferenceID")
        inv.period_start = _datum(st, "ram:BillingSpecifiedPeriod/ram:StartDateTime")
        inv.period_end = _datum(st, "ram:BillingSpecifiedPeriod/ram:EndDateTime")
        for ac in st.findall("ram:SpecifiedTradeAllowanceCharge", NS):
            inv.allowances_charges.append(_nachlass(ac))
        inv.prepaid_amount = _d(st, "ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TotalPrepaidAmount")
        inv.preceding_invoice = _t(st, "ram:InvoiceReferencedDocument/ram:IssuerAssignedID")
        # Befreiungsgrund je Steuerkategorie (Anzeige / Pruefung)
    inv.payment = pay

    for li in (tx.findall("ram:IncludedSupplyChainTradeLineItem", NS) if tx is not None else []):
        inv.lines.append(_position(li))

    gid = ""
    try:
        gid = guideline_id(xml_bytes)
    except Exception:
        pass
    inv._source_file = source_file
    inv._source_format = "XRECHNUNG_CII" if "xrechnung" in gid.lower() else "ZUGFERD_CII"
    inv._received_at = datetime.now().isoformat()
    return inv
