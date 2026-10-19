"""The sync engine: Stripe activity in, balanced QuickBooks records out.

Accounting model (the "clearing account" method most bookkeepers use for Stripe):

    Stripe charge   -> SalesReceipt  (gross sale)      deposited to  Stripe Clearing
                       or Payment against an open Invoice (charge.metadata.qbo_invoice)
    Stripe fee      -> Purchase      (Stripe Fees expense)  paid from Stripe Clearing
    Stripe refund   -> RefundReceipt                        paid from Stripe Clearing
    Stripe payout   -> Deposit       Stripe Clearing -> Business Checking, one line per
                                     balance transaction the payout settled

After every payout lands, Stripe Clearing holds exactly the money still sitting in Stripe,
and the Deposit matches the bank feed to the cent.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from .ledger import Ledger
from .models import BalanceTransaction, Charge, Payout, Refund, cents_to_amount, fmt_money
from .sinks.base import DuplicateNameError, QuickBooksSink
from .sources.base import StripeSource

# Balance transaction types the engine knows how to book. Anything else (dispute
# adjustments, Stripe Capital, Issuing...) is left for a human and flagged in the report.
BOOKABLE_TYPES = {"charge", "payment", "refund", "payment_refund"}


@dataclass(frozen=True)
class QuickBooksSetup:
    """Names of the QuickBooks records the sync posts against. Create these once."""

    clearing_account: str = "Stripe Clearing"
    bank_account: str = "Business Checking"
    fee_account: str = "Stripe Fees"
    sales_item: str = "Online Sales"
    stripe_vendor: str = "Stripe"
    fallback_customer: str = "Stripe Customer"


class SetupError(RuntimeError):
    pass


@dataclass
class SyncResult:
    counts: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        c = self.counts
        rows = [
            ("Customers matched to existing QuickBooks customers", c["customers_matched"]),
            ("Customers created", c["customers_created"]),
            ("Sales receipts created", c["sales_receipts"]),
            ("Invoice payments applied", c["invoice_payments"]),
            ("Stripe fee expenses recorded", c["fee_expenses"]),
            ("Refund receipts created", c["refund_receipts"]),
            ("Bank deposits created (payouts)", c["deposits"]),
            ("Already synced, skipped", c["already_synced"]),
            ("Failed/incomplete charges ignored", c["ignored_charges"]),
            ("Payouts not yet arrived", c["payouts_pending"]),
        ]
        width = max(len(label) for label, _ in rows)
        lines = [f"  {label:<{width}}  {n:>4}" for label, n in rows]
        if self.warnings:
            lines.append("")
            lines.append(f"  Needs review ({len(self.warnings)}):")
            lines.extend(f"    ! {w}" for w in self.warnings)
        return "\n".join(lines)


def request_id(kind: str, stripe_id: str) -> str:
    """Deterministic QuickBooks ``requestid``: a retried write can never post twice."""
    return hashlib.sha256(f"stripe-qb:{kind}:{stripe_id}".encode()).hexdigest()[:32]


def _ref(obj: dict, name_key: str = "Name") -> dict:
    return {"value": obj["Id"], "name": obj.get(name_key) or obj.get("DisplayName", "")}


class SyncEngine:
    def __init__(
        self,
        source: StripeSource,
        sink: QuickBooksSink,
        ledger: Ledger,
        setup: QuickBooksSetup = QuickBooksSetup(),
    ):
        self.source, self.sink, self.ledger, self.setup = source, sink, ledger, setup
        self.result = SyncResult()

    # ---------------------------------------------------------------------------------
    def run(self, since: date) -> SyncResult:
        self.result = SyncResult()
        self._resolve_setup()
        for charge in self.source.list_charges(since):
            self.sync_charge(charge)
        for refund in self.source.list_refunds(since):
            self.sync_refund(refund)
        for payout in self.source.list_payouts(since):
            self.sync_payout(payout)
        self.ledger.set_meta("last_since", since.isoformat())
        return self.result

    def _resolve_setup(self) -> None:
        s = self.setup
        wanted = {
            "clearing": ("Account", s.clearing_account),
            "bank": ("Account", s.bank_account),
            "fees": ("Account", s.fee_account),
            "item": ("Item", s.sales_item),
            "vendor": ("Vendor", s.stripe_vendor),
        }
        self.refs, missing = {}, []
        for key, (entity, name) in wanted.items():
            obj = self.sink.find_by_name(entity, name)
            if obj is None:
                missing.append(f"{entity} '{name}'")
            else:
                self.refs[key] = _ref(obj)
        if missing:
            raise SetupError("Create these in QuickBooks first: " + ", ".join(missing))

    def _create(self, kind: str, stripe_id: str, entity: str, body: dict) -> dict:
        obj = self.sink.create(entity, body, request_id(kind, stripe_id))
        self.ledger.record(kind, stripe_id, entity, obj["Id"])
        return obj

    # -- customers ---------------------------------------------------------------------
    def ensure_customer(self, charge: Charge) -> dict:
        if charge.customer_id:
            key = charge.customer_id
            stripe_customer = self.source.get_customer(charge.customer_id)
            name, email = stripe_customer.name, stripe_customer.email
        else:  # guest checkout: identity lives on the charge's billing details
            name, email = charge.billing_name, charge.billing_email
            key = f"guest:{email.lower()}" if email else "guest:unknown"

        if mapped := self.ledger.get("customer", key):
            return self.sink.get("Customer", mapped[1])

        if not email:
            customer = self.sink.find_by_name("Customer", self.setup.fallback_customer) or self.sink.create(
                "Customer", {"DisplayName": self.setup.fallback_customer}, request_id("customer", key)
            )
            self.ledger.record("customer", key, "Customer", customer["Id"])
            return customer

        if existing := self.sink.find_customer_by_email(email):
            self.ledger.record("customer", key, "Customer", existing["Id"])
            self.result.counts["customers_matched"] += 1
            return existing

        body = {"DisplayName": name or email, "PrimaryEmailAddr": {"Address": email}}
        if name:
            first, _, last = name.partition(" ")
            body.update(GivenName=first, FamilyName=last)
        try:
            customer = self._create("customer", key, "Customer", body)
        except DuplicateNameError:
            # A different person with the same name already exists; QuickBooks needs unique names.
            body["DisplayName"] = f"{name} ({email})"
            customer = self.sink.create("Customer", body, request_id("customer-renamed", key))
            self.ledger.record("customer", key, "Customer", customer["Id"])
        self.result.counts["customers_created"] += 1
        return customer

    # -- charges & fees ----------------------------------------------------------------
    def sync_charge(self, charge: Charge) -> None:
        if charge.status != "succeeded":
            self.result.counts["ignored_charges"] += 1
            return
        if charge.currency != "usd":
            self.result.warnings.append(f"{charge.id}: {charge.currency.upper()} charge skipped (multi-currency is off)")
            return

        if self.ledger.get("charge", charge.id):
            self.result.counts["already_synced"] += 1
        elif invoice_no := charge.metadata.get("qbo_invoice"):
            self._record_invoice_payment(charge, invoice_no)
        else:
            self._record_sales_receipt(charge)

        if charge.balance_transaction_id:
            self.sync_fee(charge, self.source.get_balance_transaction(charge.balance_transaction_id))

    def _record_sales_receipt(self, charge: Charge) -> None:
        customer = self.ensure_customer(charge)
        amount = cents_to_amount(charge.amount)
        self._create("charge", charge.id, "SalesReceipt", {
            "CustomerRef": _ref(customer, "DisplayName"),
            "TxnDate": charge.created.isoformat(),
            "DepositToAccountRef": self.refs["clearing"],
            "PrivateNote": f"Stripe charge {charge.id}",
            "Line": [{
                "Amount": amount,
                "Description": charge.description,
                "DetailType": "SalesItemLineDetail",
                "SalesItemLineDetail": {"ItemRef": self.refs["item"], "Qty": 1, "UnitPrice": amount},
            }],
        })
        self.result.counts["sales_receipts"] += 1

    def _record_invoice_payment(self, charge: Charge, invoice_no: str) -> None:
        invoice = self.sink.find_invoice_by_doc_number(invoice_no)
        if invoice is None:
            self.result.warnings.append(f"{charge.id}: pays invoice #{invoice_no}, which is not in QuickBooks")
            return
        if round(invoice["Balance"] * 100) < charge.amount:
            self.result.warnings.append(
                f"{charge.id}: {fmt_money(charge.amount)} exceeds invoice #{invoice_no} open balance "
                f"{fmt_money(round(invoice['Balance'] * 100))}"
            )
            return
        amount = cents_to_amount(charge.amount)
        self._create("charge", charge.id, "Payment", {
            "CustomerRef": invoice["CustomerRef"],
            "TotalAmt": amount,
            "TxnDate": charge.created.isoformat(),
            "DepositToAccountRef": self.refs["clearing"],
            "PrivateNote": f"Stripe charge {charge.id}",
            "Line": [{"Amount": amount, "LinkedTxn": [{"TxnId": invoice["Id"], "TxnType": "Invoice"}]}],
        })
        self.result.counts["invoice_payments"] += 1

    def sync_fee(self, charge: Charge, txn: BalanceTransaction) -> None:
        if txn.fee == 0:
            return
        if self.ledger.get("fee", txn.id):
            return
        self._create("fee", txn.id, "Purchase", {
            "PaymentType": "Cash",
            "AccountRef": self.refs["clearing"],
            "EntityRef": {**self.refs["vendor"], "type": "Vendor"},
            "TxnDate": txn.created.isoformat(),
            "PrivateNote": f"Stripe fee for {charge.id} ({txn.id})",
            "Line": [{
                "Amount": cents_to_amount(txn.fee),
                "Description": f"Stripe processing fee - {charge.id}",
                "DetailType": "AccountBasedExpenseLineDetail",
                "AccountBasedExpenseLineDetail": {"AccountRef": self.refs["fees"]},
            }],
        })
        self.result.counts["fee_expenses"] += 1

    # -- refunds -----------------------------------------------------------------------
    def sync_refund(self, refund: Refund) -> None:
        if refund.status != "succeeded":
            return
        if self.ledger.get("refund", refund.id):
            self.result.counts["already_synced"] += 1
            return
        charge = self.source.get_charge(refund.charge_id)
        if not self.ledger.get("charge", charge.id):
            self.result.warnings.append(
                f"{refund.id}: refunds {charge.id}, which was never synced (before --since?); recorded anyway"
            )
        customer = self.ensure_customer(charge)
        amount = cents_to_amount(refund.amount)
        label = "Full refund" if refund.amount == charge.amount else "Partial refund"
        self._create("refund", refund.id, "RefundReceipt", {
            "CustomerRef": _ref(customer, "DisplayName"),
            "TxnDate": refund.created.isoformat(),
            "DepositToAccountRef": self.refs["clearing"],
            "PrivateNote": f"Stripe refund {refund.id} of {charge.id}",
            "Line": [{
                "Amount": amount,
                "Description": f"{label}: {charge.description}",
                "DetailType": "SalesItemLineDetail",
                "SalesItemLineDetail": {"ItemRef": self.refs["item"], "Qty": 1, "UnitPrice": amount},
            }],
        })
        self.result.counts["refund_receipts"] += 1

    # -- payouts -----------------------------------------------------------------------
    def sync_payout(self, payout: Payout) -> None:
        if payout.status != "paid":
            self.result.counts["payouts_pending"] += 1
            return
        if self.ledger.get("payout", payout.id):
            self.result.counts["already_synced"] += 1
            return

        lines = []
        for txn in self.source.list_payout_transactions(payout.id):
            if txn.type not in BOOKABLE_TYPES:
                self.result.warnings.append(
                    f"{payout.id}: {txn.type} {txn.id} ({fmt_money(txn.net)}, {txn.description!r}) "
                    "has no automatic booking rule; deposit will not match until it is booked"
                )
                continue
            lines.append({
                "Amount": cents_to_amount(txn.net),
                "Description": f"{txn.type} {txn.source_id}" + (f" (net of {fmt_money(txn.fee)} fee)" if txn.fee else ""),
                "DetailType": "DepositLineDetail",
                "DepositLineDetail": {"AccountRef": self.refs["clearing"]},
            })

        self._create("payout", payout.id, "Deposit", {
            "TxnDate": payout.arrival_date.isoformat(),
            "DepositToAccountRef": self.refs["bank"],
            "PrivateNote": f"Stripe payout {payout.id}",
            "Line": lines,
        })
        self.result.counts["deposits"] += 1
