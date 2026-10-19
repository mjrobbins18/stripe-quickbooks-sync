"""Deterministic sample data for a fictional pottery studio, "Juniper Lane Clay Studio".

September 2026 in Stripe: class bookings, memberships, gift cards and shop sales from about
two dozen customers plus guest checkouts, weekly Monday payouts, refunds, two declined cards,
a chargeback and a wholesale invoice paid by card. Every person, email and id is made up;
all emails use the reserved example.com/.net/.org domains.

The QuickBooks seed is the studio's books before automation: chart of accounts, a few
existing customers (one with different email casing, one sharing a name with a Stripe
customer), open wholesale invoices, and some manual August entries.
"""

from __future__ import annotations

import json
import random
import string
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_SEED = 2026
START, END = date(2026, 9, 1), date(2026, 9, 30)
PAYOUT_DAYS = [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)]
AVAILABLE_AFTER_DAYS = 2
PAYOUT_TRANSIT_DAYS = 2
SNAPSHOT = date(2026, 9, 29)  # data is "as of" this day; later payouts are still in transit

CATALOG = [
    ("Drop-in wheel class", 4500, 8),
    ("Beginner wheel course (6 weeks)", 24000, 3),
    ("Glaze & fire workshop", 6500, 4),
    ("Studio membership (monthly)", 12000, 3),
    ("Handmade mug", 3200, 5),
    ("Serving bowl", 8500, 2),
    ("Gift card", 5000, 2),
    ("Gift card", 10000, 1),
    ("Kids clay camp (1 day)", 5500, 2),
    ("Date night couples class", 11000, 3),
]
FIRST = ["Avery", "Jordan", "Priya", "Mateo", "Hannah", "Theo", "Lena", "Marcus", "Iris", "Owen",
         "Nadia", "Caleb", "Rosa", "Felix", "June", "Quinn", "Elena", "Malik", "Greta", "Ravi"]
LAST = ["Holloway", "Nakamura", "Brennan", "Okafor", "Lindqvist", "Castillo", "Mercer", "Asante",
        "Delgado", "Fairbanks", "Kowalski", "Rhee", "Sorensen", "Vance", "Whitlock", "Yilmaz"]
CARD_BRANDS = ["visa", "visa", "visa", "mastercard", "mastercard", "amex", "discover"]


def _ts(d: date, hour: int = 12, minute: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).timestamp())


def _fee(amount: int) -> int:
    """Stripe's standard US card pricing: 2.9% + 30¢."""
    return round(amount * 0.029) + 30


class _Ids:
    def __init__(self, rng: random.Random):
        self.rng = rng

    def __call__(self, prefix: str) -> str:
        return prefix + "_" + "".join(self.rng.choices(string.ascii_letters + string.digits, k=24))


def generate_stripe(seed: int = DEFAULT_SEED) -> dict:
    rng = random.Random(seed)
    new_id = _Ids(rng)
    customers, charges, refunds, txns, payouts = [], [], [], [], []

    def add_customer(name: str, email: str) -> dict:
        c = {"id": new_id("cus"), "object": "customer", "name": name, "email": email,
             "created": _ts(date(2026, 8, rng.randint(1, 28)))}
        customers.append(c)
        return c

    used = set()
    while len(customers) < 20:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        if (first, last) in used:
            continue
        used.add((first, last))
        add_customer(f"{first} {last}", f"{first}.{last}@example.{rng.choice(['com', 'net', 'org'])}".lower())
    dana = add_customer("Dana Whitfield", "Dana.Whitfield@Example.com")  # same person as QBO, different casing
    sam = add_customer("Sam Patel", "sam.patel@example.net")  # different person, same name as a QBO customer
    ferncliff = add_customer("Ferncliff Coffee Co.", "ap@ferncliff.example.com")
    regulars = customers[:-1]

    def add_charge(day: date, desc: str, amount: int, customer: dict | None = None, guest: tuple | None = None,
                   status: str = "succeeded", metadata: dict | None = None) -> dict:
        created = _ts(day, rng.randint(14, 23), rng.randint(0, 59))
        ch = {
            "id": new_id("ch"), "object": "charge", "amount": amount, "amount_captured": amount if status == "succeeded" else 0,
            "amount_refunded": 0, "currency": "usd", "created": created, "description": desc, "status": status,
            "paid": status == "succeeded", "refunded": False, "disputed": False,
            "customer": customer["id"] if customer else None,
            "billing_details": {"name": (customer or {}).get("name") or guest[0], "email": (customer or {}).get("email") or guest[1]},
            "payment_method_details": {"type": "card", "card": {"brand": rng.choice(CARD_BRANDS), "last4": f"{rng.randint(0, 9999):04d}"}},
            "metadata": metadata or {}, "balance_transaction": None,
            "failure_code": None if status == "succeeded" else "card_declined",
        }
        if status == "succeeded":
            fee = _fee(amount)
            txn = {"id": new_id("txn"), "object": "balance_transaction", "type": "charge", "amount": amount, "fee": fee,
                   "net": amount - fee, "currency": "usd", "source": ch["id"], "created": created,
                   "available_on": _ts(day + timedelta(days=AVAILABLE_AFTER_DAYS)), "description": desc,
                   "fee_details": [{"type": "stripe_fee", "amount": fee, "description": "Stripe processing fees"}]}
            txns.append(txn)
            ch["balance_transaction"] = txn["id"]
        charges.append(ch)
        return ch

    weights = [w for *_, w in CATALOG]
    day = START
    while day <= END:
        n = rng.randint(2, 4) if day.weekday() >= 5 else rng.randint(1, 3)
        for _ in range(n):
            desc, amount, _ = rng.choices(CATALOG, weights)[0]
            if desc == "Handmade mug" and rng.random() < 0.4:
                desc, amount = "Handmade mug x2", amount * 2
            if rng.random() < 0.2:
                first, last = rng.choice(FIRST), rng.choice(LAST)
                add_charge(day, desc, amount, guest=(f"{first} {last}", f"{first[0]}{last}@example.com".lower()))
            else:
                add_charge(day, desc, amount, customer=rng.choice(regulars))
        day += timedelta(days=1)

    # Scripted scenarios the README walks through.
    add_charge(date(2026, 9, 4), "Studio membership (monthly)", 12000, customer=dana)
    add_charge(date(2026, 9, 12), "Beginner wheel course (6 weeks)", 24000, customer=sam)
    add_charge(date(2026, 9, 15), "Wholesale order - invoice #1043", 48000, customer=ferncliff,
               metadata={"qbo_invoice": "1043"})
    add_charge(date(2026, 9, 9), "Serving bowl", 8500, customer=rng.choice(regulars), status="failed")
    add_charge(date(2026, 9, 23), "Date night couples class", 11000, customer=rng.choice(regulars), status="failed")
    full = add_charge(date(2026, 9, 10), "Beginner wheel course (6 weeks)", 24000, customer=rng.choice(regulars))
    partial = add_charge(date(2026, 9, 18), "Handmade mug x2", 6400, customer=rng.choice(regulars))
    late = add_charge(date(2026, 9, 27), "Drop-in wheel class", 4500, customer=rng.choice(regulars))
    disputed = add_charge(date(2026, 9, 13), "Glaze & fire workshop", 6500, customer=rng.choice(regulars))

    def add_refund(ch: dict, amount: int, day: date, reason: str) -> None:
        created = _ts(day, rng.randint(14, 23), rng.randint(0, 59))
        re = {"id": new_id("re"), "object": "refund", "amount": amount, "charge": ch["id"], "currency": "usd",
              "created": created, "reason": reason, "status": "succeeded", "balance_transaction": None}
        txn = {"id": new_id("txn"), "object": "balance_transaction", "type": "refund", "amount": -amount, "fee": 0,
               "net": -amount, "currency": "usd", "source": re["id"], "created": created,
               "available_on": _ts(day), "description": f"REFUND FOR CHARGE ({ch['description']})", "fee_details": []}
        re["balance_transaction"] = txn["id"]
        ch["amount_refunded"] += amount
        ch["refunded"] = ch["amount_refunded"] == ch["amount"]
        refunds.append(re)
        txns.append(txn)

    add_refund(full, 24000, date(2026, 9, 11), "requested_by_customer")
    add_refund(partial, 3200, date(2026, 9, 20), "requested_by_customer")  # one mug arrived cracked
    add_refund(late, 4500, date(2026, 9, 28), "requested_by_customer")

    # Chargeback: Stripe withdraws the disputed amount plus a $15 dispute fee as an adjustment.
    disputed["disputed"] = True
    dispute_day = date(2026, 9, 17)
    txns.append({"id": new_id("txn"), "object": "balance_transaction", "type": "adjustment",
                 "amount": -disputed["amount"], "fee": 1500, "net": -disputed["amount"] - 1500, "currency": "usd",
                 "source": new_id("du"), "created": _ts(dispute_day, 16), "available_on": _ts(dispute_day, 16),
                 "description": f"Chargeback withholding for {disputed['id']}",
                 "fee_details": [{"type": "stripe_fee", "amount": 1500, "description": "Dispute fee"}]})

    # Weekly automatic payouts sweep everything that has become available.
    payout_map, paid = {}, set()
    for pday in PAYOUT_DAYS:
        cutoff = _ts(pday, 12)
        batch = [t for t in txns if t["available_on"] <= cutoff and t["id"] not in paid and t["type"] != "payout"]
        batch.sort(key=lambda t: (t["created"], t["id"]))
        total = sum(t["net"] for t in batch)
        arrival = pday + timedelta(days=PAYOUT_TRANSIT_DAYS)
        po = {"id": new_id("po"), "object": "payout", "amount": total, "currency": "usd", "automatic": True,
              "created": cutoff, "arrival_date": _ts(arrival), "method": "standard",
              "status": "paid" if arrival < SNAPSHOT else "in_transit", "description": "STRIPE PAYOUT"}
        payouts.append(po)
        txns.append({"id": new_id("txn"), "object": "balance_transaction", "type": "payout", "amount": -total,
                     "fee": 0, "net": -total, "currency": "usd", "source": po["id"], "created": cutoff,
                     "available_on": cutoff, "description": "STRIPE PAYOUT", "fee_details": []})
        payout_map[po["id"]] = [t["id"] for t in batch]
        paid.update(payout_map[po["id"]])

    by_created = lambda o: (o["created"], o["id"])  # noqa: E731
    return {
        "account": {"business_name": "Juniper Lane Clay Studio", "country": "US", "default_currency": "usd"},
        "customers": sorted(customers, key=by_created),
        "charges": sorted(charges, key=by_created),
        "refunds": sorted(refunds, key=by_created),
        "balance_transactions": sorted(txns, key=by_created),
        "payouts": sorted(payouts, key=by_created),
        "payout_balance_transactions": payout_map,
    }


def _ref(id_: str, name: str) -> dict:
    return {"value": id_, "name": name}


def _sales_line(amount: float, desc: str, item=("11", "Wholesale Pottery")) -> dict:
    return {"Amount": amount, "Description": desc, "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {"ItemRef": _ref(*item), "Qty": 1, "UnitPrice": amount}}


def quickbooks_seed() -> dict:
    return {
        "Account": [
            {"Id": "1", "Name": "Business Checking", "AccountType": "Bank", "AccountSubType": "Checking"},
            {"Id": "2", "Name": "Stripe Clearing", "AccountType": "Bank", "AccountSubType": "CashOnHand",
             "Description": "Money held by Stripe between a sale and its payout"},
            {"Id": "3", "Name": "Sales", "AccountType": "Income", "AccountSubType": "SalesOfProductIncome"},
            {"Id": "4", "Name": "Stripe Fees", "AccountType": "Expense", "AccountSubType": "BankCharges"},
            {"Id": "5", "Name": "Accounts Receivable (A/R)", "AccountType": "Accounts Receivable"},
            {"Id": "6", "Name": "Undeposited Funds", "AccountType": "Other Current Asset"},
        ],
        "Item": [
            {"Id": "10", "Name": "Online Sales", "Type": "Service", "IncomeAccountRef": _ref("3", "Sales")},
            {"Id": "11", "Name": "Wholesale Pottery", "Type": "NonInventory", "IncomeAccountRef": _ref("3", "Sales")},
        ],
        "Vendor": [{"Id": "20", "DisplayName": "Stripe"}],
        "Customer": [
            {"Id": "30", "DisplayName": "Ferncliff Coffee Co.", "CompanyName": "Ferncliff Coffee Co.",
             "PrimaryEmailAddr": {"Address": "ap@ferncliff.example.com"}},
            {"Id": "31", "DisplayName": "Dana Whitfield", "GivenName": "Dana", "FamilyName": "Whitfield",
             "PrimaryEmailAddr": {"Address": "dana.whitfield@example.com"}},
            {"Id": "32", "DisplayName": "Sam Patel", "GivenName": "Sam", "FamilyName": "Patel",
             "PrimaryEmailAddr": {"Address": "sam.patel@example.org"}},
            {"Id": "33", "DisplayName": "Bluebird Bakery", "CompanyName": "Bluebird Bakery",
             "PrimaryEmailAddr": {"Address": "orders@bluebird.example.com"}},
        ],
        "Invoice": [
            {"Id": "40", "DocNumber": "1041", "TxnDate": "2026-08-10", "CustomerRef": _ref("33", "Bluebird Bakery"),
             "Line": [_sales_line(360.0, "Bread bowls x24")], "Balance": 0.0},
            {"Id": "41", "DocNumber": "1042", "TxnDate": "2026-08-24", "CustomerRef": _ref("33", "Bluebird Bakery"),
             "Line": [_sales_line(275.0, "Pastry plates x20")]},
            {"Id": "42", "DocNumber": "1043", "TxnDate": "2026-08-28", "CustomerRef": _ref("30", "Ferncliff Coffee Co."),
             "Line": [_sales_line(480.0, "Espresso cups x48")]},
        ],
        "SalesReceipt": [
            {"Id": "50", "TxnDate": "2026-08-29", "CustomerRef": _ref("33", "Bluebird Bakery"),
             "DepositToAccountRef": _ref("6", "Undeposited Funds"), "PrivateNote": "Farmers market, cash (manual entry)",
             "Line": [_sales_line(140.0, "Market day sales", ("10", "Online Sales"))]},
        ],
        "Deposit": [
            {"Id": "60", "TxnDate": "2026-08-31", "DepositToAccountRef": _ref("1", "Business Checking"),
             "PrivateNote": "Check #2207 from Bluebird Bakery (manual entry)",
             "Line": [{"Amount": 360.0, "DetailType": "DepositLineDetail",
                       "DepositLineDetail": {"AccountRef": _ref("5", "Accounts Receivable (A/R)")}}]},
        ],
    }


def write_sample_data(directory: str | Path, seed: int = DEFAULT_SEED) -> tuple[Path, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stripe_path, qbo_path = directory / "stripe.json", directory / "quickbooks_seed.json"
    stripe_path.write_text(json.dumps(generate_stripe(seed), indent=2) + "\n")
    qbo_path.write_text(json.dumps(quickbooks_seed(), indent=2) + "\n")
    return stripe_path, qbo_path
