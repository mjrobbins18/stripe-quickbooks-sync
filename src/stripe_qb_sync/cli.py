"""Command line: ``stripe-qb sync --since DATE`` and ``stripe-qb report``.

Offline by default: Stripe comes from ``sample_data/stripe.json`` and QuickBooks is a local
fake company in ``.sync-state/quickbooks.json``. ``--source stripe`` / ``--sink qbo`` switch
to the real APIs (see README → Going live).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from .engine import SetupError, SyncEngine
from .ledger import Ledger
from .report import build_report
from .sample_data import DEFAULT_SEED, write_sample_data
from .sinks.fake import FakeQuickBooks
from .sources.fake import FakeStripeSource


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}")


class Context:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.data_dir = Path(args.data_dir)
        self.state_dir = Path(args.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.state_dir / "ledger.sqlite")
        self.source = self._source()
        self.sink = self._sink()

    def _source(self):
        if self.args.source == "stripe":
            from .sources.stripe_api import StripeApiSource

            return StripeApiSource()
        path = self.data_dir / "stripe.json"
        if not path.exists():
            write_sample_data(self.data_dir)
        return FakeStripeSource.from_file(path)

    def _sink(self):
        if self.args.sink == "qbo":
            from .sinks.qbo_api import QuickBooksOnlineSink

            return QuickBooksOnlineSink()
        self.company_path = self.state_dir / "quickbooks.json"
        if self.company_path.exists():
            return FakeQuickBooks.load(self.company_path)
        seed_path = self.data_dir / "quickbooks_seed.json"
        if not seed_path.exists():
            write_sample_data(self.data_dir)
        return FakeQuickBooks.from_seed(json.loads(seed_path.read_text()))

    def save(self) -> None:
        if isinstance(self.sink, FakeQuickBooks):
            self.sink.save(self.company_path)


def cmd_sync(args) -> int:
    ctx = Context(args)
    try:
        result = SyncEngine(ctx.source, ctx.sink, ctx.ledger).run(args.since)
    finally:
        ctx.save()  # keep whatever was written, so a crash mid-run is still idempotent on retry
    print(f"Synced Stripe activity since {args.since} into QuickBooks\n")
    print(result.summary())
    return 0


def cmd_report(args) -> int:
    ctx = Context(args)
    since = args.since or _date(ctx.ledger.get_meta("last_since") or "2000-01-01")
    report = build_report(ctx.source, ctx.sink, ctx.ledger, since)
    print(report.render())
    return 1 if args.strict and report.problems else 0


def cmd_generate(args) -> int:
    stripe_path, qbo_path = write_sample_data(args.data_dir, args.seed)
    print(f"Wrote {stripe_path} and {qbo_path} (seed {args.seed})")
    return 0


def cmd_reset(args) -> int:
    shutil.rmtree(args.state_dir, ignore_errors=True)
    print(f"Removed {args.state_dir}: the next sync starts from the QuickBooks seed with an empty ledger")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stripe-qb", description=__doc__.splitlines()[0])
    p.add_argument("--data-dir", default=os.environ.get("SYNC_DATA_DIR", "sample_data"))
    p.add_argument("--state-dir", default=os.environ.get("SYNC_STATE_DIR", ".sync-state"))
    p.add_argument("--source", choices=["fake", "stripe"], default=os.environ.get("SYNC_SOURCE", "fake"))
    p.add_argument("--sink", choices=["fake", "qbo"], default=os.environ.get("SYNC_SINK", "fake"))
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sync", help="push Stripe activity into QuickBooks (safe to rerun)")
    s.add_argument("--since", type=_date, required=True, help="first day to sync, YYYY-MM-DD")
    s.set_defaults(func=cmd_sync)

    r = sub.add_parser("report", help="reconcile Stripe payouts against QuickBooks deposits")
    r.add_argument("--since", type=_date, help="defaults to the --since of the last sync")
    r.add_argument("--strict", action="store_true", help="exit 1 if any payout needs attention")
    r.set_defaults(func=cmd_report)

    g = sub.add_parser("generate", help="rewrite the sample data files")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.set_defaults(func=cmd_generate)

    x = sub.add_parser("reset", help="delete the local ledger and fake QuickBooks company")
    x.set_defaults(func=cmd_reset)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
