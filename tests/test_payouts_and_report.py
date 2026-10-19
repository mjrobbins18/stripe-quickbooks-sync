from datetime import date

from conftest import CHECKING, CLEARING, Harness
from stripe_qb_sync.engine import BOOKABLE_TYPES
from stripe_qb_sync.report import IN_TRANSIT, MISMATCH, MISSING, NOT_SYNCED, OK, build_report

D = date(2026, 9, 3)


def report(h, since=date(2026, 9, 1)):
    return build_report(h.source, h.qb, h.ledger, since)


def two_clean_payouts(stripe):
    cus = stripe.customer("Caleb Lindqvist", "caleb@example.com")
    a, b, c = (stripe.charge(n, D, cus) for n in (4500, 12000, 6500))
    stripe.payout([a, b], date(2026, 9, 7))
    stripe.payout([c], date(2026, 9, 14))
    return Harness(stripe.source())


def test_clean_books_reconcile(stripe):
    h = two_clean_payouts(stripe)
    h.sync()
    r = report(h)
    assert [c.status for c in r.checks] == [OK, OK]
    assert r.problems == []
    assert "reconcile to the cent" in r.render()
    assert h.account_balance(CLEARING) == 0


def test_partial_payout_window_still_deposits_the_full_payout(stripe):
    """A payout arriving inside --since can settle charges from before it."""
    cus = stripe.customer("Greta Yilmaz", "greta@example.com")
    old = stripe.charge(10000, date(2026, 8, 30), cus)
    new = stripe.charge(5000, date(2026, 9, 1), cus)
    po = stripe.payout([old, new], date(2026, 9, 3))
    h = Harness(stripe.source())
    h.sync(since=date(2026, 9, 1))

    [deposit] = h.created("Deposit")
    assert round(deposit["TotalAmt"] * 100) == po["amount"]  # matches the bank, to the cent
    [check] = report(h).checks
    assert check.status == OK
    assert any(old["id"] in n and "no sales receipt" in n for n in check.notes)


def test_charges_not_yet_paid_out_stay_in_clearing(stripe):
    cus = stripe.customer("Nadia Whitlock", "nadia@example.com")
    paid = stripe.charge(10000, D, cus)
    stripe.charge(5000, date(2026, 9, 29), cus)  # still in Stripe's pending balance
    stripe.payout([paid], date(2026, 9, 7))
    h = Harness(stripe.source())
    h.sync()

    assert len(h.created("Deposit")) == 1
    assert h.account_balance(CLEARING) == 5000 - (round(5000 * 0.029) + 30)


def test_in_transit_payout_is_deposited_once_it_lands(stripe):
    cus = stripe.customer("Ravi Delgado", "ravi@example.com")
    po = stripe.payout([stripe.charge(10000, D, cus)], date(2026, 9, 7), status="in_transit")
    h = Harness(stripe.source())
    h.sync()
    assert h.created("Deposit") == []
    assert [c.status for c in report(h).checks] == [IN_TRANSIT]
    assert report(h).problems == []

    po["status"] = "paid"
    h.sync()
    assert len(h.created("Deposit")) == 1
    assert [c.status for c in report(h).checks] == [OK]


def test_unbooked_adjustment_is_flagged_as_mismatch(stripe):
    cus = stripe.customer("Marcus Rhee", "marcus@example.com")
    ch = stripe.charge(20000, D, cus)
    adj = stripe.adjustment(-8000, date(2026, 9, 5), "Chargeback withholding for ch_x")
    stripe.payout([ch, adj], date(2026, 9, 7))
    h = Harness(stripe.source())
    result = h.sync()

    assert any(adj["id"] in w for w in result.warnings)
    [check] = report(h).checks
    assert check.status == MISMATCH
    assert check.difference == 8000
    assert any("Unbooked adjustment" in n and adj["id"] in n for n in check.notes)


def test_deposit_edited_in_quickbooks_is_flagged(stripe):
    h = two_clean_payouts(stripe)
    h.sync()
    deposit = h.created("Deposit")[0]
    deposit["Line"][0]["Amount"] = round(deposit["Line"][0]["Amount"] - 10, 2)

    check = report(h).checks[0]
    assert check.status == MISMATCH
    assert check.difference == -1000
    assert any("edited in QuickBooks" in n for n in check.notes)


def test_deleted_deposit_is_flagged_missing(stripe):
    h = two_clean_payouts(stripe)
    h.sync()
    deposit = h.created("Deposit")[1]
    del h.qb.entities["Deposit"][deposit["Id"]]
    assert [c.status for c in report(h).checks] == [OK, MISSING]


def test_payout_never_synced_is_flagged(stripe):
    h = two_clean_payouts(stripe)
    h.sync(since=date(2026, 9, 10))  # only picks up the second payout
    r = report(h)
    assert [c.status for c in r.checks] == [NOT_SYNCED, OK]
    assert "1 payout(s) need attention" in r.render()


def test_sample_month_reconciles_except_the_chargeback(sample):
    sample.sync()
    r = report(sample)
    statuses = [c.status for c in r.checks]
    assert statuses.count(OK) == 2 and statuses.count(IN_TRANSIT) == 1
    [bad] = r.problems
    assert bad.status == MISMATCH and bad.difference == 8000  # $65 class + $15 dispute fee
    assert "Chargeback withholding" in bad.notes[0]


def test_sample_clearing_account_equals_money_still_at_stripe(sample):
    sample.sync()
    src = sample.source
    deposited = set()
    for po in src.list_payouts(date(2026, 1, 1)):
        if po.status == "paid":
            deposited.update(t.id for t in src.list_payout_transactions(po.id))
    expected = sum(
        t["net"] for t in src.data["balance_transactions"]
        if t["type"] in BOOKABLE_TYPES and t["id"] not in deposited
    )
    assert expected > 0
    assert sample.account_balance(CLEARING) == expected


def test_sample_checking_receives_exactly_the_deposits(sample):
    sample.sync()
    deposits = sum(round(d["TotalAmt"] * 100) for d in sample.created("Deposit"))
    seed_deposit = 36000
    assert sample.account_balance(CHECKING) == deposits + seed_deposit
