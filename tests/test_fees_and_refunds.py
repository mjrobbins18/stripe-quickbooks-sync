from datetime import date

from conftest import CLEARING, FEES, Harness

D = date(2026, 9, 3)


def test_sales_receipt_is_gross_and_fee_is_a_separate_expense(stripe):
    cus = stripe.customer("Avery Holloway", "avery@example.com")
    ch = stripe.charge(10000, D, cus)  # $100.00, fee 2.9% + 30c = $3.20
    h = Harness(stripe.source())
    h.sync()

    [receipt] = h.created("SalesReceipt")
    assert receipt["TotalAmt"] == 100.00
    assert receipt["DepositToAccountRef"]["value"] == CLEARING
    assert ch["id"] in receipt["PrivateNote"]

    [fee] = h.created("Purchase")
    assert fee["TotalAmt"] == 3.20
    assert fee["AccountRef"]["value"] == CLEARING
    assert fee["EntityRef"]["name"] == "Stripe"
    assert fee["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] == FEES

    assert h.account_balance(CLEARING) == 10000 - 320  # Stripe Clearing holds the net


def test_fee_rounding_matches_stripe_to_the_cent(stripe):
    cus = stripe.customer("Priya Rhee", "priya@example.com")
    stripe.charge(4500, D, cus)  # 130.5c -> Stripe's balance txn says 160 total
    stripe.charge(3333, D, cus, fee=127)  # whatever Stripe reports is what we book
    h = Harness(stripe.source())
    h.sync()
    assert sorted(p["TotalAmt"] for p in h.created("Purchase")) == [1.27, 1.60]


def test_zero_fee_charge_records_no_expense(stripe):
    stripe.charge(5000, D, stripe.customer("Theo Vance", "theo@example.com"), fee=0)
    h = Harness(stripe.source())
    h.sync()
    assert h.created("Purchase") == []
    assert len(h.created("SalesReceipt")) == 1


def test_deposit_lines_use_net_amounts(stripe):
    cus = stripe.customer("Lena Mercer", "lena@example.com")
    a, b = stripe.charge(10000, D, cus), stripe.charge(2500, D, cus)
    po = stripe.payout([a, b], date(2026, 9, 7))
    h = Harness(stripe.source())
    h.sync()

    [deposit] = h.created("Deposit")
    assert [line["Amount"] for line in deposit["Line"]] == [96.80, 23.98]
    assert deposit["TotalAmt"] * 100 == po["amount"]
    assert h.account_balance(CLEARING) == 0  # everything paid out


def test_full_refund(stripe):
    cus = stripe.customer("Owen Okafor", "owen@example.com")
    ch = stripe.charge(24000, D, cus, description="Beginner wheel course (6 weeks)")
    stripe.refund(ch, 24000, date(2026, 9, 4))
    h = Harness(stripe.source())
    h.sync()

    [refund] = h.created("RefundReceipt")
    [receipt] = h.created("SalesReceipt")
    assert refund["TotalAmt"] == 240.00
    assert refund["CustomerRef"]["value"] == receipt["CustomerRef"]["value"]
    assert refund["DepositToAccountRef"]["value"] == CLEARING
    assert refund["Line"][0]["Description"].startswith("Full refund")
    # The Stripe fee is not returned on refunds, so the studio is out exactly the fee.
    assert h.account_balance(CLEARING) == -round(24000 * 0.029) - 30


def test_partial_refund(stripe):
    cus = stripe.customer("Iris Nakamura", "iris@example.com")
    ch = stripe.charge(6400, D, cus, description="Handmade mug x2")
    stripe.refund(ch, 3200, date(2026, 9, 5))
    h = Harness(stripe.source())
    h.sync()

    [refund] = h.created("RefundReceipt")
    assert refund["TotalAmt"] == 32.00
    assert refund["Line"][0]["Description"] == "Partial refund: Handmade mug x2"


def test_refund_reduces_the_payout_deposit(stripe):
    cus = stripe.customer("Rosa Castillo", "rosa@example.com")
    ch1, ch2 = stripe.charge(10000, D, cus), stripe.charge(5000, D, cus)
    re = stripe.refund(ch2, 5000, date(2026, 9, 4))
    po = stripe.payout([ch1, ch2, re], date(2026, 9, 7))
    h = Harness(stripe.source())
    h.sync()

    [deposit] = h.created("Deposit")
    assert deposit["Line"][-1]["Amount"] == -50.00
    assert round(deposit["TotalAmt"] * 100) == po["amount"]


def test_refund_of_charge_before_the_sync_window_is_still_recorded(stripe):
    cus = stripe.customer("Felix Asante", "felix@example.com")
    old = stripe.charge(8500, date(2026, 8, 20), cus)
    stripe.refund(old, 8500, date(2026, 9, 2))
    h = Harness(stripe.source())
    result = h.sync(since=date(2026, 9, 1))

    assert h.created("SalesReceipt") == []
    assert len(h.created("RefundReceipt")) == 1
    assert any("never synced" in w for w in result.warnings)


def test_failed_charges_are_ignored(stripe):
    cus = stripe.customer("Malik Brennan", "malik@example.com")
    stripe.charge(8500, D, cus, status="failed")
    h = Harness(stripe.source())
    result = h.sync()
    assert h.created("SalesReceipt") == [] and h.created("Purchase") == []
    assert result.counts["ignored_charges"] == 1
