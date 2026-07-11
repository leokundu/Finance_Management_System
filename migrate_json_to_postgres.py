"""
migrate_json_to_postgres.py - ONE-TIME migration of finance_data.json into
Supabase PostgreSQL.

Usage:
    python migrate_json_to_postgres.py [path/to/finance_data.json]

Wrapped in a single transaction: if anything fails partway through, the
whole migration rolls back and finance_data.json is left completely
untouched, so it's safe to fix the problem and re-run.

This script does NOT delete finance_data.json - do that yourself only
after you've verified (see verify_migration() at the bottom, and a manual
spot-check in your Supabase dashboard) that everything came across
correctly.
"""
import json
import sys
from database import get_session, init_db
from models import User, Account, Category, Transaction, AuditLog


def migrate(json_path="finance_data.json"):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    init_db()  # create tables if this is the very first run

    counts = {}
    with get_session() as session:
        # Guard against double-running this against a database that already
        # has data in it, which would create duplicates.
        if session.query(User).count() > 0:
            raise RuntimeError(
                "Users table is not empty - it looks like migration already ran. "
                "Aborting to avoid creating duplicates. If you really want to "
                "re-import, truncate the tables in Supabase first."
            )

        for u in data.get("Users", []):
            session.add(User(
                username=u.get("Username", ""), full_name=u.get("FullName", ""),
                password_hash=u.get("PasswordHash", ""), role=u.get("Role", "user"),
                active=u.get("Active", "True"), created_date=u.get("CreatedDate", ""),
                created_by=u.get("CreatedBy", ""), last_login=u.get("LastLogin", ""),
                failed_login_attempts=u.get("FailedLoginAttempts", "0"),
                locked_until=u.get("LockedUntil", ""),
            ))
        counts["Users"] = len(data.get("Users", []))

        for a in data.get("Accounts", []):
            session.add(Account(
                account_name=a.get("AccountName", ""), account_type=a.get("AccountType", ""),
                opening_balance=a.get("OpeningBalance", "0"), current_balance=a.get("CurrentBalance", "0"),
                status=a.get("Status", "Active"), currency=a.get("Currency", "AED"),
                description=a.get("Description", ""), created_date=a.get("CreatedDate", ""),
                created_by=a.get("CreatedBy", ""),
            ))
        counts["Accounts"] = len(data.get("Accounts", []))

        for c in data.get("Categories", []):
            session.add(Category(
                category_name=c.get("CategoryName", ""), category_type=c.get("CategoryType", "Expense"),
                status=c.get("Status", "Active"), description=c.get("Description", ""),
                created_date=c.get("CreatedDate", ""), created_by=c.get("CreatedBy", ""),
            ))
        counts["Categories"] = len(data.get("Categories", []))

        for t in data.get("Transactions", []):
            session.add(Transaction(
                transaction_id=t.get("TransactionID", ""), date=t.get("Date", ""), type=t.get("Type", ""),
                debit_account=t.get("DebitAccount", ""), credit_account=t.get("CreditAccount", ""),
                category=t.get("Category", ""), amount=str(t.get("Amount", "0")),
                description=t.get("Description", ""), remarks=t.get("Remarks", ""),
                created_by=t.get("CreatedBy", ""), timestamp=t.get("Timestamp", ""),
                status=t.get("Status", "Active"),
            ))
        counts["Transactions"] = len(data.get("Transactions", []))

        # Support both the current "Audit Log" key and the older "AuditLog"
        # key (see the _migrate() note in the old LocalStorage) so a JSON
        # file from any point in this project's history imports cleanly.
        audit_entries = data.get("Audit Log", []) or data.get("AuditLog", [])
        seen_log_ids = set()
        for log in audit_entries:
            lid = log.get("LogID", "")
            if lid in seen_log_ids:
                continue
            seen_log_ids.add(lid)
            session.add(AuditLog(
                log_id=lid, timestamp=log.get("Timestamp", ""), user=log.get("User", ""),
                action=log.get("Action", ""), transaction_id=log.get("TransactionID", ""),
                details=log.get("Details", ""), old_value=log.get("OldValue", ""),
                new_value=log.get("NewValue", ""),
            ))
        counts["Audit Log"] = len(seen_log_ids)
        # get_session() commits on clean exit here, or rolls back everything
        # above on any exception - this whole migration is one transaction.

    print("Migration complete:")
    for table, n in counts.items():
        print(f"  {table}: {n} rows")
    return counts


def verify_migration(json_path="finance_data.json"):
    """Row-count sanity check: JSON counts vs DB counts. Run this AFTER
    migrate() and BEFORE deleting finance_data.json."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    with get_session() as session:
        checks = [
            ("Users", len(data.get("Users", [])), session.query(User).count()),
            ("Accounts", len(data.get("Accounts", [])), session.query(Account).count()),
            ("Categories", len(data.get("Categories", [])), session.query(Category).count()),
            ("Transactions", len(data.get("Transactions", [])), session.query(Transaction).count()),
        ]
    ok = True
    for name, json_count, db_count in checks:
        status = "OK" if json_count == db_count else "MISMATCH"
        if status == "MISMATCH":
            ok = False
        print(f"  {name}: json={json_count} db={db_count}  [{status}]")
    if not ok:
        print("\nDO NOT delete finance_data.json - counts don't match. Investigate before proceeding.")
    else:
        print("\nAll counts match. Safe to remove finance_data.json once you've also spot-checked values in Supabase.")
    return ok


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "finance_data.json"
    migrate(path)
    print()
    verify_migration(path)
