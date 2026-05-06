"""List open / overdue invoices from the connected Dataverse environment.

An invoice is considered "open" when:
  * statecode == 0 (Active), OR
  * mueller_outstandingamount > 0 (the synthetic AR column)

Default filter is the OR of both, since paid invoices may still carry an
outstanding amount > 0 if a partial payment hasn't fully closed them.

Usage::

    python -m ki_forderungsmanagement.list_open_invoices                  # all
    python -m ki_forderungsmanagement.list_open_invoices --top 50         # top 50 by outstanding desc
    python -m ki_forderungsmanagement.list_open_invoices --customer X     # one customer (substring)
    python -m ki_forderungsmanagement.list_open_invoices --csv out.csv    # export instead of pretty print
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from typing import Optional

from .config import load_config
from .http_client import DataverseClient


def _eur(amount: Optional[float]) -> str:
    if amount is None:
        return ""
    return f"{amount:>12,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _date(s: Optional[str]) -> str:
    if not s:
        return ""
    return s[:10]


def _days_overdue(due_iso: Optional[str], today) -> object:
    if not due_iso:
        return ""
    try:
        due = datetime.fromisoformat(due_iso.replace("Z", "+00:00")).date()
    except ValueError:
        return ""
    return (today - due).days


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--top", type=int, default=0,
                        help="Show only the top N rows by outstanding (0 = all)")
    parser.add_argument("--customer",
                        help="Filter by customer name (substring match)")
    parser.add_argument("--csv",
                        help="Write results to CSV file at this path")
    parser.add_argument("--statecode-only", action="store_true",
                        help="Filter on statecode=0 only (ignore mueller_outstandingamount)")
    args = parser.parse_args()

    cfg, _, limits = load_config()
    print(f"[auth] env={cfg.environment_url}", file=sys.stderr)
    client = DataverseClient(cfg, limits)

    if args.statecode_only:
        odata_filter = "statecode eq 0"
    else:
        odata_filter = "statecode eq 0 or mueller_outstandingamount gt 0"

    select_cols = ",".join([
        "invoiceid", "invoicenumber", "name", "duedate", "totalamount",
        "totaltax", "statecode", "statuscode", "createdon",
        "mueller_grossamount", "mueller_netamount", "mueller_taxamount",
        "mueller_outstandingamount", "mueller_paidamount", "mueller_partiallypaid",
        "mueller_riskscore", "mueller_dunningstage", "mueller_isdisputed",
        "_customerid_value",
    ])

    params = {
        "$select": select_cols,
        "$filter": odata_filter,
        "$expand": "customerid_account($select=name,accountnumber,emailaddress1)",
        "$orderby": "mueller_outstandingamount desc",
    }

    print(f"[query] {odata_filter}", file=sys.stderr)
    today = datetime.now(timezone.utc).date()
    rows: list[dict] = []
    for inv in client.iter_pages("invoices", params=params):
        cust = inv.get("customerid_account") or {}
        cname = cust.get("name") or ""
        if args.customer and args.customer.lower() not in cname.lower():
            continue
        # invoice.totalamount is a calculated rollup from invoicedetail lines.
        # When there are no line items (typical for AR-only seeded invoices),
        # it stays 0 - so we fall back to mueller_grossamount which we set
        # directly on the invoice header.
        gross = (inv.get("totalamount") or 0.0) or (inv.get("mueller_grossamount") or 0.0)
        rows.append({
            "invoicenumber": inv.get("invoicenumber") or "",
            "customer": cname,
            "accountnumber": cust.get("accountnumber") or "",
            "email": cust.get("emailaddress1") or "",
            "duedate": _date(inv.get("duedate")),
            "days_overdue": _days_overdue(inv.get("duedate"), today),
            "totalamount": gross,
            "outstanding": inv.get("mueller_outstandingamount") or 0.0,
            "paid": inv.get("mueller_paidamount") or 0.0,
            "risk": inv.get("mueller_riskscore") or 0.0,
            "partially_paid": bool(inv.get("mueller_partiallypaid")),
            "disputed": bool(inv.get("mueller_isdisputed")),
            "statecode": inv.get("statecode"),
        })
        if args.top and len(rows) >= args.top:
            break

    print(f"[result] {len(rows)} open invoice(s)", file=sys.stderr)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        print(f"[csv] wrote {args.csv}", file=sys.stderr)
        return

    if not rows:
        print("(no open invoices found)")
        return

    header = (f"{'Invoice':14} {'Customer':35} {'Email':30} {'Due':12} {'Days':>5} "
              f"{'Total EUR':>14} {'Outstanding':>14} {'Risk':>6} {'Flags':6}")
    print(header)
    print("-" * len(header))
    for r in rows:
        flags = []
        if r["partially_paid"]:
            flags.append("P")
        if r["disputed"]:
            flags.append("D")
        if r["days_overdue"] != "" and r["days_overdue"] > 0:
            flags.append("!")
        flag_str = "".join(flags) or " "
        print(f"{r['invoicenumber']:14} "
              f"{r['customer'][:35]:35} "
              f"{(r['email'] or '')[:30]:30} "
              f"{r['duedate']:12} "
              f"{str(r['days_overdue']):>5} "
              f"{_eur(r['totalamount'])} "
              f"{_eur(r['outstanding'])} "
              f"{r['risk']:>6.2f} "
              f"{flag_str:6}")

    total_outstanding = sum(r["outstanding"] for r in rows)
    overdue_count = sum(1 for r in rows
                        if r["days_overdue"] != "" and r["days_overdue"] > 0)
    print("-" * len(header))
    print(f"Open invoices: {len(rows):,}    "
          f"Overdue: {overdue_count:,}    "
          f"Total outstanding: {_eur(total_outstanding)} EUR")
    print("Flags: P=partially paid, D=disputed, !=overdue")


if __name__ == "__main__":
    main()
