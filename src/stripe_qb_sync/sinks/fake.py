"""Offline QuickBooks Online company, persisted to a JSON file.

Behaves like the parts of QBO the engine relies on: auto-assigned Ids, ``requestid``
idempotency, duplicate-name rejection, computed ``TotalAmt`` and invoice balances.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

from .base import DuplicateNameError

TOTALED = {"SalesReceipt", "RefundReceipt", "Purchase", "Deposit", "Invoice"}
NAMED_BY_DISPLAY_NAME = {"Customer", "Vendor"}


def _sum_lines(lines: list[dict]) -> float:
    return float(sum((Decimal(str(line["Amount"])) for line in lines), Decimal(0)))


class FakeQuickBooks:
    def __init__(self, data: dict | None = None):
        data = copy.deepcopy(data or {})
        self.entities: dict[str, dict[str, dict]] = data.get("entities", {})
        self.request_ids: dict[str, list[str]] = data.get("request_ids", {})
        self.next_id: int = data.get("next_id", 1)
        self.create_calls = 0  # how many creates actually wrote something (for tests)

    # -- persistence -------------------------------------------------------------------
    @classmethod
    def from_seed(cls, seed: dict) -> FakeQuickBooks:
        qb = cls()
        for entity, rows in seed.items():
            for row in rows:
                qb._insert(entity, dict(row))
        return qb

    @classmethod
    def load(cls, path: str | Path) -> FakeQuickBooks:
        return cls(json.loads(Path(path).read_text()))

    def save(self, path: str | Path) -> None:
        state = {"entities": self.entities, "request_ids": self.request_ids, "next_id": self.next_id}
        Path(path).write_text(json.dumps(state, indent=2, sort_keys=True))

    # -- QuickBooksSink ----------------------------------------------------------------
    def all(self, entity: str) -> list[dict]:
        return list(self.entities.get(entity, {}).values())

    def find_by_name(self, entity: str, name: str) -> dict | None:
        key = "DisplayName" if entity in NAMED_BY_DISPLAY_NAME else "Name"
        return next((e for e in self.all(entity) if e.get(key) == name), None)

    def find_customer_by_email(self, email: str) -> dict | None:
        wanted = email.strip().lower()
        for c in self.all("Customer"):
            addr = (c.get("PrimaryEmailAddr") or {}).get("Address", "")
            if addr.strip().lower() == wanted:
                return c
        return None

    def find_invoice_by_doc_number(self, doc_number: str) -> dict | None:
        return next((i for i in self.all("Invoice") if i.get("DocNumber") == doc_number), None)

    def get(self, entity: str, entity_id: str) -> dict | None:
        return self.entities.get(entity, {}).get(str(entity_id))

    def create(self, entity: str, body: dict, request_id: str) -> dict:
        if request_id in self.request_ids:
            prior_entity, prior_id = self.request_ids[request_id]
            return self.entities[prior_entity][prior_id]
        if entity in NAMED_BY_DISPLAY_NAME and self.find_by_name(entity, body["DisplayName"]):
            raise DuplicateNameError(f"Duplicate Name Exists Error: {body['DisplayName']}")
        obj = self._insert(entity, copy.deepcopy(body))
        if entity == "Payment":
            self._apply_payment(obj)
        self.request_ids[request_id] = [entity, obj["Id"]]
        self.create_calls += 1
        return obj

    # -- internals ---------------------------------------------------------------------
    def _insert(self, entity: str, obj: dict) -> dict:
        obj.setdefault("Id", str(self.next_id))
        self.next_id = max(self.next_id, int(obj["Id"])) + 1
        if entity in TOTALED:
            obj["TotalAmt"] = _sum_lines(obj.get("Line", []))
        if entity == "Invoice":
            obj.setdefault("Balance", obj["TotalAmt"])
        self.entities.setdefault(entity, {})[obj["Id"]] = obj
        return obj

    def _apply_payment(self, payment: dict) -> None:
        for line in payment.get("Line", []):
            for linked in line.get("LinkedTxn", []):
                if linked["TxnType"] == "Invoice":
                    inv = self.get("Invoice", linked["TxnId"])
                    remaining = Decimal(str(inv["Balance"])) - Decimal(str(line["Amount"]))
                    inv["Balance"] = float(remaining)
