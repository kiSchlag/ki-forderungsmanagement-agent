from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .config import load_config
from .http_client import DataverseClient


TEST_EMAIL_DEFAULT = "fawziai@outlook.com"
BACKUP_FILE_NAME = "emails_backup.json"


def find_customers_with_open_invoices(client: DataverseClient) -> list[str]:
    params = {
        "$select": "_customerid_value",
        "$filter": "statecode eq 0 or mueller_outstandingamount gt 0",
        "$top": "5000",
    }
    seen: set[str] = set()
    for inv in client.iter_pages("invoices", params=params):
        cid = inv.get("_customerid_value")
        if cid:
            seen.add(cid)
    return sorted(seen)


def fetch_account_info(client: DataverseClient, account_id: str) -> Optional[dict]:
    return client.get(
        f"accounts({account_id})",
        params={"$select": "accountid,name,emailaddress1"},
        allow_404=True,
    )


def write_backup(backup_path: Path, mapping: dict[str, dict[str, Optional[str]]]) -> Path:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = backup_path.with_suffix(backup_path.suffix + ".tmp")
    tmp.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, backup_path)
    return backup_path


def read_backup(backup_path: Path) -> dict[str, dict[str, Optional[str]]]:
    if not backup_path.is_file():
        raise SystemExit(f"Backup file not found: {backup_path}")
    return json.loads(backup_path.read_text(encoding="utf-8"))


def patch_account_email(client: DataverseClient, account_id: str, new_email: Optional[str]) -> None:
    body = {"emailaddress1": new_email if new_email is not None else None}
    client.patch(f"accounts({account_id})", json=body, extra_headers={"If-Match": "*"})


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite emailaddress1 to a test inbox for every customer with an open invoice. Idempotent. Backup-protected.",
    )
    parser.add_argument("--apply", action="store_true",
                        help="Actually PATCH (default is dry-run, prints plan only).")
    parser.add_argument("--email", default=TEST_EMAIL_DEFAULT,
                        help=f"Target test email (default: {TEST_EMAIL_DEFAULT})")
    parser.add_argument("--restore", action="store_true",
                        help="Read the backup file and restore each account's original emailaddress1, then exit.")
    parser.add_argument("--force-backup", action="store_true",
                        help="Allow overwriting an existing backup file (default refuses).")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    cfg, paths, limits = load_config()
    client = DataverseClient(cfg, limits)
    backup_path = paths.output_dir / BACKUP_FILE_NAME

    print(f"[auth] env={cfg.environment_url}", file=sys.stderr)

    if args.restore:
        return _run_restore(client, backup_path)

    return _run_set(client, args, backup_path)


def _run_set(client: DataverseClient, args: argparse.Namespace, backup_path: Path) -> int:
    customer_ids = find_customers_with_open_invoices(client)
    if not customer_ids:
        print("[result] No customers with open invoices. Nothing to do.")
        return 0

    print(f"[scan] {len(customer_ids)} distinct customer(s) with open invoices.", file=sys.stderr)

    mapping: dict[str, dict[str, Optional[str]]] = {}
    for cid in customer_ids:
        info = fetch_account_info(client, cid)
        if info is None:
            print(f"  ! account {cid} not retrievable, skipping", file=sys.stderr)
            continue
        mapping[cid] = {
            "name": info.get("name"),
            "original_email": info.get("emailaddress1"),
        }

    print(f"[plan] target email = {args.email}", file=sys.stderr)
    for cid, info in mapping.items():
        current = info["original_email"] or "(empty)"
        marker = "==" if info["original_email"] == args.email else "->"
        print(f"  {info['name']:42s}  {current:35s} {marker}  {args.email}")

    if not args.apply:
        print("\n[dry-run] no changes written. Re-run with --apply to commit.")
        return 0

    if backup_path.is_file() and not args.force_backup:
        print(f"\n[abort] Backup already exists at {backup_path}. "
              f"Refusing to overwrite. Use --force-backup to override or --restore to restore first.",
              file=sys.stderr)
        return 2

    if backup_path.is_file() and args.force_backup:
        existing = read_backup(backup_path)
        merged = dict(existing)
        added = 0
        for cid, info in mapping.items():
            if cid not in merged:
                merged[cid] = info
                added += 1
        write_backup(backup_path, merged)
        print(f"[backup] merged into {backup_path} (kept {len(existing)} existing, added {added} new)", file=sys.stderr)
        mapping = merged
    else:
        write_backup(backup_path, mapping)
        print(f"[backup] wrote {backup_path} ({len(mapping)} entries)", file=sys.stderr)

    updated = 0
    skipped = 0
    failed = 0
    for cid, info in mapping.items():
        if info["original_email"] == args.email:
            skipped += 1
            continue
        try:
            patch_account_email(client, cid, args.email)
            updated += 1
            print(f"  [ok] {info['name']}", file=sys.stderr)
        except Exception as exc:
            failed += 1
            print(f"  [fail] {info['name']}: {exc}", file=sys.stderr)

    print(f"\n[done] {len(mapping)} customer(s); updated={updated}, skipped={skipped} (already at target), failed={failed}")
    print(f"[backup] {backup_path}")
    print(f"[reminder] After your demo, run: python -m ki_forderungsmanagement.set_test_email --restore")
    return 0 if failed == 0 else 1


def _run_restore(client: DataverseClient, backup_path: Path) -> int:
    mapping = read_backup(backup_path)
    if not mapping:
        print("[result] Backup file is empty. Nothing to restore.")
        return 0

    print(f"[restore] reading {backup_path} ({len(mapping)} entries)", file=sys.stderr)

    restored = 0
    failed = 0
    for cid, info in mapping.items():
        original = info.get("original_email")
        try:
            patch_account_email(client, cid, original)
            restored += 1
            shown = original if original is not None else "(empty)"
            print(f"  [ok] {info.get('name','?')} -> {shown}", file=sys.stderr)
        except Exception as exc:
            failed += 1
            print(f"  [fail] {info.get('name','?')}: {exc}", file=sys.stderr)

    print(f"\n[done] restored={restored}, failed={failed}")
    if failed == 0:
        print(f"[cleanup] you can now safely delete {backup_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
