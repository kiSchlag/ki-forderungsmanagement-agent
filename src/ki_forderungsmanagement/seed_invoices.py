"""Seed realistic open / overdue invoices into the connected Dataverse environment.

Generates demo data for the Müller Industriebedarf AR scenario so the Copilot
Studio agent has meaningful records to operate on.

Distribution per active customer (after seeding):
  * 25% get 0 invoices (so demo can show a clean account)
  * 60% get 1-3 invoices (typical)
  * 15% get 4-7 invoices (heavy delinquents)

Customers with ``mueller_blockorders=true`` are skipped.

Each generated invoice carries:
  invoicenumber           RE-2026-NNNNN (sequential, continues from highest existing)
  name                    Short German line description
  customerid_account      bound to the active account
  duedate                 40% past (-1..-90 d), 60% future (+1..+60 d) from today
  totalamount             EUR 500..25,000
  mueller_netamount/tax/gross/taxrate (19%) consistent
  mueller_outstandingamount  = totalamount, except ~20% are partials (10..80% open)
  mueller_paidamount         = totalamount - outstanding for partials
  mueller_partiallypaid      true for partials
  mueller_riskscore          0..1; biased high (>=0.4) for past-due
  mueller_isdisputed         true for ~5%
  mueller_skontopercent (2.0), mueller_skontodeadline (duedate - 14d), mueller_skontoamount
  statecode=0 (Active), statuscode=1 (Open Billed)

Usage::

    python -m ki_forderungsmanagement.seed_invoices                      # full seed
    python -m ki_forderungsmanagement.seed_invoices --dry-run            # plan only
    python -m ki_forderungsmanagement.seed_invoices --max-customers 50   # subset
    python -m ki_forderungsmanagement.seed_invoices --seed 7             # different randomness
    python -m ki_forderungsmanagement.seed_invoices --batch-size 25      # smaller $batch
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import uuid
from datetime import date, timedelta

from .config import load_config
from .http_client import DataverseClient


OPT_BASE = 727000000  # publisher customizationoptionvalueprefix base


# ---------------------------------------------------------------------------
# German invoice line descriptions (kept short, intentionally varied)
# ---------------------------------------------------------------------------

GERMAN_INVOICE_DESCRIPTIONS = [
    "Industriesensoren Lieferung KW{N}",
    "SCADA-Lizenz Q{Q} 2026",
    "Wartungsvertrag 2026",
    "Hardware-Bestellung KW {N}",
    "Predictive Maintenance Suite",
    "OT-Cybersecurity Pack {N} Endpunkte",
    "Drucksensoren Charge {N}",
    "Steuerungsbaustein S7-1500",
    "Schraubenkomponenten Grossauftrag",
    "Edelstahl-Fittings Lieferung",
    "Notabschalt-System Modul {N}",
    "Industrie-Switch 24-Port Charge",
    "Werkzeugset Wartung Q{Q}",
    "Magnetventile Bestellung {N}",
    "Frequenzumrichter Lieferung",
    "Industrieleuchten Charge {N}",
    "Kabelsatz H07RN-F {N}m",
    "Praezisions-Messgeraet {N}",
    "Schutzhelme Werkstatt",
    "Wartungspauschale Q{Q}",
]


def german_description(rng: random.Random) -> str:
    tmpl = rng.choice(GERMAN_INVOICE_DESCRIPTIONS)
    return tmpl.format(N=rng.randint(1, 99), Q=rng.randint(1, 4))


# ---------------------------------------------------------------------------
# Field-derivation helpers
# ---------------------------------------------------------------------------

def dunningstage_for_days_overdue(days_overdue: int) -> int | None:
    """0=Friendly <14, 1=Standard 14-30, 2=Escalated 30-60, 3=FinalDemand >60."""
    if days_overdue < 0:
        return None
    if days_overdue < 14:
        return OPT_BASE + 0
    if days_overdue < 30:
        return OPT_BASE + 1
    if days_overdue < 60:
        return OPT_BASE + 2
    return OPT_BASE + 3


def build_invoice(account: dict, seq_num: int, rng: random.Random, today: date) -> dict:
    invoice_number = f"RE-2026-{seq_num:05d}"
    name = german_description(rng)

    # 40% past-due, 60% future
    if rng.random() < 0.4:
        days_offset = -rng.randint(1, 90)
    else:
        days_offset = rng.randint(1, 60)
    due_date = today + timedelta(days=days_offset)
    _invoice_date = due_date - timedelta(days=30)  # 30-day net terms

    # Totals (gross 500..25,000 EUR, derive net + tax at 19%)
    gross = round(rng.uniform(500.0, 25_000.0), 2)
    tax_rate = 19.0
    net = round(gross / (1 + tax_rate / 100.0), 2)
    tax = round(gross - net, 2)

    # Skonto: 2% if paid within 14 days before duedate
    skonto_pct = 2.0
    skonto_deadline = due_date - timedelta(days=14)
    skonto_amount = round(gross * skonto_pct / 100.0, 2)

    # 20% have a partial payment, rest fully outstanding
    if rng.random() < 0.20:
        outstanding_pct = rng.uniform(0.10, 0.80)
        outstanding = round(gross * outstanding_pct, 2)
        paid = round(gross - outstanding, 2)
        partially_paid = True
    else:
        outstanding = gross
        paid = 0.0
        partially_paid = False

    # Risk biased on past-due status
    days_overdue = (today - due_date).days
    if days_overdue > 0:
        risk = round(rng.uniform(0.4, 1.0), 2)
    else:
        risk = round(rng.uniform(0.0, 0.5), 2)

    # 5% disputed
    disputed = rng.random() < 0.05

    record = {
        "invoicenumber": invoice_number,
        "name": name[:160],
        "customerid_account@odata.bind": f"/accounts({account['accountid']})",
        # invoice.duedate is Edm.Date (DateOnly format) - must be YYYY-MM-DD
        # WITHOUT a time component, otherwise Dataverse returns 0x80048d19.
        "duedate": due_date.isoformat(),
        "totalamount": gross,
        "totaltax": tax,
        "statecode": 0,
        "statuscode": 1,
        "mueller_netamount": net,
        "mueller_taxamount": tax,
        "mueller_grossamount": gross,
        "mueller_taxrate": tax_rate,
        "mueller_outstandingamount": outstanding,
        "mueller_paidamount": paid,
        "mueller_partiallypaid": partially_paid,
        "mueller_riskscore": risk,
        "mueller_isdisputed": disputed,
        "mueller_skontopercent": skonto_pct,
        "mueller_skontodeadline": skonto_deadline.isoformat() + "T00:00:00Z",
        "mueller_skontoamount": skonto_amount,
        "mueller_nettodeadline": due_date.isoformat() + "T00:00:00Z",
    }

    # mueller_dunningstage currently has only the placeholder "Default" option;
    # widen the OptionSet before re-enabling. The stage is also derivable from
    # days-overdue at query time, so omit it here.
    _ = dunningstage_for_days_overdue(days_overdue)

    record["_is_overdue"] = days_overdue > 0
    return record


# ---------------------------------------------------------------------------
# Customer fetch + invoice-number sequencing
# ---------------------------------------------------------------------------

def fetch_active_customers(client: DataverseClient, max_customers: int | None) -> list[dict]:
    params = {
        "$select": "accountid,name,accountnumber,mueller_blockorders",
        "$filter": "statecode eq 0",
        "$orderby": "accountnumber asc",
    }
    out: list[dict] = []
    for acc in client.iter_pages("accounts", params=params):
        if acc.get("mueller_blockorders"):
            continue
        out.append(acc)
        if max_customers and len(out) >= max_customers:
            break
    return out


def query_next_invoice_seq(client: DataverseClient) -> int:
    """Find the highest existing RE-2026-NNNNN to know where to start."""
    body = client.get("invoices", params={
        "$select": "invoicenumber",
        "$filter": "startswith(invoicenumber,'RE-2026-')",
        "$orderby": "invoicenumber desc",
        "$top": "1",
    })
    items = (body or {}).get("value", [])
    if not items:
        return 1
    m = re.search(r"RE-2026-(\d+)", items[0].get("invoicenumber", ""))
    return int(m.group(1)) + 1 if m else 1


# ---------------------------------------------------------------------------
# $batch insert (Microsoft-prescribed Web API pattern, ChangeSet=atomic)
# ---------------------------------------------------------------------------

def batch_insert_invoices(client: DataverseClient, records: list[dict]) -> list[str]:
    if not records:
        return []
    boundary = f"batch_{uuid.uuid4().hex}"
    cs_boundary = f"changeset_{uuid.uuid4().hex}"
    crlf = "\r\n"
    api_version = client.cfg.api_version
    lines = [
        f"--{boundary}",
        f"Content-Type: multipart/mixed; boundary={cs_boundary}",
        "",
    ]
    for i, body in enumerate(records, 1):
        # Strip private "_"-prefixed bookkeeping keys before POST.
        clean = {k: v for k, v in body.items() if not k.startswith("_")}
        lines += [
            f"--{cs_boundary}",
            "Content-Type: application/http",
            "Content-Transfer-Encoding: binary",
            f"Content-ID: {i}",
            "",
            f"POST /api/data/{api_version}/invoices HTTP/1.1",
            "Content-Type: application/json; type=entry",
            "",
            json.dumps(clean, ensure_ascii=False),
        ]
    lines += [f"--{cs_boundary}--", f"--{boundary}--"]
    body_text = crlf.join(lines)

    url = f"https://{client.cfg.environment_url}/api/data/{api_version}/$batch"
    headers = {"Content-Type": f'multipart/mixed; boundary="{boundary}"'}

    response = None
    for attempt in range(client.limits.max_retries):
        response = client.session.post(
            url,
            data=body_text.encode("utf-8"),
            headers=headers,
            timeout=180,
        )
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", "30"))
            print(f"  ! 429 throttled, sleep {wait}s "
                  f"(attempt {attempt + 1}/{client.limits.max_retries})", file=sys.stderr)
            time.sleep(wait)
            continue
        if 500 <= response.status_code < 600:
            wait = min(2 ** attempt, 60)
            print(f"  ! {response.status_code} {response.reason} retry in {wait}s "
                  f"(attempt {attempt + 1}/{client.limits.max_retries})", file=sys.stderr)
            time.sleep(wait)
            continue
        if not response.ok:
            sys.exit(f"$batch -> {response.status_code} {response.reason}\n{response.text[:2000]}")
        break
    else:
        sys.exit(f"$batch failed after {client.limits.max_retries} retries")

    guids = re.findall(
        r"OData-EntityId:\s*[^\s]+invoices\(([0-9a-fA-F-]{36})\)", response.text)
    if len(guids) != len(records):
        err = re.search(r'"error":\s*\{[^}]+\}', response.text)
        msg = err.group(0)[:1500] if err else response.text[:1500]
        sys.exit(f"$batch returned {len(guids)} GUIDs, expected {len(records)}\n{msg}")
    return [g.lower() for g in guids]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan and print but don't POST")
    parser.add_argument("--max-customers", type=int, default=0,
                        help="Process only first N customers (default: all)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for deterministic generation (default: 42)")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Invoices per $batch ChangeSet (default: 50)")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    today = date.today()

    cfg, _, limits = load_config()
    print(f"[auth] env={cfg.environment_url}", file=sys.stderr)
    client = DataverseClient(cfg, limits)

    print("[fetch] active customers (statecode=0, mueller_blockorders!=true)...",
          file=sys.stderr)
    customers = fetch_active_customers(client, args.max_customers or None)
    print(f"[fetch] {len(customers)} eligible customers", file=sys.stderr)
    if not customers:
        sys.exit("No eligible customers found - nothing to seed.")

    if args.dry_run:
        next_seq = 1
    else:
        next_seq = query_next_invoice_seq(client)
    print(f"[seq] starting invoicenumber sequence at RE-2026-{next_seq:05d}",
          file=sys.stderr)

    plans: list[tuple[dict, int]] = []
    total_planned = 0
    for acc in customers:
        roll = rng.random()
        if roll < 0.25:
            count = 0
        elif roll < 0.85:
            count = rng.randint(1, 3)
        else:
            count = rng.randint(4, 7)
        plans.append((acc, count))
        total_planned += count

    served = sum(1 for _, c in plans if c > 0)
    print(f"[plan] {total_planned} invoices for {served} customers "
          f"({len(customers) - served} get 0 by design)", file=sys.stderr)

    pending: list[dict] = []
    cum_created = 0
    cum_outstanding = 0.0
    cum_overdue = 0
    cum_partial = 0
    cum_disputed = 0

    def flush() -> None:
        nonlocal pending, cum_created
        if not pending:
            return
        if args.dry_run:
            cum_created += len(pending)
            pending = []
            return
        guids = batch_insert_invoices(client, pending)
        cum_created += len(guids)
        pending = []

    for acc, n_invoices in plans:
        if n_invoices == 0:
            continue
        partial_local = disputed_local = overdue_local = 0
        for _ in range(n_invoices):
            inv = build_invoice(acc, next_seq, rng, today)
            next_seq += 1

            cum_outstanding += inv["mueller_outstandingamount"]
            if inv["mueller_partiallypaid"]:
                cum_partial += 1
                partial_local += 1
            if inv["mueller_isdisputed"]:
                cum_disputed += 1
                disputed_local += 1
            if inv.get("_is_overdue"):
                cum_overdue += 1
                overdue_local += 1

            pending.append(inv)
            if len(pending) >= args.batch_size:
                flush()

        bits = []
        if partial_local:
            bits.append(f"{partial_local} partial")
        if disputed_local:
            bits.append(f"{disputed_local} disputed")
        if overdue_local:
            bits.append(f"{overdue_local} overdue")
        suffix = f" ({', '.join(bits)})" if bits else ""
        prefix = "[dry-run]" if args.dry_run else "[seed]"
        print(f"{prefix} {acc.get('name', '?'):<50} "
              f"created {n_invoices} invoice(s){suffix}", file=sys.stderr)

    flush()

    print(f"\n=== {'Dry-run' if args.dry_run else 'Seeding'} complete ===",
          file=sys.stderr)
    print(f"  Total invoices created: {cum_created:,}", file=sys.stderr)
    print(f"  Total outstanding:      EUR {cum_outstanding:,.2f}", file=sys.stderr)
    print(f"  Overdue:                {cum_overdue:,}", file=sys.stderr)
    print(f"  Partial payments:       {cum_partial:,}", file=sys.stderr)
    print(f"  Disputed:               {cum_disputed:,}", file=sys.stderr)
    if args.dry_run:
        print("  (no records were written - run without --dry-run to apply)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
