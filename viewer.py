"""
E-Rechnungssystem – Rechnungs-Viewer
FR-220..FR-240: Menschenlesbare Darstellung, Pflichtfelder, Fehlermarkierung
"""
from __future__ import annotations
from models import Invoice
from validator import ValidationReport, Severity


def render_invoice(inv: Invoice, report: ValidationReport = None) -> str:
    """Erzeugt eine menschenlesbare Textdarstellung einer E-Rechnung."""
    lines = []

    # Fehler-Indikatoren
    errors = {}
    warnings = {}
    if report:
        for iss in report.issues:
            target = errors if iss.severity == Severity.ERROR else warnings
            if iss.field:
                target[iss.field] = iss.message

    def _mark(field_id: str, value: str) -> str:
        if field_id in errors:
            return f"{value}  ✗ FEHLER: {errors[field_id]}"
        if field_id in warnings:
            return f"{value}  ⚠ {warnings[field_id]}"
        return value

    def _missing(field_id: str, label: str) -> str:
        if field_id in errors:
            return f"  {label:<30} ✗ FEHLT – {errors[field_id]}"
        return f"  {label:<30} (nicht angegeben)"

    # Header
    status_icon = "✓" if (report and report.is_valid) else "✗" if report else "?"
    lines.append(f"{'═' * 70}")
    lines.append(f"  E-RECHNUNG  [{status_icon}]  {inv.invoice_number or '(keine Nr.)'}")
    lines.append(f"{'═' * 70}")

    # Kopfdaten
    lines.append(f"\n  ── Kopfdaten ──")
    lines.append(f"  {'Rechnungsnummer:':<30} {_mark('BT-1', inv.invoice_number or '—')}")
    lines.append(f"  {'Rechnungsdatum:':<30} {_mark('BT-2', str(inv.invoice_date))}")
    lines.append(f"  {'Typ:':<30} {_mark('BT-3', inv.invoice_type_code)}")
    lines.append(f"  {'Währung:':<30} {_mark('BT-5', inv.currency_code)}")
    ref = inv.buyer_reference or inv.buyer.buyer_reference
    lines.append(f"  {'Buyer Reference:':<30} {_mark('BT-10', ref or '—')}")
    if inv.order_reference:
        lines.append(f"  {'Bestellreferenz:':<30} {inv.order_reference}")
    if inv.contract_reference:
        lines.append(f"  {'Vertragsreferenz:':<30} {inv.contract_reference}")
    if inv.period_start or inv.period_end:
        lines.append(f"  {'Leistungszeitraum:':<30} {inv.period_start or '?'} – {inv.period_end or '?'}")
    if inv.note:
        lines.append(f"  {'Bemerkung:':<30} {inv.note}")
    if inv.preceding_invoice:
        lines.append(f"  {'Bezug auf Rechnung:':<30} {inv.preceding_invoice}")

    # Verkäufer
    lines.append(f"\n  ── Verkäufer ──")
    if inv.seller.name:
        lines.append(f"  {'Name:':<30} {_mark('BT-27', inv.seller.name)}")
    else:
        lines.append(_missing('BT-27', 'Name:'))
    lines.append(f"  {'Adresse:':<30} {inv.seller.address.street}")
    lines.append(f"  {'':<30} {inv.seller.address.post_code} {inv.seller.address.city}")
    lines.append(f"  {'E-Adresse:':<30} {_mark('BT-34', inv.seller.electronic_address or '—')}")
    if inv.seller.vat_id:
        lines.append(f"  {'USt-ID:':<30} {inv.seller.vat_id}")
    if inv.seller.tax_registration_id:
        lines.append(f"  {'Steuernummer:':<30} {inv.seller.tax_registration_id}")
    lines.append(f"  {'Kontakt:':<30} {inv.seller.contact.name}, {inv.seller.contact.telephone}")
    lines.append(f"  {'':<30} {inv.seller.contact.email}")

    # Käufer
    lines.append(f"\n  ── Käufer ──")
    if inv.buyer.name:
        lines.append(f"  {'Name:':<30} {_mark('BT-44', inv.buyer.name)}")
    else:
        lines.append(_missing('BT-44', 'Name:'))
    lines.append(f"  {'Adresse:':<30} {inv.buyer.address.street}")
    lines.append(f"  {'':<30} {inv.buyer.address.post_code} {inv.buyer.address.city}")
    lines.append(f"  {'E-Adresse:':<30} {_mark('BT-49', inv.buyer.electronic_address or '—')}")

    # Zahlung
    lines.append(f"\n  ── Zahlung ──")
    lines.append(f"  {'Zahlungsart:':<30} {inv.payment.means_code}")
    if inv.payment.iban:
        lines.append(f"  {'IBAN:':<30} {_mark('BT-84', inv.payment.iban)}")
    if inv.payment.due_date:
        lines.append(f"  {'Fällig:':<30} {inv.payment.due_date}")
    if inv.payment.payment_terms:
        lines.append(f"  {'Bedingungen:':<30} {inv.payment.payment_terms}")

    # Positionen
    lines.append(f"\n  ── Positionen ──")
    lines.append(f"  {'Nr':<5} {'Bezeichnung':<30} {'Menge':>8} {'EP netto':>10} {'Netto':>12} {'USt%':>6}")
    lines.append(f"  {'─' * 73}")
    for line in inv.lines:
        lines.append(
            f"  {line.line_id:<5} {line.item_name[:30]:<30} {line.quantity:>8} "
            f"{line.unit_price:>10.2f} {line.line_net_amount:>12.2f} {line.tax_rate:>5.1f}%"
        )
        if line.item_description:
            lines.append(f"        {line.item_description[:60]}")

    # Nachlässe/Zuschläge
    if inv.allowances_charges:
        lines.append(f"\n  ── Nachlässe/Zuschläge ──")
        for ac in inv.allowances_charges:
            typ = "Zuschlag" if ac.is_charge else "Nachlass"
            lines.append(f"  {typ}: {ac.amount:.2f} {inv.currency_code}  {ac.reason}")

    # Summen
    lines.append(f"\n  ── Summen ──")
    lines.append(f"  {'Summe Positionen netto:':<30} {inv.sum_line_net():>12.2f} {inv.currency_code}")
    if inv.sum_allowances():
        lines.append(f"  {'./. Nachlässe:':<30} {inv.sum_allowances():>12.2f} {inv.currency_code}")
    if inv.sum_charges():
        lines.append(f"  {'+ Zuschläge:':<30} {inv.sum_charges():>12.2f} {inv.currency_code}")
    lines.append(f"  {'Netto:':<30} {inv.tax_exclusive_amount():>12.2f} {inv.currency_code}")
    for st in inv.compute_tax_subtotals():
        lines.append(f"  {'USt ' + str(st.rate) + '%:':<30} {st.tax_amount:>12.2f} {inv.currency_code}  (auf {st.taxable_amount:.2f})")
    lines.append(f"  {'Brutto:':<30} {inv.tax_inclusive_amount():>12.2f} {inv.currency_code}")
    lines.append(f"  {'═' * 44}")
    lines.append(f"  {'ZAHLBETRAG:':<30} {inv.amount_due():>12.2f} {inv.currency_code}")

    # Validierungsstatus
    if report:
        lines.append(f"\n  ── Validierung ──")
        lines.append(f"  Status: {'GÜLTIG ✓' if report.is_valid else 'UNGÜLTIG ✗'}")
        lines.append(f"  Fehler: {report.error_count}  |  Warnungen: {report.warning_count}")
        if report.issues:
            for iss in report.issues:
                px = {"ERROR": "✗", "WARNING": "⚠", "INFO": "ℹ"}[iss.severity.value]
                lines.append(f"    {px} {iss.message}")

    # Workflow
    if inv.status:
        lines.append(f"\n  ── Workflow ──")
        lines.append(f"  Status: {inv.status}")
        if inv.assigned_to:
            lines.append(f"  Zugewiesen: {inv.assigned_to}")

    # Audit
    if inv.audit_trail:
        lines.append(f"\n  ── Audit-Trail ──")
        for evt in inv.audit_trail[-5:]:  # Letzte 5 Einträge
            lines.append(f"  [{evt.timestamp[:19]}] {evt.event_type}: {evt.comment}")

    lines.append(f"\n{'═' * 70}")
    return "\n".join(lines)
