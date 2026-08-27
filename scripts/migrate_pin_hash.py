#!/usr/bin/env python3
"""PR 7a: One-time migration script — convert plaintext employees.pin → pin_hash.

Usage:
    python scripts/migrate_pin_hash.py

This script:
1. Reads all employees with a plaintext `pin` (and no `pin_hash`).
2. Hashes each PIN with bcrypt (14 rounds — higher than passwords due to
   smaller PIN keyspace).
3. Writes the hash to `pin_hash` and NULLs out the plaintext `pin` column.
4. Logs each migration to `activity_log` for audit.

Safe to re-run (idempotent — skips employees that already have pin_hash).
"""
import sys
import os
from pathlib import Path

# Add project root to sys.path
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from app import db, security
from app.db import log_activity


def migrate_pin_hash():
    """Migrate all plaintext PINs to bcrypt hashes."""
    db.init()
    migrated = 0
    skipped = 0
    errors = 0

    # Collect rows to migrate (read-only)
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, name, pin, pin_hash FROM employees "
            "WHERE pin IS NOT NULL AND pin != '' AND (pin_hash IS NULL OR pin_hash = '')"
        ).fetchall()
    print(f"Found {len(rows)} employees with plaintext PINs to migrate")

    # Process each row in its own transaction (avoid log_activity opening a
    # nested connection inside the main txn — "database is locked").
    for row in rows:
        try:
            pin = row["pin"]
            pin_hash = security.hash_pin(pin)
            with db.conn() as c:
                c.execute(
                    "UPDATE employees SET pin_hash = ?, pin = NULL WHERE id = ?",
                    (pin_hash, row["id"]),
                )
            migrated += 1
            print(f"  [OK] {row['name']} (id={row['id']}) → pin_hash set, plaintext pin cleared")
            # Log AFTER the txn closes (avoids nested-connection deadlock)
            log_activity(
                "pin_hash_migrated", "employee", row["id"],
                f"Migrated employee {row['name']} PIN from plaintext to bcrypt hash",
                {"employee_id": row["id"], "employee_name": row["name"]},
            )
        except Exception as e:
            errors += 1
            print(f"  [FAIL] {row['name']} (id={row['id']}): {e}")

    print(f"\nMigration complete: {migrated} migrated, {skipped} skipped, {errors} errors")
    return migrated, errors


if __name__ == "__main__":
    migrated, errors = migrate_pin_hash()
    sys.exit(1 if errors else 0)
