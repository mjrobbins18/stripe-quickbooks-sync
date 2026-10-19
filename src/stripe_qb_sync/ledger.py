"""Sync ledger: which Stripe object became which QuickBooks record.

This is what makes reruns safe. Every write is keyed on (kind, Stripe id); if the key is
already here, the engine skips it. SQLite keeps it a single file you can back up or inspect.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_map (
    kind       TEXT NOT NULL,   -- customer | charge | fee | refund | payout
    stripe_id  TEXT NOT NULL,
    qbo_entity TEXT NOT NULL,   -- Customer | SalesReceipt | Payment | Purchase | RefundReceipt | Deposit
    qbo_id     TEXT NOT NULL,
    synced_at  TEXT NOT NULL,
    PRIMARY KEY (kind, stripe_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Ledger:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def get(self, kind: str, stripe_id: str) -> tuple[str, str] | None:
        row = self.db.execute(
            "SELECT qbo_entity, qbo_id FROM sync_map WHERE kind = ? AND stripe_id = ?", (kind, stripe_id)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def record(self, kind: str, stripe_id: str, qbo_entity: str, qbo_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO sync_map VALUES (?, ?, ?, ?, ?)", (kind, stripe_id, qbo_entity, qbo_id, now)
            )

    def count(self, kind: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM sync_map WHERE kind = ?", (kind,)).fetchone()[0]

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def clear(self) -> None:
        """Forget every mapping (used by tests to simulate a lost ledger)."""
        with self.db:
            self.db.execute("DELETE FROM sync_map")
