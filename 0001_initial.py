"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(150), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), default=""),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), default="user"),
        sa.Column("active", sa.String(10), default="True"),
        sa.Column("created_date", sa.String(50), default=""),
        sa.Column("created_by", sa.String(150), default=""),
        sa.Column("last_login", sa.String(50), default=""),
        sa.Column("failed_login_attempts", sa.String(10), default="0"),
        sa.Column("locked_until", sa.String(50), default=""),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("account_type", sa.String(50), nullable=False),
        sa.Column("opening_balance", sa.String(50), default="0"),
        sa.Column("current_balance", sa.String(50), default="0"),
        sa.Column("status", sa.String(20), default="Active"),
        sa.Column("currency", sa.String(10), default="AED"),
        sa.Column("description", sa.Text, default=""),
        sa.Column("created_date", sa.String(50), default=""),
        sa.Column("created_by", sa.String(150), default=""),
    )
    op.create_index("ix_accounts_account_name", "accounts", ["account_name"])

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("category_name", sa.String(150), nullable=False),
        sa.Column("category_type", sa.String(50), default="Expense"),
        sa.Column("status", sa.String(20), default="Active"),
        sa.Column("description", sa.Text, default=""),
        sa.Column("created_date", sa.String(50), default=""),
        sa.Column("created_by", sa.String(150), default=""),
    )
    op.create_index("ix_categories_category_name", "categories", ["category_name"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("transaction_id", sa.String(30), nullable=False, unique=True),
        sa.Column("date", sa.String(20), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("debit_account", sa.String(200), nullable=False),
        sa.Column("credit_account", sa.String(200), nullable=False),
        sa.Column("category", sa.String(150), default=""),
        sa.Column("amount", sa.String(50), default="0"),
        sa.Column("description", sa.Text, default=""),
        sa.Column("remarks", sa.Text, default=""),
        sa.Column("created_by", sa.String(150), default=""),
        sa.Column("timestamp", sa.String(50), default=""),
        sa.Column("status", sa.String(20), default="Active"),
    )
    op.create_index("ix_transactions_transaction_id", "transactions", ["transaction_id"], unique=True)
    op.create_index("ix_transactions_date", "transactions", ["date"])
    op.create_index("ix_transactions_debit_account", "transactions", ["debit_account"])
    op.create_index("ix_transactions_credit_account", "transactions", ["credit_account"])
    op.create_index("ix_transactions_category", "transactions", ["category"])
    op.create_index("ix_transactions_created_by", "transactions", ["created_by"])
    op.create_index("ix_transactions_date_status", "transactions", ["date", "status"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("log_id", sa.String(60), nullable=False, unique=True),
        sa.Column("timestamp", sa.String(50), default=""),
        sa.Column("user", sa.String(150), default=""),
        sa.Column("action", sa.String(100), default=""),
        sa.Column("transaction_id", sa.String(30), default=""),
        sa.Column("details", sa.Text, default=""),
        sa.Column("old_value", sa.Text, default=""),
        sa.Column("new_value", sa.Text, default=""),
    )
    op.create_index("ix_audit_logs_log_id", "audit_logs", ["log_id"], unique=True)
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_user", "audit_logs", ["user"])

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(150), nullable=False, unique=True),
        sa.Column("value", sa.Text, default=""),
    )
    op.create_index("ix_settings_key", "settings", ["key"], unique=True)


def downgrade():
    op.drop_table("settings")
    op.drop_table("audit_logs")
    op.drop_table("transactions")
    op.drop_table("categories")
    op.drop_table("accounts")
    op.drop_table("users")
