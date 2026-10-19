"""Real Stripe adapter (reference implementation).

Not exercised by the offline test suite and never called by the demo. It is the drop-in
replacement for ``FakeStripeSource`` — see README → "Going live".

Environment:
    STRIPE_API_KEY       restricted or secret key; must be a *test* key (sk_test_/rk_test_)
    STRIPE_ALLOW_LIVE=1  required on purpose before a live key is accepted
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timezone

from ..models import BalanceTransaction, Charge, Payout, Refund, StripeCustomer


def _ts(d: date) -> int:
    return int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp())


def _d(obj) -> dict:
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


class StripeApiSource:
    def __init__(self, api_key: str | None = None):
        import stripe  # optional dependency: `pip install -e ".[live]"`

        key = api_key or os.environ["STRIPE_API_KEY"]
        if not key.startswith(("sk_test_", "rk_test_")) and os.environ.get("STRIPE_ALLOW_LIVE") != "1":
            raise RuntimeError("Refusing a live Stripe key without STRIPE_ALLOW_LIVE=1")
        stripe.api_key = key
        self.stripe = stripe

    def list_charges(self, since: date):
        pages = self.stripe.Charge.list(created={"gte": _ts(since)}, limit=100)
        charges = [Charge.from_stripe(_d(c)) for c in pages.auto_paging_iter()]
        return sorted(charges, key=lambda c: (c.created, c.id))

    def get_charge(self, charge_id: str) -> Charge:
        return Charge.from_stripe(_d(self.stripe.Charge.retrieve(charge_id)))

    def list_refunds(self, since: date):
        pages = self.stripe.Refund.list(created={"gte": _ts(since)}, limit=100)
        refunds = [Refund.from_stripe(_d(r)) for r in pages.auto_paging_iter()]
        return sorted(refunds, key=lambda r: (r.created, r.id))

    def list_payouts(self, since: date):
        pages = self.stripe.Payout.list(arrival_date={"gte": _ts(since)}, limit=100)
        payouts = [Payout.from_stripe(_d(p)) for p in pages.auto_paging_iter()]
        return sorted(payouts, key=lambda p: (p.arrival_date, p.id))

    def list_payout_transactions(self, payout_id: str) -> list[BalanceTransaction]:
        # Automatic payouts only: Stripe can itemize which balance transactions a payout settled.
        pages = self.stripe.BalanceTransaction.list(payout=payout_id, limit=100)
        txns = [BalanceTransaction.from_stripe(_d(t)) for t in pages.auto_paging_iter()]
        return [t for t in txns if t.type != "payout"]

    def get_balance_transaction(self, txn_id: str) -> BalanceTransaction:
        return BalanceTransaction.from_stripe(_d(self.stripe.BalanceTransaction.retrieve(txn_id)))

    def get_customer(self, customer_id: str) -> StripeCustomer:
        return StripeCustomer.from_stripe(_d(self.stripe.Customer.retrieve(customer_id)))
