"""Reconciliation: does every Stripe payout have a QuickBooks deposit for the same amount?"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .engine import BOOKABLE_TYPES
from .ledger import Ledger
from .models import Payout, amount_to_cents, fmt_money
from .sinks.base import QuickBooksSink
from .sources.base import StripeSource

OK, MISMATCH, MISSING, NOT_SYNCED, IN_TRANSIT = "OK", "MISMATCH", "MISSING", "NOT SYNCED", "IN TRANSIT"
PROBLEMS = {MISMATCH, MISSING, NOT_SYNCED}


@dataclass
class PayoutCheck:
    payout: Payout
    deposit_id: str | None
    deposit_total: int | None
    status: str
    notes: list[str] = field(default_factory=list)

    @property
    def difference(self) -> int:
        return (self.deposit_total or 0) - self.payout.amount


@dataclass
class Report:
    since: date
    checks: list[PayoutCheck]

    @property
    def problems(self) -> list[PayoutCheck]:
        return [c for c in self.checks if c.status in PROBLEMS]

    def render(self) -> str:
        out = [
            f"Stripe payouts vs QuickBooks deposits (payouts arriving on/after {self.since})",
            "",
            f"{'Payout':<27} {'Arrives':<10} {'Stripe':>11} {'QuickBooks':>11} {'Diff':>9}  Status",
            "-" * 86,
        ]
        for c in self.checks:
            qbo = fmt_money(c.deposit_total) if c.deposit_total is not None else "-"
            diff = fmt_money(c.difference) if c.deposit_total is not None and c.difference else ""
            out.append(
                f"{c.payout.id:<27} {c.payout.arrival_date.isoformat():<10} "
                f"{fmt_money(c.payout.amount):>11} {qbo:>11} {diff:>9}  {c.status}"
            )
            out.extend(f"{'':>29}↳ {note}" for note in c.notes)
        settled = [c for c in self.checks if c.status != IN_TRANSIT]
        stripe_total = sum(c.payout.amount for c in settled)
        qbo_total = sum(c.deposit_total or 0 for c in settled)
        out += [
            "-" * 86,
            f"{'Arrived payouts':<38} {fmt_money(stripe_total):>11} {fmt_money(qbo_total):>11} "
            f"{fmt_money(qbo_total - stripe_total) if qbo_total != stripe_total else '':>9}",
            "",
        ]
        if self.problems:
            out.append(f"{len(self.problems)} payout(s) need attention before the books will reconcile.")
        else:
            out.append("All arrived payouts reconcile to the cent.")
        return "\n".join(out)


def build_report(source: StripeSource, sink: QuickBooksSink, ledger: Ledger, since: date) -> Report:
    checks = []
    for payout in source.list_payouts(since):
        if payout.status != "paid":
            checks.append(PayoutCheck(payout, None, None, IN_TRANSIT, [f"Stripe status: {payout.status}"]))
            continue
        mapped = ledger.get("payout", payout.id)
        if not mapped:
            checks.append(PayoutCheck(payout, None, None, NOT_SYNCED, ["Run `stripe-qb sync` to create the deposit"]))
            continue
        deposit = sink.get("Deposit", mapped[1])
        if deposit is None:
            checks.append(PayoutCheck(payout, mapped[1], None, MISSING, [f"Deposit {mapped[1]} was deleted in QuickBooks"]))
            continue
        total = sum(amount_to_cents(line["Amount"]) for line in deposit["Line"])
        check = PayoutCheck(payout, mapped[1], total, OK if total == payout.amount else MISMATCH)
        check.notes = _explain(source, ledger, payout, total)
        checks.append(check)
    return Report(since, checks)


def _explain(source: StripeSource, ledger: Ledger, payout: Payout, deposit_total: int) -> list[str]:
    """Point at the specific Stripe transactions behind a difference."""
    notes = []
    txns = source.list_payout_transactions(payout.id)
    expected = 0
    for t in txns:
        if t.type not in BOOKABLE_TYPES:
            notes.append(f"Unbooked {t.type} {t.id} {fmt_money(t.net)}: {t.description}")
            continue
        expected += t.net
        if t.type in ("charge", "payment") and not ledger.get("charge", t.source_id):
            notes.append(f"{t.source_id} is in this payout but has no sales receipt/payment in QuickBooks")
    if deposit_total != expected:
        notes.append(f"Deposit was edited in QuickBooks: lines total {fmt_money(deposit_total)}, "
                     f"Stripe transactions total {fmt_money(expected)}")
    return notes
