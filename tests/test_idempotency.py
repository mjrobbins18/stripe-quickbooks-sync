from datetime import date

import pytest

from conftest import CLEARING
from stripe_qb_sync.engine import SyncEngine

ENTITIES = ["Customer", "SalesReceipt", "Payment", "Purchase", "RefundReceipt", "Deposit"]


def snapshot(h):
    return {e: h.count(e) for e in ENTITIES}


def test_rerun_creates_nothing(sample):
    first = sample.sync()
    before, calls = snapshot(sample), sample.qb.create_calls

    second = sample.sync()

    assert snapshot(sample) == before
    assert sample.qb.create_calls == calls
    assert second.counts["sales_receipts"] == second.counts["deposits"] == 0
    assert second.counts["already_synced"] == (
        first.counts["sales_receipts"] + first.counts["invoice_payments"]
        + first.counts["refund_receipts"] + first.counts["deposits"]
    )


def test_overlapping_windows_never_double_post(sample):
    sample.sync(since=date(2026, 9, 15))
    sample.sync(since=date(2026, 9, 1))
    sample.sync(since=date(2026, 9, 20))

    notes = [r["PrivateNote"] for e in ("SalesReceipt", "Payment", "RefundReceipt", "Purchase", "Deposit")
             for r in sample.created(e)]
    assert len(notes) == len(set(notes))
    charges = [c for c in sample.source.list_charges(date(2026, 9, 1)) if c.status == "succeeded"]
    assert len(sample.created("SalesReceipt")) + len(sample.created("Payment")) == len(charges)


def test_lost_ledger_is_caught_by_quickbooks_request_ids(sample):
    sample.sync()
    before = snapshot(sample)

    sample.ledger.clear()  # e.g. the ledger file was deleted
    sample.sync()

    assert snapshot(sample) == before


def test_crash_mid_run_then_retry_finishes_without_duplicates(sample):
    real_create, calls = sample.qb.create, {"n": 0}

    def flaky_create(entity, body, request_id):
        calls["n"] += 1
        if calls["n"] == 40:
            raise ConnectionError("QuickBooks timed out")
        return real_create(entity, body, request_id)

    sample.qb.create = flaky_create
    with pytest.raises(ConnectionError):
        sample.sync()
    sample.qb.create = real_create
    sample.sync()

    clean = type(sample)(sample.source)
    clean.sync()
    assert snapshot(sample) == snapshot(clean)
    assert sample.account_balance(CLEARING) == clean.account_balance(CLEARING)


def test_engine_keys_every_record_on_a_stripe_id(sample):
    sample.sync()
    kinds = {k: sample.ledger.count(k) for k in ("charge", "fee", "refund", "payout")}
    assert kinds["charge"] == len(sample.created("SalesReceipt")) + len(sample.created("Payment"))
    assert kinds["fee"] == len(sample.created("Purchase"))
    assert kinds["refund"] == len(sample.created("RefundReceipt"))
    assert kinds["payout"] == len(sample.created("Deposit"))
