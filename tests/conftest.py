from __future__ import annotations

import itertools
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from stripe_qb_sync.engine import SyncEngine
from stripe_qb_sync.ledger import Ledger
from stripe_qb_sync.sample_data import generate_stripe, quickbooks_seed
from stripe_qb_sync.sinks.fake import FakeQuickBooks
from stripe_qb_sync.sources.fake import FakeStripeSource


def ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 15, tzinfo=timezone.utc).timestamp())


class StripeBuilder:
    """Hand-built Stripe fixtures for focused scenarios with readable ids and round numbers."""

    def __init__(self):
        self.data = {"customers": [], "charges": [], "refunds": [], "balance_transactions": [],
                     "payouts": [], "payout_balance_transactions": {}}
        self._n = itertools.count(1)

    def _id(self, prefix):
        return f"{prefix}_{next(self._n):03d}"

    def customer(self, name, email):
        c = {"id": self._id("cus"), "name": name, "email": email, "created": ts(date(2026, 8, 1))}
        self.data["customers"].append(c)
        return c

    def txn(self, type_, amount, fee, source, day, description=""):
        t = {"id": self._id("txn"), "type": type_, "amount": amount, "fee": fee, "net": amount - fee,
             "source": source, "created": ts(day), "description": description}
        self.data["balance_transactions"].append(t)
        return t

    def charge(self, amount, day, customer=None, fee=None, guest=None, status="succeeded", metadata=None,
               description="Drop-in wheel class"):
        ch = {"id": self._id("ch"), "amount": amount, "currency": "usd", "status": status, "created": ts(day),
              "description": description, "customer": customer["id"] if customer else None,
              "billing_details": {"name": guest[0] if guest else None, "email": guest[1] if guest else None},
              "metadata": metadata or {}, "balance_transaction": None}
        if status == "succeeded":
            fee = round(amount * 0.029) + 30 if fee is None else fee
            ch["balance_transaction"] = self.txn("charge", amount, fee, ch["id"], day, description)["id"]
        self.data["charges"].append(ch)
        return ch

    def refund(self, charge, amount, day):
        re = {"id": self._id("re"), "charge": charge["id"], "amount": amount, "status": "succeeded",
              "created": ts(day), "reason": "requested_by_customer", "balance_transaction": None}
        re["balance_transaction"] = self.txn("refund", -amount, 0, re["id"], day)["id"]
        self.data["refunds"].append(re)
        return re

    def adjustment(self, net, day, description="Chargeback withholding"):
        return self.txn("adjustment", net, 0, self._id("du"), day, description)

    def payout(self, objects, arrival, status="paid"):
        txn_ids = [o["balance_transaction"] if "balance_transaction" in o else o["id"] for o in objects]
        by_id = {t["id"]: t for t in self.data["balance_transactions"]}
        po = {"id": self._id("po"), "amount": sum(by_id[i]["net"] for i in txn_ids), "status": status,
              "arrival_date": ts(arrival), "created": ts(arrival)}
        self.data["payouts"].append(po)
        self.data["payout_balance_transactions"][po["id"]] = txn_ids
        return po

    def source(self):
        return FakeStripeSource(self.data)


class Harness:
    def __init__(self, source, qb=None):
        self.source = source
        self.qb = qb or FakeQuickBooks.from_seed(quickbooks_seed())
        self.ledger = Ledger()

    def sync(self, since=date(2026, 9, 1)):
        return SyncEngine(self.source, self.qb, self.ledger).run(since)

    def count(self, entity):
        return len(self.qb.all(entity))

    def created(self, entity):
        """Records the sync made (seed rows have no Stripe note)."""
        return [e for e in self.qb.all(entity) if "Stripe" in e.get("PrivateNote", "")]

    def account_balance(self, account_id):
        """Balance of a bank-type account, computed from the transactions that touch it."""
        bal = Decimal(0)
        for entity, sign in (("SalesReceipt", 1), ("Payment", 1), ("RefundReceipt", -1), ("Purchase", -1)):
            for e in self.qb.all(entity):
                ref = e.get("DepositToAccountRef") or e.get("AccountRef")
                if ref and ref["value"] == account_id:
                    bal += sign * Decimal(str(e["TotalAmt"]))
        for d in self.qb.all("Deposit"):
            for line in d["Line"]:
                if line["DepositLineDetail"]["AccountRef"]["value"] == account_id:
                    bal -= Decimal(str(line["Amount"]))
            if d["DepositToAccountRef"]["value"] == account_id:
                bal += Decimal(str(d["TotalAmt"]))
        return int(bal * 100)


@pytest.fixture
def stripe():
    return StripeBuilder()


@pytest.fixture
def sample():
    return Harness(FakeStripeSource(generate_stripe()))


CLEARING, CHECKING, FEES = "2", "1", "4"
