from datetime import date

import pytest

from conftest import Harness
from stripe_qb_sync.engine import QuickBooksSetup, SetupError, SyncEngine
from stripe_qb_sync.ledger import Ledger
from stripe_qb_sync.sinks.fake import FakeQuickBooks

D = date(2026, 9, 3)


def test_matches_existing_customer_by_email_ignoring_case(stripe):
    stripe.charge(4500, D, stripe.customer("Dana Whitfield", "Dana.Whitfield@Example.com"))
    h = Harness(stripe.source())
    customers_before = h.count("Customer")
    result = h.sync()

    assert h.count("Customer") == customers_before
    assert h.created("SalesReceipt")[0]["CustomerRef"]["value"] == "31"
    assert result.counts["customers_matched"] == 1


def test_same_name_different_email_gets_a_distinct_customer(stripe):
    stripe.charge(4500, D, stripe.customer("Sam Patel", "sam.patel@example.net"))
    h = Harness(stripe.source())
    h.sync()

    ref = h.created("SalesReceipt")[0]["CustomerRef"]
    assert ref["value"] != "32"  # not the existing Sam Patel (example.org)
    assert h.qb.get("Customer", ref["value"])["DisplayName"] == "Sam Patel (sam.patel@example.net)"


def test_customer_created_once_and_reused(stripe):
    cus = stripe.customer("June Sorensen", "june@example.com")
    for _ in range(3):
        stripe.charge(4500, D, cus)
    h = Harness(stripe.source())
    before = h.count("Customer")
    h.sync()

    assert h.count("Customer") == before + 1
    assert len({r["CustomerRef"]["value"] for r in h.created("SalesReceipt")}) == 1


def test_guest_checkouts_are_matched_on_billing_email(stripe):
    stripe.charge(4500, D, guest=("Quinn Kowalski", "quinn@example.com"))
    stripe.charge(3200, D, guest=("Quinn Kowalski", "QUINN@example.com"))
    h = Harness(stripe.source())
    before = h.count("Customer")
    h.sync()

    assert h.count("Customer") == before + 1


def test_charge_with_invoice_metadata_pays_the_invoice(stripe):
    cus = stripe.customer("Ferncliff Coffee Co.", "ap@ferncliff.example.com")
    stripe.charge(48000, D, cus, metadata={"qbo_invoice": "1043"})
    h = Harness(stripe.source())
    result = h.sync()

    assert h.created("SalesReceipt") == []  # revenue was already booked on the invoice
    [payment] = h.created("Payment")
    assert payment["Line"][0]["LinkedTxn"] == [{"TxnId": "42", "TxnType": "Invoice"}]
    assert h.qb.find_invoice_by_doc_number("1043")["Balance"] == 0
    assert len(h.created("Purchase")) == 1  # the card fee is still an expense
    assert result.counts["invoice_payments"] == 1


def test_unknown_or_overpaid_invoice_is_flagged_not_guessed(stripe):
    cus = stripe.customer("Bluebird Bakery", "orders@bluebird.example.com")
    stripe.charge(10000, D, cus, metadata={"qbo_invoice": "9999"})
    stripe.charge(99900, D, cus, metadata={"qbo_invoice": "1042"})  # open balance is $275
    h = Harness(stripe.source())
    result = h.sync()

    assert h.created("Payment") == [] and h.created("SalesReceipt") == []
    assert len(result.warnings) == 2
    assert h.qb.find_invoice_by_doc_number("1042")["Balance"] == 275.0


def test_missing_quickbooks_setup_is_a_clear_error(stripe):
    stripe.charge(4500, D, stripe.customer("Elena Fairbanks", "elena@example.com"))
    qb = FakeQuickBooks.from_seed({"Account": [{"Id": "1", "Name": "Business Checking", "AccountType": "Bank"}]})
    with pytest.raises(SetupError, match="Stripe Clearing"):
        SyncEngine(stripe.source(), qb, Ledger()).run(D)


def test_account_names_are_configurable(stripe):
    stripe.charge(4500, D, stripe.customer("Elena Fairbanks", "elena@example.com"))
    h = Harness(stripe.source())
    h.qb.get("Account", "4")["Name"] = "Merchant Fees"
    SyncEngine(h.source, h.qb, h.ledger, QuickBooksSetup(fee_account="Merchant Fees")).run(D)
    assert len(h.created("Purchase")) == 1
