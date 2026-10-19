"""The read side: everything the sync engine needs from Stripe."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Protocol

from ..models import BalanceTransaction, Charge, Payout, Refund, StripeCustomer


class StripeSource(Protocol):
    """Read-only view of a Stripe account.

    Implementations: ``FakeStripeSource`` (offline JSON fixture, used by tests and the demo)
    and ``StripeApiSource`` (real Stripe API, test or live mode).
    """

    def list_charges(self, since: date) -> Iterable[Charge]:
        """Charges created on or after ``since``, oldest first."""

    def get_charge(self, charge_id: str) -> Charge: ...

    def list_refunds(self, since: date) -> Iterable[Refund]:
        """Refunds created on or after ``since``, oldest first."""

    def list_payouts(self, since: date) -> Iterable[Payout]:
        """Payouts arriving on or after ``since``, oldest first."""

    def list_payout_transactions(self, payout_id: str) -> list[BalanceTransaction]:
        """Every balance transaction settled by this payout (excluding the payout row itself)."""

    def get_balance_transaction(self, txn_id: str) -> BalanceTransaction: ...

    def get_customer(self, customer_id: str) -> StripeCustomer: ...
