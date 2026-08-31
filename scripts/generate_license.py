#!/usr/bin/env python3
"""BillBook license generator — OWNER TOOL.

One setup = one license: every BillBook install shows a Setup ID
(XXXX-XXXX-XXXX-XXXX) on its license screen / setup wizard Step 1.
You issue a license bound to THAT Setup ID with this script. The license
only works on that machine — sharing the installer or a copied database
with someone else stays locked.

USAGE
-----
1. One-time setup (already done for the shipped app — only redo this if
   you want to invalidate ALL previously issued licenses):

     python scripts/generate_license.py --init

   This writes a new Ed25519 private key (default:
   ./billbook_license_private_key.pem) and prints the matching PUBLIC key.
   Embed that public key in app/licensing.py (_PUBLIC_KEY_B64) and rebuild
   the app. NEVER commit, share, or lose the private key file — anyone who
   has it can mint licenses, and without it no new licenses can be issued.

2. Issue a license for a customer (perpetual):

     python scripts/generate_license.py --setup-id A1B2-C3D4-E5F6-7788 \
         --name "Azhar Store"

   ...or time-limited (subscription), e.g. 90 days:

     python scripts/generate_license.py --setup-id A1B2-C3D4-E5F6-7788 \
         --name "Azhar Store" --days 90

   Send the printed key to the customer (WhatsApp/email is fine — the key
   only works on their machine). Every issued license is appended to the
   ledger CSV next to the private key so you can track who has what.
"""
import argparse
import base64
import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Make `from app import licensing` work when run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402


def do_init(key_file: Path) -> int:
    if key_file.exists():
        print(f"ERROR: {key_file} already exists — move it away first if you "
              f"really want a new keypair (this invalidates ALL issued licenses).")
        return 1
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_file.write_bytes(pem)
    key_file.chmod(0o600)
    pub_b64 = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw,
                                      serialization.PublicFormat.Raw)
    ).decode()
    print("New Ed25519 keypair generated.")
    print(f"  Private key : {key_file}  (KEEP SECRET — back this up safely)")
    print(f"  Public key  : {pub_b64}")
    print()
    print("Embed the public key in app/licensing.py:")
    print(f'    _PUBLIC_KEY_B64 = "{pub_b64}"')
    print("then rebuild the app. Licenses signed with the OLD key stop working.")
    return 0


def load_private_key(key_file: Path):
    if not key_file.exists():
        print(f"ERROR: private key not found: {key_file}")
        print("Pass --key-file PATH, set BILLBOOK_LICENSE_KEY_FILE, or run "
              "with --init to create one.")
        return None
    return serialization.load_pem_private_key(key_file.read_bytes(), password=None)


def next_license_no(ledger: Path) -> int:
    if not ledger.exists():
        return 1
    try:
        with open(ledger, newline="", encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f)) + 1
    except Exception:
        return 1


def wrap_key(key: str, width: int = 24) -> str:
    body = key[len("BBL1."):]
    lines = [body[i:i + width] for i in range(0, len(body), width)]
    return "BBL1.\n  " + "\n  ".join(lines)


def do_issue(args, key_file: Path, ledger: Path) -> int:
    from app import licensing

    priv = load_private_key(key_file)
    if priv is None:
        return 1

    sid = licensing._normalize_sid(args.setup_id)
    if len(sid) != 16:
        print(f"ERROR: invalid Setup ID {args.setup_id!r} — expected the "
              f"XXXX-XXXX-XXXX-XXXX format shown on the customer's screen.")
        return 1
    sid_display = f"{sid[0:4]}-{sid[4:8]}-{sid[8:12]}-{sid[12:16]}"

    if args.days is not None and args.expires:
        print("ERROR: use either --days or --expires, not both.")
        return 1
    now = int(time.time())
    exp = 0
    exp_note = "perpetual"
    if args.days is not None:
        if args.days <= 0:
            print("ERROR: --days must be a positive number.")
            return 1
        exp = now + args.days * 86400
        exp_note = f"expires {datetime.fromtimestamp(exp):%Y-%m-%d}"
    elif args.expires:
        try:
            exp = int(datetime.strptime(args.expires, "%Y-%m-%d").timestamp())
        except ValueError:
            print("ERROR: --expires must be YYYY-MM-DD.")
            return 1
        exp_note = f"expires {args.expires}"

    no = next_license_no(ledger)
    pem = key_file.read_bytes()
    key = licensing.make_license_key(pem, sid_display, no, now, exp or None)

    # Ledger row (kept next to the private key by default)
    new_ledger = not ledger.exists()
    with open(ledger, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_ledger:
            w.writerow(["no", "issued_at", "setup_id", "name", "expires",
                        "license_key"])
        w.writerow([no, datetime.now().isoformat(timespec="seconds"),
                    sid_display, args.name or "",
                    datetime.fromtimestamp(exp).isoformat(timespec="seconds")
                    if exp else "never", key])

    print("License issued successfully:")
    print(f"  License #   : {no}")
    print(f"  Setup ID    : {sid_display}")
    print(f"  Customer    : {args.name or '(no name)'}")
    print(f"  Validity    : {exp_note}")
    print(f"  Ledger      : {ledger}")
    print()
    print("Send this key to the customer (they paste the WHOLE thing):")
    print()
    print(wrap_key(key))
    print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="BillBook license generator (owner tool) — one setup = one license.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s --init
  %(prog)s --setup-id A1B2-C3D4-E5F6-7788 --name "Azhar Store"
  %(prog)s --setup-id A1B2-C3D4-E5F6-7788 --name "Azhar Store" --days 90
""")
    p.add_argument("--init", action="store_true",
                   help="generate a new signing keypair (invalidates old licenses)")
    p.add_argument("--setup-id", metavar="XXXX-XXXX-XXXX-XXXX",
                   help="the Setup ID shown on the customer's license screen")
    p.add_argument("--name", default="", help="customer name (for your ledger)")
    p.add_argument("--days", type=int, default=None,
                   help="time-limited license, N days from now (omit = perpetual)")
    p.add_argument("--expires", metavar="YYYY-MM-DD", default=None,
                   help="time-limited license, valid until this date")
    p.add_argument("--key-file", default=None,
                   help="path to the private key PEM (default: $BILLBOOK_LICENSE_KEY_FILE "
                        "or ./billbook_license_private_key.pem)")
    p.add_argument("--ledger", default=None,
                   help="path to the issued-licenses CSV (default: licenses_issued.csv "
                        "next to the private key)")
    args = p.parse_args()

    import os
    key_file = Path(args.key_file or os.getenv("BILLBOOK_LICENSE_KEY_FILE")
                    or "billbook_license_private_key.pem").expanduser().resolve()
    ledger = Path(args.ledger).expanduser().resolve() if args.ledger \
        else key_file.parent / "licenses_issued.csv"

    if args.init:
        return do_init(key_file)
    if not args.setup_id:
        p.error("--setup-id is required to issue a license (or use --init)")
    return do_issue(args, key_file, ledger)


if __name__ == "__main__":
    raise SystemExit(main())
