"""
models.py - SQLAlchemy ORM models.

Column choices deliberately mirror the existing finance_data.json shape
field-for-field (including fields historically stored as strings, e.g.
'True'/'False', numeric amounts as text) so every existing endpoint,
frontend call, and piece of business logic keeps working unchanged - per
the "do not change business logic unless required for DB compatibility"
requirement. The migration script converts existing JSON values into
these columns as-is, no reformatting.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(150), nullable=False, unique=True, index=True)
    full_name = Column(String(200), default="")
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="user")
    active = Column(String(10), default="True")
    created_date = Column(String(50), default="")
    created_by = Column(String(150), default="")
    last_login = Column(String(50), default="")
    failed_login_attempts = Column(String(10), default="0")
    locked_until = Column(String(50), default="")


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_name = Column(String(200), nullable=False, index=True)
    account_type = Column(String(50), nullable=False)
    opening_balance = Column(String(50), default="0")
    current_balance = Column(String(50), default="0")
    status = Column(String(20), default="Active")
    currency = Column(String(10), default="AED")
    description = Column(Text, default="")
    created_date = Column(String(50), default="")
    created_by = Column(String(150), default="")


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category_name = Column(String(150), nullable=False, index=True)
    category_type = Column(String(50), default="Expense")
    status = Column(String(20), default="Active")
    description = Column(Text, default="")
    created_date = Column(String(50), default="")
    created_by = Column(String(150), default="")


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String(30), nullable=False, unique=True, index=True)
    date = Column(String(20), nullable=False, index=True)
    type = Column(String(50), nullable=False)
    debit_account = Column(String(200), nullable=False, index=True)
    credit_account = Column(String(200), nullable=False, index=True)
    category = Column(String(150), default="", index=True)
    amount = Column(String(50), default="0")
    description = Column(Text, default="")
    remarks = Column(Text, default="")
    created_by = Column(String(150), default="", index=True)
    timestamp = Column(String(50), default="")
    status = Column(String(20), default="Active")

    __table_args__ = (
        Index("ix_transactions_date_status", "date", "status"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    log_id = Column(String(60), nullable=False, unique=True, index=True)
    timestamp = Column(String(50), default="", index=True)
    user = Column(String(150), default="", index=True)
    action = Column(String(100), default="")
    transaction_id = Column(String(30), default="")
    details = Column(Text, default="")
    old_value = Column(Text, default="")
    new_value = Column(Text, default="")


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(150), nullable=False, unique=True, index=True)
    value = Column(Text, default="")


# Maps the storage-layer "table name" strings already used everywhere in
# code.py (db.read_all('Accounts'), db.find_by('Users', ...), etc.) to the
# matching model + its dict<->column field name mapping. This is the piece
# that lets db_adapter.py be a true drop-in replacement for LocalStorage
# without touching a single API route.
TABLE_MODEL_MAP = {
    "Users": (User, {
        "ID": "id", "Username": "username", "FullName": "full_name", "PasswordHash": "password_hash",
        "Role": "role", "Active": "active", "CreatedDate": "created_date", "CreatedBy": "created_by",
        "LastLogin": "last_login", "FailedLoginAttempts": "failed_login_attempts", "LockedUntil": "locked_until",
    }),
    "Accounts": (Account, {
        "ID": "id", "AccountName": "account_name", "AccountType": "account_type",
        "OpeningBalance": "opening_balance", "CurrentBalance": "current_balance", "Status": "status",
        "Currency": "currency", "Description": "description", "CreatedDate": "created_date", "CreatedBy": "created_by",
    }),
    "Categories": (Category, {
        "ID": "id", "CategoryName": "category_name", "CategoryType": "category_type", "Status": "status",
        "Description": "description", "CreatedDate": "created_date", "CreatedBy": "created_by",
    }),
    "Transactions": (Transaction, {
        "ID": "id", "TransactionID": "transaction_id", "Date": "date", "Type": "type",
        "DebitAccount": "debit_account", "CreditAccount": "credit_account", "Category": "category",
        "Amount": "amount", "Description": "description", "Remarks": "remarks",
        "CreatedBy": "created_by", "Timestamp": "timestamp", "Status": "status",
    }),
    "Audit Log": (AuditLog, {
        "ID": "id", "LogID": "log_id", "Timestamp": "timestamp", "User": "user", "Action": "action",
        "TransactionID": "transaction_id", "Details": "details", "OldValue": "old_value", "NewValue": "new_value",
    }),
    "Settings": (Setting, {"ID": "id", "Key": "key", "Value": "value"}),
}
