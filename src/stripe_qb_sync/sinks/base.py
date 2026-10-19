"""The write side: the small slice of the QuickBooks Online API the sync engine uses.

Bodies and return values are QuickBooks Online JSON entities (``SalesReceipt``,
``Purchase``, ``RefundReceipt``, ``Payment``, ``Deposit``, ``Customer``...), so the
real adapter is a thin HTTP wrapper and the fake can be checked against the same shapes.
"""

from __future__ import annotations

from typing import Protocol


class DuplicateNameError(Exception):
    """QuickBooks error 6240: another Customer/Vendor/Employee already uses this name."""


class QuickBooksSink(Protocol):
    def find_by_name(self, entity: str, name: str) -> dict | None:
        """Account/Item by ``Name``, Vendor/Customer by ``DisplayName``."""

    def find_customer_by_email(self, email: str) -> dict | None:
        """Case-insensitive match on ``PrimaryEmailAddr.Address``."""

    def find_invoice_by_doc_number(self, doc_number: str) -> dict | None: ...

    def get(self, entity: str, entity_id: str) -> dict | None: ...

    def create(self, entity: str, body: dict, request_id: str) -> dict:
        """Create an entity and return it with its ``Id``.

        ``request_id`` maps to QuickBooks' ``?requestid=`` idempotency parameter: replaying
        the same request id returns the original object instead of creating a duplicate.
        """
