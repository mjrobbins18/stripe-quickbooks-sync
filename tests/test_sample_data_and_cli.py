import json
import re

import pytest

from stripe_qb_sync.cli import main
from stripe_qb_sync.sample_data import generate_stripe, quickbooks_seed, write_sample_data


def test_generator_is_deterministic():
    assert generate_stripe(2026) == generate_stripe(2026)
    assert generate_stripe(2026) != generate_stripe(7)


def test_payouts_equal_the_transactions_they_settle():
    data = generate_stripe()
    txns = {t["id"]: t for t in data["balance_transactions"]}
    for po in data["payouts"]:
        assert po["amount"] == sum(txns[i]["net"] for i in data["payout_balance_transactions"][po["id"]])


def test_sample_people_are_fictional():
    data = generate_stripe()
    emails = [c["email"] for c in data["customers"]] + [c["billing_details"]["email"] for c in data["charges"]]
    emails += [c["PrimaryEmailAddr"]["Address"] for c in quickbooks_seed()["Customer"]]
    assert all(re.search(r"@([a-z]+\.)?example\.(com|net|org)$", e.lower()) for e in emails)


def test_committed_sample_files_match_the_generator(tmp_path):
    write_sample_data(tmp_path)
    from pathlib import Path

    committed = Path(__file__).parent.parent / "sample_data"
    for name in ("stripe.json", "quickbooks_seed.json"):
        assert json.loads((committed / name).read_text()) == json.loads((tmp_path / name).read_text())


@pytest.fixture
def cli(tmp_path, capsys):
    base = ["--data-dir", str(tmp_path / "data"), "--state-dir", str(tmp_path / "state")]

    def run(*args):
        code = main([*base, *args])
        return code, capsys.readouterr().out

    return run


def test_cli_sync_then_report(cli):
    code, out = cli("sync", "--since", "2026-09-01")
    assert code == 0
    assert re.search(r"Sales receipts created\s+\d{2}", out)
    assert "Needs review (1)" in out

    code, out = cli("report")
    assert code == 0
    assert "MISMATCH" in out and "IN TRANSIT" in out
    assert "on/after 2026-09-01" in out  # remembers the last --since


def test_cli_rerun_is_a_no_op(cli):
    cli("sync", "--since", "2026-09-01")
    _, out = cli("sync", "--since", "2026-09-01")
    assert re.search(r"Sales receipts created\s+0\b", out)
    assert re.search(r"Bank deposits created \(payouts\)\s+0\b", out)
    assert not re.search(r"Already synced, skipped\s+0\b", out)


def test_cli_strict_report_fails_on_mismatch(cli):
    cli("sync", "--since", "2026-09-01")
    code, _ = cli("report", "--strict")
    assert code == 1


def test_cli_rejects_bad_dates(cli):
    with pytest.raises(SystemExit) as exc:
        cli("sync", "--since", "last tuesday")
    assert exc.value.code == 2
