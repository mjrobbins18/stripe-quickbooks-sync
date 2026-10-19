"""Offline Stripe backed by a JSON fixture shaped like real Stripe API responses."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..models import BalanceTransaction, Charge, Payout, Refund, StripeCustomer


class FakeStripeSource:
    """Serves Stripe objects from a dict like the one ``sample_data.generate_stripe`` builds.

    Fixture keys mirror Stripe list endpoints (``customers``, ``charges``, ``refunds``,
    ``balance_transactions``, ``payouts``). ``payout_balance_transactions`` stands in for
    ``GET /v1/balance_transactions?payout=po_...``.
    """

    def __init__(self, data: dict):
        self.data = data
        self._customers = {c["id"]: c for c in data["customers"]}
        self._charges = {c["id"]: c for c in data["charges"]}
        self._txns = {t["id"]: t for t in data["balance_transactions"]}

    @classmethod
    def from_file(cls, path: str | Path) -> FakeStripeSource:
        return cls(json.loads(Path(path).read_text()))

    def list_charges(self, since: date):
        charges = (Charge.from_stripe(c) for c in self.data["charges"])
        return sorted((c for c in charges if c.created >= since), key=lambda c: (c.created, c.id))

    def get_charge(self, charge_id: str) -> Charge:
        return Charge.from_stripe(self._charges[charge_id])

    def list_refunds(self, since: date):
        refunds = (Refund.from_stripe(r) for r in self.data["refunds"])
        return sorted((r for r in refunds if r.created >= since), key=lambda r: (r.created, r.id))

    def list_payouts(self, since: date):
        payouts = (Payout.from_stripe(p) for p in self.data["payouts"])
        return sorted(
            (p for p in payouts if p.arrival_date >= since), key=lambda p: (p.arrival_date, p.id)
        )

    def list_payout_transactions(self, payout_id: str) -> list[BalanceTransaction]:
        ids = self.data["payout_balance_transactions"].get(payout_id, [])
        return [BalanceTransaction.from_stripe(self._txns[i]) for i in ids]

    def get_balance_transaction(self, txn_id: str) -> BalanceTransaction:
        return BalanceTransaction.from_stripe(self._txns[txn_id])

    def get_customer(self, customer_id: str) -> StripeCustomer:
        return StripeCustomer.from_stripe(self._customers[customer_id])
