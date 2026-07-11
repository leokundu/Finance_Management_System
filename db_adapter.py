"""
db_adapter.py - PostgresStorage: a drop-in replacement for the old
LocalStorage (JSON file) class.

Same public interface (read_all, find_by, create, update, delete_record),
same "table name" strings, same dict-in/dict-out shape - so every existing
API route in code.py that calls db.read_all('Accounts'), db.find_by(...),
etc. keeps working completely unchanged. Only this file (plus models.py /
database.py) is new; nothing about routes, validation, or accounting logic
changed to support this migration, as required.
"""
import bcrypt
from datetime import datetime
from sqlalchemy import select
from database import get_session, init_db
from models import TABLE_MODEL_MAP, User, Account, Category


def _row_to_dict(row, field_map):
    """Convert an ORM row -> the same {'ID': ..., 'AccountName': ...} dict
    shape the old JSON store produced. IDs are stringified since the old
    store always stored/returned IDs as strings."""
    out = {}
    for json_key, col_name in field_map.items():
        val = getattr(row, col_name)
        out[json_key] = str(val) if json_key == "ID" and val is not None else (val if val is not None else "")
    return out


class PostgresStorage:
    def __init__(self):
        init_db()
        self._create_defaults()
        self._migrate()

    # ------------------------------------------------------------------
    # Same interface as the old LocalStorage
    # ------------------------------------------------------------------
    def read_all(self, table):
        model, field_map = TABLE_MODEL_MAP[table]
        with get_session() as session:
            rows = session.execute(select(model)).scalars().all()
            return [_row_to_dict(r, field_map) for r in rows]

    def find_by(self, table, field, value):
        model, field_map = TABLE_MODEL_MAP[table]
        col_name = field_map.get(field)
        if col_name is None:
            return []
        with get_session() as session:
            if col_name == "id":
                if not str(value).isdigit():
                    return []
                row = session.get(model, int(value))
                return [_row_to_dict(row, field_map)] if row else []
            column = getattr(model, col_name)
            # Case-insensitive match, matching the old str(...).lower() == str(...).lower() behavior
            rows = session.execute(select(model).where(column.ilike(str(value)))).scalars().all()
            return [_row_to_dict(r, field_map) for r in rows]

    def create(self, table, record):
        model, field_map = TABLE_MODEL_MAP[table]
        reverse_map = {v: k for k, v in field_map.items()}
        with get_session() as session:
            if table == "Transactions":
                count = session.query(model).count()
                record["TransactionID"] = f"TRX{count + 1:06d}"
            elif "ID" not in record:
                pass  # auto-increment PK handles this

            kwargs = {}
            for json_key, val in record.items():
                col_name = field_map.get(json_key)
                if col_name and col_name != "id":
                    kwargs[col_name] = val

            row = model(**kwargs)
            session.add(row)
            session.flush()  # populate row.id before we read it back
            record = dict(record)
            record["ID"] = str(row.id)
            if table == "Transactions":
                record["TransactionID"] = row.transaction_id
            return record

    def _get_row(self, session, model, col_name, id_value):
        if col_name == "id":
            if not str(id_value).isdigit():
                return None
            return session.get(model, int(id_value))
        column = getattr(model, col_name)
        return session.execute(select(model).where(column == id_value)).scalars().first()

    def update(self, table, id_field, id_value, data):
        model, field_map = TABLE_MODEL_MAP[table]
        col_name = field_map.get(id_field)
        if col_name is None:
            return False
        with get_session() as session:
            row = self._get_row(session, model, col_name, id_value)
            if not row:
                return False
            for json_key, val in data.items():
                target_col = field_map.get(json_key)
                if target_col and target_col != "id":
                    setattr(row, target_col, val)
            return True

    def delete_record(self, table, id_field, id_value):
        model, field_map = TABLE_MODEL_MAP[table]
        col_name = field_map.get(id_field)
        if col_name is None:
            return False
        with get_session() as session:
            row = self._get_row(session, model, col_name, id_value)
            if not row:
                return False
            session.delete(row)
            return True

    # ------------------------------------------------------------------
    # Same startup behavior as the old LocalStorage: seed defaults on a
    # fresh database, run idempotent migrations on every startup.
    # ------------------------------------------------------------------
    def _create_defaults(self):
        with get_session() as session:
            if session.query(User).count() > 0:
                return  # already initialized

            admin_hash = bcrypt.hashpw(b"Admin@123", bcrypt.gensalt(rounds=12)).decode()
            session.add(User(
                username="admin", full_name="Administrator", password_hash=admin_hash, role="admin",
                active="True", created_date=str(datetime.now()), created_by="system",
                failed_login_attempts="0", locked_until="",
            ))
            defaults = [
                ("Cash Wallet", "Cash", "5000", "5000", "Physical cash"),
                ("Bank Account", "Bank", "50000", "50000", "Main bank"),
                ("Owner's Equity", "Equity", "55000", "55000", "Owner capital"),
                ("Salary Income", "Income", "0", "0", "Salary"),
                ("Food Expenses", "Expense", "0", "0", "Food"),
            ]
            for name, atype, opening, current, desc in defaults:
                session.add(Account(
                    account_name=name, account_type=atype, opening_balance=opening, current_balance=current,
                    status="Active", currency="AED", description=desc,
                    created_date=str(datetime.now()), created_by="admin",
                ))
            cat_names = ['Food', 'Rent', 'Salary', 'Fuel', 'Shopping', 'Utilities', 'Entertainment', 'Investment', 'Others']
            for c in cat_names:
                session.add(Category(
                    category_name=c, category_type="Income" if c in ("Salary", "Investment") else "Expense",
                    status="Active", created_date=str(datetime.now()), created_by="admin",
                ))

    def _migrate(self):
        """Idempotent, additive-only migration: ensure 'Local Payment' category
        exists (same behavior as the old LocalStorage._migrate). The legacy
        'AuditLog' vs 'Audit Log' JSON key mismatch this used to fix doesn't
        apply anymore - there's one real 'audit_logs' table now."""
        with get_session() as session:
            exists = session.query(Category).filter(Category.category_name.ilike("local payment")).first()
            if not exists:
                session.add(Category(
                    category_name="Local Payment", category_type="Expense", status="Active",
                    created_date=str(datetime.now()), created_by="system",
                    description="Payments made locally (cash/in-person) - used for Local Payments KPI",
                ))
