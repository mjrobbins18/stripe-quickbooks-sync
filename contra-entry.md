# Contra work entry

## Title

Stripe → QuickBooks Sync with Automatic Reconciliation

## Description

Small businesses on Stripe lose hours re-typing payments into QuickBooks, and payouts still don't match the bank. I built a Python sync engine that turns each Stripe charge into a sales receipt, books every fee as an expense, records full and partial refunds, matches or creates customers, and groups each payout into a bank deposit. It's idempotent: every record is keyed on its Stripe ID, so reruns and crashes never create duplicates. A reconciliation report checks payout totals against deposits and flags mismatches such as chargebacks. Runs on realistic sample data, with 41 offline tests and a documented path to the live APIs.

## Tools

Stripe, QuickBooks Online, Python, SQLite, pytest, REST APIs, OAuth 2.0, Accounting Automation, Payments, Bank Reconciliation

## Attachments

- Cover: `cover.png` (1600×1200, 4:3)
- Code & README: this folder (`portfolio/stripe-quickbooks-sync/`)

**Link:** https://github.com/mjrobbins18/stripe-quickbooks-sync
