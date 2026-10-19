"""Normalized Stripe objects.

Both the offline fake and the real Stripe adapter return these dataclasses, built by the
same ``from_stripe`` parsers, so the sync engine never sees raw API payloads.
All money is integer cents (Stripe's native unit) to avoid float rounding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal


def ts_to_date(ts: int) -> date:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def cents_to_amount(cents: int) -> float:
    """QuickBooks JSON expects decimal dollars, e.g. 1234 -> 12.34."""
    return float(Decimal(cents) / 100)


def amount_to_cents(amount: float | str | Decimal) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1)))


def fmt_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


@dataclass(frozen=True)
class StripeCustomer:
    id: str
    name: str
    email: str

    @classmethod
    def from_stripe(cls, obj: dict) -> StripeCustomer:
        return cls(id=obj["id"], name=obj.get("name") or obj["email"], email=obj["email"])


@dataclass(frozen=True)
class Charge:
    id: str
    amount: int
    currency: str
    status: str
    created: date
    description: str
    customer_id: str | None
    billing_name: str | None
    billing_email: str | None
    balance_transaction_id: str | None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_stripe(cls, obj: dict) -> Charge:
        billing = obj.get("billing_details") or {}
        return cls(
            id=obj["id"],
            amount=obj["amount"],
            currency=obj["currency"],
            status=obj["status"],
            created=ts_to_date(obj["created"]),
            description=obj.get("description") or "",
            customer_id=obj.get("customer"),
            billing_name=billing.get("name"),
            billing_email=billing.get("email"),
            balance_transaction_id=obj.get("balance_transaction"),
            metadata=dict(obj.get("metadata") or {}),
        )


@dataclass(frozen=True)
class Refund:
    id: str
    charge_id: str
    amount: int
    status: str
    created: date
    reason: str | None
    balance_transaction_id: str | None

    @classmethod
    def from_stripe(cls, obj: dict) -> Refund:
        return cls(
            id=obj["id"],
            charge_id=obj["charge"],
            amount=obj["amount"],
            status=obj["status"],
            created=ts_to_date(obj["created"]),
            reason=obj.get("reason"),
            balance_transaction_id=obj.get("balance_transaction"),
        )


@dataclass(frozen=True)
class BalanceTransaction:
    """One movement in the Stripe balance. ``amount`` is gross, ``net`` = amount - fee."""

    id: str
    type: str  # charge | refund | adjustment | payout | ...
    amount: int
    fee: int
    net: int
    source_id: str | None
    created: date
    description: str

    @classmethod
    def from_stripe(cls, obj: dict) -> BalanceTransaction:
        return cls(
            id=obj["id"],
            type=obj["type"],
            amount=obj["amount"],
            fee=obj["fee"],
            net=obj["net"],
            source_id=obj.get("source"),
            created=ts_to_date(obj["created"]),
            description=obj.get("description") or "",
        )


@dataclass(frozen=True)
class Payout:
    id: str
    amount: int
    status: str  # paid | in_transit | pending | failed | canceled
    arrival_date: date
    created: date

    @classmethod
    def from_stripe(cls, obj: dict) -> Payout:
        return cls(
            id=obj["id"],
            amount=obj["amount"],
            status=obj["status"],
            arrival_date=ts_to_date(obj["arrival_date"]),
            created=ts_to_date(obj["created"]),
        )
