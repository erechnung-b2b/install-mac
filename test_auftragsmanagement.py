#!/usr/bin/env python3
"""
E-Rechnungssystem – Test-Suite Auftragsmanagement (Phase 1–4)
pytest-Tests für: suppliers, transactions, doc_generator, dunning
"""
import pytest
import json
import tempfile
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from suppliers import SupplierManager
from transactions import (TransactionManager, STEP_KEYS, STEP_LABELS,
                           DOC_PREFIXES, make_position, calc_step_totals)
from dunning import DunningManager


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def supplier_mgr(tmp_dir):
    return SupplierManager(tmp_dir)


@pytest.fixture
def txn_mgr(tmp_dir):
    return TransactionManager(tmp_dir)


@pytest.fixture
def dunning_mgr(tmp_dir):
    return DunningManager(tmp_dir)


# ══════════════════════════════════════════════════════════════════════
# SUPPLIERS
# ══════════════════════════════════════════════════════════════════════

class TestSupplierCRUD:
    def test_add_supplier(self, supplier_mgr):
        s = supplier_mgr.add({"name": "Muster Bau GmbH", "city": "Brüggen"})
        assert s["name"] == "Muster Bau GmbH"
        assert s["city"] == "Brüggen"
        assert s["country"] == "DE"
        assert s["approved"] is False
        assert s["status"] == "AKTIV"
        assert len(s["id"]) == 5

    def test_add_without_name_raises(self, supplier_mgr):
        with pytest.raises(ValueError, match="Pflichtfeld"):
            supplier_mgr.add({"city": "Berlin"})

    def test_add_duplicate_returns_existing(self, supplier_mgr):
        s1 = supplier_mgr.add({"name": "Test AG"})
        s2 = supplier_mgr.add({"name": "test ag"})  # case-insensitive
        assert s1["id"] == s2["id"]

    def test_list_all(self, supplier_mgr):
        supplier_mgr.add({"name": "A GmbH"})
        supplier_mgr.add({"name": "B GmbH"})
        assert len(supplier_mgr.list_all()) == 2

    def test_get(self, supplier_mgr):
        s = supplier_mgr.add({"name": "Test"})
        found = supplier_mgr.get(s["id"])
        assert found["name"] == "Test"
        assert supplier_mgr.get("99999") is None

    def test_update(self, supplier_mgr):
        s = supplier_mgr.add({"name": "Alt"})
        updated = supplier_mgr.update(s["id"], {"city": "München"})
        assert updated["city"] == "München"

    def test_delete(self, supplier_mgr):
        s = supplier_mgr.add({"name": "Löschbar"})
        assert supplier_mgr.delete(s["id"]) is True
        assert len(supplier_mgr.list_all()) == 0
        assert supplier_mgr.delete("00000") is False

    def test_delete_all(self, supplier_mgr):
        supplier_mgr.add({"name": "A"})
        supplier_mgr.add({"name": "B"})
        count = supplier_mgr.delete_all()
        assert count == 2
        assert len(supplier_mgr.list_all()) == 0


class TestSupplierApproval:
    def test_approve(self, supplier_mgr):
        s = supplier_mgr.add({"name": "Prüf GmbH"})
        result = supplier_mgr.approve(s["id"], "rolf", "OK")
        assert result["approved"] is True
        assert result["approved_by"] == "rolf"
        assert result["approved_at"] is not None

    def test_unapprove(self, supplier_mgr):
        s = supplier_mgr.add({"name": "Prüf GmbH"})
        supplier_mgr.approve(s["id"], "rolf")
        result = supplier_mgr.unapprove(s["id"])
        assert result["approved"] is False
        assert result["approved_at"] is None

    def test_approve_nonexistent(self, supplier_mgr):
        assert supplier_mgr.approve("00000") is None


class TestSupplierCSV:
    def test_csv_import_with_header(self, supplier_mgr):
        csv = "Firma;Strasse;PLZ;Ort;Email\nA GmbH;Str.1;12345;Berlin;a@a.de\nB AG;Str.2;54321;München;b@b.de"
        result = supplier_mgr.import_csv(csv.encode("utf-8-sig"))
        assert result["imported"] == 2
        assert result["total"] == 2

    def test_csv_import_skip_duplicates(self, supplier_mgr):
        supplier_mgr.add({"name": "A GmbH"})
        csv = "Firma;Ort\nA GmbH;Berlin\nC GmbH;Hamburg"
        result = supplier_mgr.import_csv(csv.encode("utf-8-sig"))
        assert result["imported"] == 1  # nur C GmbH

    def test_csv_import_empty_name_skipped(self, supplier_mgr):
        csv = "Firma;Ort\n;Berlin\nX GmbH;Hamburg"
        result = supplier_mgr.import_csv(csv.encode("utf-8-sig"))
        assert result["imported"] == 1

    def test_csv_export(self, supplier_mgr):
        supplier_mgr.add({"name": "Export GmbH", "city": "Köln"})
        csv_str = supplier_mgr.export_csv()
        assert "Export GmbH" in csv_str
        assert "Köln" in csv_str


# ══════════════════════════════════════════════════════════════════════
# TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════

class TestTransactionCRUD:
    def test_create(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Dachsanierung"})
        assert txn["id"].startswith("V-")
        assert txn["status"] == "NEU"
        assert len(txn["steps"]) == 7
        for key in STEP_KEYS:
            assert key in txn["steps"]

    def test_list_and_filter(self, txn_mgr):
        txn_mgr.create({"subject": "A"})
        txn_mgr.create({"subject": "B"})
        assert len(txn_mgr.list_all()) == 2
        assert len(txn_mgr.list_all(status="NEU")) == 2
        assert len(txn_mgr.list_all(status="ABGESCHLOSSEN")) == 0

    def test_get(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        found = txn_mgr.get(txn["id"])
        assert found["subject"] == "Test"
        assert txn_mgr.get("V-0000-0000") is None

    def test_update(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Alt"})
        updated = txn_mgr.update(txn["id"], {"subject": "Neu"})
        assert updated["subject"] == "Neu"

    def test_delete(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Weg"})
        assert txn_mgr.delete(txn["id"]) is True
        assert len(txn_mgr.list_all()) == 0


class TestTransactionSteps:
    def test_current_step(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        assert txn_mgr.current_step(txn) == "supplier_quote"

    def test_update_step_with_positions(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        positions = [
            {"pos_nr": 1, "description": "Ziegel", "quantity": 100,
             "unit": "Stk", "unit_price": 5.0, "discount_percent": 0,
             "discount_amount": 0, "net_amount": 500, "tax_rate": 19}
        ]
        txn = txn_mgr.update_step(txn["id"], "supplier_quote",
                                   {"positions": positions, "date": "2026-04-22"})
        step = txn["steps"]["supplier_quote"]
        assert len(step["positions"]) == 1
        assert step["amount"] > 0
        assert step["date"] == "2026-04-22"
        assert step["status"] == "IN_BEARBEITUNG"


class TestTransactionApproval:
    def test_approve_first_step(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        txn = txn_mgr.approve_step(txn["id"], "supplier_quote", "rolf", "OK")
        step = txn["steps"]["supplier_quote"]
        assert step["approved"] is True
        assert step["approved_by"] == "rolf"
        assert step["status"] == "ERLEDIGT"
        assert step["reference"] is not None  # Auto-generiert

    def test_approval_order_enforced(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        # customer_quote (3.) ohne vorherige Freigabe → Fehler
        with pytest.raises(ValueError, match="muss zuerst"):
            txn_mgr.approve_step(txn["id"], "customer_quote", "rolf")

    def test_skip_enables_next(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        txn = txn_mgr.skip_step(txn["id"], "supplier_quote", "rolf", "Nicht nötig")
        assert txn["steps"]["supplier_quote"]["status"] == "UEBERSPRUNGEN"
        # Nächster Step sollte jetzt freigabefähig sein
        ok, _ = txn_mgr.can_approve(txn, "purchase_order")
        assert ok is True

    def test_unapprove_blocked_if_next_approved(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        txn = txn_mgr.approve_step(txn["id"], "supplier_quote", "rolf")
        txn = txn_mgr.approve_step(txn["id"], "purchase_order", "rolf")
        with pytest.raises(ValueError, match="bereits freigegeben"):
            txn_mgr.unapprove_step(txn["id"], "supplier_quote")

    def test_unapprove_allowed_if_next_not_approved(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Test"})
        txn = txn_mgr.approve_step(txn["id"], "supplier_quote", "rolf")
        txn = txn_mgr.unapprove_step(txn["id"], "supplier_quote")
        assert txn["steps"]["supplier_quote"]["approved"] is False

    def test_all_approved_marks_complete(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Komplett"})
        for key in STEP_KEYS:
            txn = txn_mgr.approve_step(txn["id"], key, "rolf")
        assert txn["status"] == "ABGESCHLOSSEN"


class TestTransactionDeliveries:
    def test_add_delivery(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Lieferung"})
        # Vorgänger freigeben
        for key in STEP_KEYS[:4]:
            txn = txn_mgr.approve_step(txn["id"], key, "rolf")
        txn = txn_mgr.add_delivery(txn["id"], {
            "positions": [{"pos_nr": 1, "description": "Teil A", "quantity": 50}],
            "notes": "Erstlieferung"
        })
        deliveries = txn["steps"]["delivery_note"]["deliveries"]
        assert len(deliveries) == 1
        assert deliveries[0]["id"].startswith("LIEF-")

    def test_approve_all_deliveries_approves_step(self, txn_mgr):
        txn = txn_mgr.create({"subject": "Teil"})
        for key in STEP_KEYS[:4]:
            txn = txn_mgr.approve_step(txn["id"], key, "rolf")
        txn = txn_mgr.add_delivery(txn["id"], {"positions": []})
        txn = txn_mgr.add_delivery(txn["id"], {"positions": []})
        d1 = txn["steps"]["delivery_note"]["deliveries"][0]["id"]
        d2 = txn["steps"]["delivery_note"]["deliveries"][1]["id"]
        txn = txn_mgr.approve_delivery(txn["id"], d1, "rolf")
        assert txn["steps"]["delivery_note"]["approved"] is False  # nur 1 von 2
        txn = txn_mgr.approve_delivery(txn["id"], d2, "rolf")
        assert txn["steps"]["delivery_note"]["approved"] is True


class TestTransactionTimeline:
    def test_timeline(self, txn_mgr):
        txn = txn_mgr.create({"subject": "TL"})
        txn = txn_mgr.approve_step(txn["id"], "supplier_quote", "rolf")
        events = txn_mgr.get_timeline(txn["id"])
        assert len(events) >= 2  # ERSTELLT + FREIGEGEBEN
        assert events[0]["action"] == "VORGANG_ERSTELLT"


class TestTransactionStats:
    def test_stats(self, txn_mgr):
        txn_mgr.create({"subject": "A"})
        txn_mgr.create({"subject": "B"})
        stats = txn_mgr.stats()
        assert stats["total"] == 2
        assert stats["neu"] == 2


class TestNumberSequence:
    def test_sequential(self, txn_mgr):
        n1 = txn_mgr.numbers.next("TEST")
        n2 = txn_mgr.numbers.next("TEST")
        assert n1.endswith("-0001")
        assert n2.endswith("-0002")

    def test_different_prefixes(self, txn_mgr):
        a = txn_mgr.numbers.next("A")
        b = txn_mgr.numbers.next("B")
        assert a.endswith("-0001")
        assert b.endswith("-0001")


class TestPositions:
    def test_make_position(self):
        p = make_position(1, "Ziegel", 100, "Stk", 5.50, tax_rate=19)
        assert p["net_amount"] == 550.0

    def test_make_position_with_discount(self):
        p = make_position(1, "Ziegel", 100, "Stk", 10.0, discount_percent=10)
        assert p["discount_amount"] == 100.0
        assert p["net_amount"] == 900.0

    def test_calc_step_totals(self):
        step = {
            "positions": [
                {"net_amount": 1000, "tax_rate": 19},
                {"net_amount": 500, "tax_rate": 19},
            ],
            "doc_discount_percent": 0,
            "doc_discount_amount": 0,
            "doc_surcharge_amount": 0,
        }
        totals = calc_step_totals(step)
        assert totals["net_sum"] == 1500
        assert totals["total_tax"] == 285  # 1500 * 0.19
        assert totals["total_gross"] == 1785

    def test_calc_with_doc_discount(self):
        step = {
            "positions": [{"net_amount": 1000, "tax_rate": 19}],
            "doc_discount_percent": 10,
            "doc_discount_amount": 0,
            "doc_surcharge_amount": 0,
        }
        totals = calc_step_totals(step)
        assert totals["doc_discount_amount"] == 100
        assert totals["subtotal"] == 900


# ══════════════════════════════════════════════════════════════════════
# DUNNING
# ══════════════════════════════════════════════════════════════════════

class TestDunningRules:
    def test_default_rules(self, dunning_mgr):
        rules = dunning_mgr.get_rules()
        assert len(rules["levels"]) == 3
        assert rules["levels"][0]["level"] == 1

    def test_save_and_load_rules(self, dunning_mgr):
        rules = dunning_mgr.get_rules()
        rules["levels"][0]["fee"] = 2.50
        dunning_mgr.save_rules(rules)
        loaded = dunning_mgr.get_rules()
        assert loaded["levels"][0]["fee"] == 2.50


class TestDunningCheck:
    def test_overdue_detected(self, dunning_mgr):
        today = date.today()
        invoices = [{
            "transaction_id": "V-1", "invoice_reference": "RE-1",
            "due_date": (today - timedelta(days=20)).isoformat(),
            "amount": 1000, "buyer_name": "Test", "status": "",
            "dunning_history": []
        }]
        result = dunning_mgr.check_overdue(invoices)
        assert len(result) == 1
        assert result[0]["days_overdue"] == 20
        assert result[0]["next_dunning_level"] == 1
        assert result[0]["needs_action"] is True

    def test_not_yet_due_excluded(self, dunning_mgr):
        today = date.today()
        invoices = [{
            "transaction_id": "V-1", "invoice_reference": "RE-1",
            "due_date": (today + timedelta(days=10)).isoformat(),
            "amount": 500, "buyer_name": "Test", "status": "",
            "dunning_history": []
        }]
        result = dunning_mgr.check_overdue(invoices)
        assert len(result) == 0

    def test_paid_excluded(self, dunning_mgr):
        today = date.today()
        invoices = [{
            "transaction_id": "V-1", "invoice_reference": "RE-1",
            "due_date": (today - timedelta(days=30)).isoformat(),
            "amount": 500, "buyer_name": "Test", "status": "BEZAHLT",
            "dunning_history": []
        }]
        result = dunning_mgr.check_overdue(invoices)
        assert len(result) == 0

    def test_next_level_after_first_dunning(self, dunning_mgr):
        today = date.today()
        invoices = [{
            "transaction_id": "V-1", "invoice_reference": "RE-1",
            "due_date": (today - timedelta(days=35)).isoformat(),
            "amount": 800, "buyer_name": "Test", "status": "",
            "dunning_history": [{"level": 1, "date": "2026-04-01"}]
        }]
        result = dunning_mgr.check_overdue(invoices)
        assert result[0]["last_dunning_level"] == 1
        assert result[0]["next_dunning_level"] == 2

    def test_overdue_but_below_threshold(self, dunning_mgr):
        """5 Tage überfällig aber Schwelle ist 14 → keine Aktion."""
        today = date.today()
        invoices = [{
            "transaction_id": "V-1", "invoice_reference": "RE-1",
            "due_date": (today - timedelta(days=5)).isoformat(),
            "amount": 200, "buyer_name": "Test", "status": "",
            "dunning_history": []
        }]
        result = dunning_mgr.check_overdue(invoices)
        assert len(result) == 1
        assert result[0]["needs_action"] is False

    def test_collect_from_transactions(self, dunning_mgr):
        today = date.today()
        txns = [
            {"id": "V-1", "buyer_name": "Kunde A", "steps": {
                "invoice": {"approved": True, "date": (today - timedelta(days=40)).isoformat(),
                            "due_date": (today - timedelta(days=10)).isoformat(),
                            "amount": 999, "reference": "RE-001"},
                "dunning": {"status": "OFFEN", "levels": [], "approved": False}}},
            {"id": "V-2", "buyer_name": "Kunde B", "steps": {
                "invoice": {"approved": False, "date": "", "amount": 0},
                "dunning": {"status": "OFFEN", "levels": [], "approved": False}}},
        ]
        collected = dunning_mgr.collect_invoices_from_transactions(txns)
        assert len(collected) == 1  # nur V-1 (invoice approved)
        assert collected[0]["transaction_id"] == "V-1"

    def test_overdue_summary(self, dunning_mgr):
        today = date.today()
        txns = [
            {"id": "V-1", "buyer_name": "Test", "steps": {
                "invoice": {"approved": True, "date": (today - timedelta(days=50)).isoformat(),
                            "due_date": (today - timedelta(days=20)).isoformat(),
                            "amount": 1500, "reference": "RE-1"},
                "dunning": {"status": "OFFEN", "levels": [], "approved": False}}},
        ]
        summary = dunning_mgr.get_overdue_summary(txns)
        assert summary["total_overdue"] == 1
        assert summary["needs_action"] == 1
        assert summary["total_overdue_amount"] == 1500


# ══════════════════════════════════════════════════════════════════════
# DOC_GENERATOR
# ══════════════════════════════════════════════════════════════════════

class TestDocGenerator:
    @pytest.fixture
    def doc_gen(self, tmp_dir):
        from doc_generator import DocumentGenerator
        # Mandant-Settings
        Path(tmp_dir, "mandant_settings.json").write_text(json.dumps({
            "name": "Test GmbH", "street": "Teststr. 1",
            "post_code": "12345", "city": "Berlin",
            "email": "test@test.de", "vat_id": "DE123",
            "iban": "DE89370400440532013000", "bic": "COBADEFF",
            "contact_name": "Max Test", "contact_phone": "030-12345",
        }, ensure_ascii=False))
        return DocumentGenerator(tmp_dir)

    def test_generate_customer_quote(self, doc_gen):
        doc = doc_gen.generate(
            doc_type="customer_quote",
            recipient={"name": "Kunde AG", "street": "Abc 1", "post_code": "54321", "city": "München"},
            positions=[{"pos_nr": 1, "description": "Leistung A", "quantity": 2,
                        "unit": "Std", "unit_price": 100, "net_amount": 200, "tax_rate": 19}],
            step={"doc_discount_amount": 0, "doc_surcharge_amount": 0},
            reference="ANG-K-2026-0001",
            doc_date="2026-04-22",
        )
        assert doc["filename"] == "ANG-K-2026-0001.pdf"
        assert doc["size_bytes"] > 1000
        assert Path(doc["filepath"]).exists()

    def test_generate_delivery_note_no_prices(self, doc_gen):
        doc = doc_gen.generate(
            doc_type="delivery_note",
            recipient={"name": "Kunde"},
            positions=[{"pos_nr": 1, "description": "Ware", "quantity": 10, "unit": "Stk"}],
            step={},
            reference="LIEF-2026-0001",
        )
        assert doc["size_bytes"] > 1000

    def test_generate_dunning(self, doc_gen):
        doc = doc_gen.generate(
            doc_type="dunning",
            recipient={"name": "Säumiger Kunde"},
            positions=[],
            step={},
            reference="MAH-2026-0001",
            dunning_level=2,
            original_invoice_ref="RE-2026-0001",
            original_invoice_amount=1500,
            dunning_fee=5.0,
        )
        assert doc["size_bytes"] > 1000

    def test_document_index(self, doc_gen):
        doc_gen.generate(
            doc_type="purchase_order",
            recipient={"name": "Lieferant"},
            positions=[{"pos_nr": 1, "description": "X", "quantity": 1,
                        "unit": "Stk", "unit_price": 50, "net_amount": 50}],
            step={},
            reference="BEST-2026-0001",
            transaction_id="V-2026-0042",
        )
        docs = doc_gen.list_docs("V-2026-0042")
        assert len(docs) == 1
        assert docs[0]["transaction_id"] == "V-2026-0042"

    def test_all_doc_types(self, doc_gen):
        """Jeder Dokumenttyp muss ohne Fehler erzeugt werden."""
        types = ["supplier_quote", "purchase_order", "customer_quote",
                 "order_intake", "delivery_note"]
        for dt in types:
            doc = doc_gen.generate(
                doc_type=dt,
                recipient={"name": "Test"},
                positions=[{"pos_nr": 1, "description": "Pos", "quantity": 1,
                            "unit": "Stk", "unit_price": 100, "net_amount": 100, "tax_rate": 19}],
                step={"doc_discount_amount": 0, "doc_surcharge_amount": 0},
                reference=f"TEST-{dt}",
            )
            assert doc["size_bytes"] > 0, f"Fehler bei {dt}"
