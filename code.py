"""
🏦 Finance Management System - FastAPI Backend + HTML Frontend
Version: 6.1.0 - Complete CRUD + Reports
Run: python finance_system.py
Open: http://localhost:8000
"""

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime, date, timedelta
import bcrypt
import uuid
import logging
import uvicorn
import socket

# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    APP_NAME = "Finance Management System"
    VERSION = "6.1.0"
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "Admin@123"
    CURRENCY = "AED"
    CURRENCY_SYMBOL = "Dhs"

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# LOCAL STORAGE
# ============================================================================

# ============================================================================
# POSTGRESQL STORAGE (Supabase) - replaces the old JSON-file LocalStorage
# ----------------------------------------------------------------------------
# PostgresStorage (db_adapter.py) exposes the exact same interface the old
# LocalStorage did - read_all(table), find_by(table, field, value),
# create(table, record), update(table, id_field, id_value, data),
# delete_record(table, id_field, id_value) - using the same "table name"
# strings ('Users', 'Accounts', 'Categories', 'Transactions', 'Audit Log').
# Every route below that calls db.* is completely unchanged.
# ============================================================================
from db_adapter import PostgresStorage

db = PostgresStorage()

# ============================================================================
# ACCOUNTING ENGINE
# ============================================================================

class AccountingEngine:
    @staticmethod
    def calculate_balance(account_name: str) -> float:
        acc = next((a for a in db.read_all('Accounts') if a['AccountName'] == account_name), None)
        if not acc: return 0.0
        opening = float(acc.get('OpeningBalance', 0))
        trx = db.read_all('Transactions')
        active = [t for t in trx if t.get('Status') == 'Active']
        debits = sum(float(t.get('Amount',0)) for t in active if t.get('DebitAccount') == account_name)
        credits = sum(float(t.get('Amount',0)) for t in active if t.get('CreditAccount') == account_name)
        atype = acc.get('AccountType','')
        if atype in ['Asset','Cash','Bank','Credit Card','Expense']:
            return round(opening + debits - credits, 2)
        return round(opening + credits - debits, 2)
    
    @staticmethod
    def get_all_balances():
        result = {}
        for acc in db.read_all('Accounts'):
            if acc.get('Status') == 'Active':
                result[acc['AccountName']] = {
                    'balance': AccountingEngine.calculate_balance(acc['AccountName']),
                    'type': acc.get('AccountType',''),
                    'id': acc.get('ID','')
                }
        return result
    
    @staticmethod
    def get_kpi():
        trx = db.read_all('Transactions')
        active = [t for t in trx if t.get('Status') == 'Active']
        income = sum(float(t.get('Amount',0)) for t in active if t.get('Type') == 'Income')
        expense = sum(float(t.get('Amount',0)) for t in active if t.get('Type') == 'Expense')
        deposits = sum(float(t.get('Amount',0)) for t in active if t.get('Type') == 'Owner Deposit')
        withdrawals = sum(float(t.get('Amount',0)) for t in active if t.get('Type') == 'Owner Withdrawal')

        # Total Transfer: cumulative amount of every Transfer-type transaction
        # since the beginning of the database (matches the same 'active' /
        # not-cancelled filter used by every other KPI here).
        transfer_trx = [t for t in active if t.get('Type') == 'Transfer']
        total_transfer = sum(float(t.get('Amount', 0)) for t in transfer_trx)
        transfer_count = len(transfer_trx)

        # Local Payments: "Local Payment" is modeled as a Category (not a
        # transaction Type) in this system's data - see the Categories table.
        # Match on Category first; also match Type for forward-compatibility
        # in case a future version records it as a Type instead.
        def _is_local_payment(t):
            return (str(t.get('Category', '')).strip().lower() == 'local payment'
                    or str(t.get('Type', '')).strip().lower() == 'local payment')
        local_payment_trx = [t for t in active if _is_local_payment(t)]
        total_local_payments = sum(float(t.get('Amount', 0)) for t in local_payment_trx)
        local_payment_count = len(local_payment_trx)

        # Total Balance: sum of the balances of every active Asset, Cash,
        # Bank, Wallet, and Credit Card account, using the same
        # calculate_balance() accounting logic the rest of the app relies on
        # (opening balance +/- debits/credits per account type). This is
        # deliberately different from net_worth (income - expense); it's a
        # real cash-position figure, not a P&L figure.
        BALANCE_ACCOUNT_TYPES = {'Asset', 'Cash', 'Bank', 'Wallet', 'Credit Card'}
        total_balance = sum(
            AccountingEngine.calculate_balance(a['AccountName'])
            for a in db.read_all('Accounts')
            if a.get('Status') == 'Active' and a.get('AccountType') in BALANCE_ACCOUNT_TYPES
        )

        today_str = date.today().isoformat()
        today_trx = [t for t in active if t.get('Date','') == today_str]
        week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        week_trx = [t for t in active if t.get('Date','') >= week_start]
        month_str = today_str[:7]
        month_trx = [t for t in active if t.get('Date','')[:7] == month_str]
        return {
            'total_income': income, 'total_expense': expense,
            'net_worth': income - expense, 'total_deposits': deposits,
            'total_withdrawals': withdrawals, 'total_transactions': len(active),
            'total_transfer': round(total_transfer, 2), 'transfer_count': transfer_count,
            'total_local_payments': round(total_local_payments, 2), 'local_payment_count': local_payment_count,
            'total_balance': round(total_balance, 2),
            'today_count': len(today_trx), 'today_amount': sum(float(t.get('Amount',0)) for t in today_trx),
            'week_count': len(week_trx), 'week_amount': sum(float(t.get('Amount',0)) for t in week_trx),
            'month_count': len(month_trx), 'month_amount': sum(float(t.get('Amount',0)) for t in month_trx),
            'balances': AccountingEngine.get_all_balances()
        }

# ============================================================================
# PASSWORD MANAGER
# ============================================================================

class PasswordManager:
    @staticmethod
    def hash(pwd: str) -> str:
        return bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(rounds=12)).decode()
    
    @staticmethod
    def verify(pwd: str, hashed: str) -> bool:
        try: return bcrypt.checkpw(pwd.encode(), hashed.encode())
        except: return False
    
    @staticmethod
    def generate(length=12):
        import secrets, string
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(chars) for _ in range(length))

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(title=Config.APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
sessions = {}

# ============================================================================
# MODELS
# ============================================================================

class LoginData(BaseModel):
    username: str
    password: str

# ============================================================================
# DOUBLE-ENTRY ACCOUNTING RULES - SINGLE SOURCE OF TRUTH
# ----------------------------------------------------------------------------
# Root cause of the dropdown misclassification bug: the Debit/Credit
# AccountType rules previously existed ONLY inside the frontend JavaScript
# (a hand-maintained mapping), and the "else" branch used for Transfer and
# Adjustment types didn't filter by AccountType at all - it returned every
# account, including Income/Expense/Equity ones. There was no backend
# validation to catch that, so invalid combinations (e.g. an Income account
# offered as a Debit, or an Equity account offered on both sides of a
# Transfer) could reach the API undetected.
#
# The permanent fix is to define these rules exactly once, here, and have
# both the API (validation) and the frontend (dropdown population, via
# GET /api/transaction-rules) read from this single dict. Nothing about
# double-entry logic is hardcoded in the frontend anymore - it's dynamic,
# driven entirely by AccountType.
#
# A value of None for 'debit' or 'credit' means "any AccountType is valid"
# (used for Adjustment, which is a free-form correction entry by design).
# ============================================================================
TRANSACTION_TYPE_RULES = {
    'Income':            {'debit': ['Cash', 'Bank', 'Asset', 'Credit Card'],           'credit': ['Income']},
    'Expense':           {'debit': ['Expense'],                                        'credit': ['Cash', 'Bank', 'Asset', 'Credit Card']},
    'Owner Deposit':     {'debit': ['Cash', 'Bank', 'Asset'],                           'credit': ['Equity']},
    'Owner Withdrawal':  {'debit': ['Equity'],                                          'credit': ['Cash', 'Bank', 'Asset']},
    'Transfer':          {'debit': ['Cash', 'Bank', 'Asset', 'Credit Card'],            'credit': ['Cash', 'Bank', 'Asset', 'Credit Card']},
    'Adjustment':        {'debit': None,                                                'credit': None},
}

def _account_type_of(name: str):
    matches = db.find_by('Accounts', 'AccountName', name)
    return matches[0].get('AccountType') if matches else None

def _validate_transaction_accounts(ttype: str, debit_name: str, credit_name: str):
    rules = TRANSACTION_TYPE_RULES.get(ttype)
    if rules is None:
        # Unknown/custom Type: fall back to the universal double-entry rule
        # (both accounts must exist and must differ) rather than silently
        # allowing anything.
        rules = {'debit': None, 'credit': None}

    debit_type = _account_type_of(debit_name)
    credit_type = _account_type_of(credit_name)
    if debit_type is None:
        raise HTTPException(400, f"Debit account '{debit_name}' does not exist")
    if credit_type is None:
        raise HTTPException(400, f"Credit account '{credit_name}' does not exist")

    allowed_debit = rules['debit']
    allowed_credit = rules['credit']
    if allowed_debit is not None and debit_type not in allowed_debit:
        raise HTTPException(400, f"Invalid Debit account for {ttype}: '{debit_name}' is AccountType "
                                  f"'{debit_type}', but {ttype} requires Debit to be one of {allowed_debit}")
    if allowed_credit is not None and credit_type not in allowed_credit:
        raise HTTPException(400, f"Invalid Credit account for {ttype}: '{credit_name}' is AccountType "
                                  f"'{credit_type}', but {ttype} requires Credit to be one of {allowed_credit}")
    if debit_name == credit_name and ttype != 'Adjustment':
        raise HTTPException(400, "Debit and Credit accounts must be different (unless Type is Adjustment)")


class TransactionData(BaseModel):
    Date: str
    Type: str
    DebitAccount: str
    CreditAccount: str
    Category: str = ""
    Amount: float
    Description: str = ""
    Remarks: str = ""

ALLOWED_CURRENCIES = ['AED', 'INR', 'USD']

class AccountData(BaseModel):
    AccountName: str
    AccountType: str
    OpeningBalance: float = 0.0
    Currency: str = "AED"
    Description: str = ""

class CategoryData(BaseModel):
    CategoryName: str
    CategoryType: str
    Description: str = ""

class UserData(BaseModel):
    Username: str
    FullName: str
    Password: str
    Role: str = "user"

class PasswordResetData(BaseModel):
    UserID: str
    NewPassword: str = ""

class RoleChangeData(BaseModel):
    UserID: str
    Role: str

# ============================================================================
# AUTH HELPERS
# ============================================================================

def get_current_user(request: Request):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    user = sessions.get(token)
    if not user: raise HTTPException(401, "Not authenticated")
    return user

def require_admin(request: Request):
    user = get_current_user(request)
    if user.get('Role') != 'admin': raise HTTPException(403, "Admin only")
    return user

# ============================================================================
# AUTH API
# ============================================================================

@app.post("/api/login")
def api_login(data: LoginData):
    # NOTE: distinct "no such user" vs "wrong password" messages are given
    # here on purpose, per product requirement. Trade-off: this makes it
    # possible to enumerate valid usernames by observing which error comes
    # back, which a generic "Invalid credentials" message would prevent.
    # The lockout below (5 failed attempts -> 15 min lock) limits how much
    # that can be abused for brute-forcing.
    username = data.username.strip().lower()
    users = db.find_by('Users', 'Username', username)
    if not users:
        raise HTTPException(401, "No account found with that username")
    user = users[0]
    if user.get('Active','True').lower() != 'true':
        raise HTTPException(403, "This account has been disabled")

    locked_until = user.get('LockedUntil') or ''
    if locked_until:
        try:
            if datetime.fromisoformat(locked_until) > datetime.now():
                raise HTTPException(403, f"Too many failed attempts. Try again after {locked_until}")
        except ValueError:
            pass

    if not PasswordManager.verify(data.password, user.get('PasswordHash','')):
        attempts = int(user.get('FailedLoginAttempts', 0) or 0) + 1
        update = {'FailedLoginAttempts': str(attempts)}
        if attempts >= 5:
            update['LockedUntil'] = (datetime.now() + timedelta(minutes=15)).isoformat()
            db.update('Users', 'ID', user['ID'], update)
            raise HTTPException(403, "Too many failed attempts. Account locked for 15 minutes.")
        db.update('Users', 'ID', user['ID'], update)
        raise HTTPException(401, "Incorrect password")

    db.update('Users', 'ID', user['ID'], {'FailedLoginAttempts': '0', 'LockedUntil': '', 'LastLogin': datetime.now().isoformat()})
    token = str(uuid.uuid4())
    sessions[token] = user
    return {"status": "success", "token": token, "user": {"username": user['Username'], "fullName": user['FullName'], "role": user['Role']}}

@app.post("/api/logout")
def api_logout(request: Request):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    sessions.pop(token, None)
    return {"status": "success"}

# ============================================================================
# DASHBOARD API
# ============================================================================

@app.get("/api/dashboard")
def api_dashboard(request: Request):
    get_current_user(request)
    return AccountingEngine.get_kpi()

# ============================================================================
# ACCOUNTS API - COMPLETE CRUD
# ============================================================================

@app.get("/api/accounts")
def api_get_accounts(request: Request):
    get_current_user(request)
    accounts = db.read_all('Accounts')
    for acc in accounts:
        acc['CurrentBalance'] = str(AccountingEngine.calculate_balance(acc['AccountName']))
    return accounts

ALLOWED_ACCOUNT_TYPES = ['Cash', 'Bank', 'Credit Card', 'Asset', 'Liability', 'Equity', 'Income', 'Expense']

def _validate_account_fields(account_type=None, currency=None):
    if account_type is not None and account_type not in ALLOWED_ACCOUNT_TYPES:
        raise HTTPException(400, f"AccountType must be one of {ALLOWED_ACCOUNT_TYPES}")
    if currency is not None and currency not in ALLOWED_CURRENCIES:
        raise HTTPException(400, f"Currency must be one of {ALLOWED_CURRENCIES}")

@app.post("/api/accounts")
def api_create_account(data: AccountData, request: Request):
    user = require_admin(request)
    _validate_account_fields(data.AccountType, data.Currency)
    record = {
        'AccountName': data.AccountName, 'AccountType': data.AccountType,
        'OpeningBalance': str(data.OpeningBalance), 'CurrentBalance': str(data.OpeningBalance),
        'Status': 'Active', 'CreatedDate': datetime.now().isoformat(),
        'CreatedBy': user['Username'], 'Description': data.Description, 'Currency': data.Currency
    }
    created = db.create('Accounts', record)
    _log_audit(user['Username'], 'CREATE_ACCOUNT', created.get('ID',''), str(record))
    return {"status": "success", "data": created}

@app.put("/api/accounts/{account_id}")
def api_update_account(account_id: str, data: dict, request: Request):
    user = require_admin(request)
    existing = db.find_by('Accounts', 'ID', account_id)
    if not existing:
        raise HTTPException(404, "Account not found")
    _validate_account_fields(data.get('AccountType'), data.get('Currency'))
    old_value = str(existing[0])
    if not db.update('Accounts', 'ID', account_id, data):
        raise HTTPException(404, "Account not found")
    _log_audit(user['Username'], 'UPDATE_ACCOUNT', account_id, f"OLD: {old_value} | NEW: {data}")
    return {"status": "success"}

@app.delete("/api/accounts/{account_id}")
def api_delete_account(account_id: str, request: Request):
    user = require_admin(request)
    existing = db.find_by('Accounts', 'ID', account_id)
    if not db.delete_record('Accounts', 'ID', account_id):
        raise HTTPException(404, "Account not found")
    _log_audit(user['Username'], 'DELETE_ACCOUNT', account_id, str(existing[0]) if existing else '')
    return {"status": "success"}

@app.put("/api/accounts/{account_id}/disable")
def api_disable_account(account_id: str, request: Request):
    user = require_admin(request)
    if not db.update('Accounts', 'ID', account_id, {'Status': 'Inactive'}):
        raise HTTPException(404, "Account not found")
    _log_audit(user['Username'], 'DISABLE_ACCOUNT', account_id, 'Status set to Inactive')
    return {"status": "success"}

@app.put("/api/accounts/{account_id}/enable")
def api_enable_account(account_id: str, request: Request):
    user = require_admin(request)
    if not db.update('Accounts', 'ID', account_id, {'Status': 'Active'}):
        raise HTTPException(404, "Account not found")
    _log_audit(user['Username'], 'ENABLE_ACCOUNT', account_id, 'Status set to Active')
    return {"status": "success"}

class BalanceCorrectionData(BaseModel):
    NewOpeningBalance: float
    Reason: str = ""

@app.post("/api/accounts/{account_id}/correct-balance")
def api_correct_balance(account_id: str, data: BalanceCorrectionData, request: Request):
    # Deliberately only ever touches OpeningBalance, never CurrentBalance
    # (which is always derived live from the ledger by AccountingEngine) and
    # never rewrites transaction history. This is the "safe" way to fix a
    # starting balance: the full ledger of debits/credits since then still
    # applies on top of the corrected opening figure.
    user = require_admin(request)
    matches = db.find_by('Accounts', 'ID', account_id)
    if not matches:
        raise HTTPException(404, "Account not found")
    old_balance = matches[0].get('OpeningBalance')
    db.update('Accounts', 'ID', account_id, {'OpeningBalance': str(data.NewOpeningBalance)})
    _log_audit(user['Username'], 'CORRECT_BALANCE', account_id,
               f"OpeningBalance {old_balance} -> {data.NewOpeningBalance}. Reason: {data.Reason}")
    return {"status": "success", "new_balance": AccountingEngine.calculate_balance(matches[0]['AccountName'])}

# ============================================================================
# CATEGORIES API - COMPLETE CRUD
# ============================================================================

@app.get("/api/categories")
def api_get_categories(request: Request):
    get_current_user(request)
    return db.read_all('Categories')

@app.post("/api/categories")
def api_create_category(data: CategoryData, request: Request):
    user = require_admin(request)
    record = {
        'CategoryName': data.CategoryName, 'CategoryType': data.CategoryType,
        'Status': 'Active', 'CreatedDate': datetime.now().isoformat(),
        'CreatedBy': user['Username'], 'Description': data.Description
    }
    created = db.create('Categories', record)
    return {"status": "success", "data": created}

@app.put("/api/categories/{category_id}")
def api_update_category(category_id: str, data: dict, request: Request):
    require_admin(request)
    db.update('Categories', 'ID', category_id, data)
    return {"status": "success"}

@app.delete("/api/categories/{category_id}")
def api_delete_category(category_id: str, request: Request):
    require_admin(request)
    db.delete_record('Categories', 'ID', category_id)
    return {"status": "success"}

# ============================================================================
# TRANSACTIONS API - COMPLETE CRUD
# ============================================================================

@app.get("/api/transactions")
def api_get_transactions(request: Request, type: str = "", account: str = "", user: str = "", 
                         date_from: str = "", date_to: str = "", category: str = ""):
    get_current_user(request)
    trx = db.read_all('Transactions')
    if type: trx = [t for t in trx if t.get('Type') == type]
    if account: trx = [t for t in trx if t.get('DebitAccount') == account or t.get('CreditAccount') == account]
    if user: trx = [t for t in trx if t.get('CreatedBy','').lower() == user.lower()]
    if date_from: trx = [t for t in trx if t.get('Date','') >= date_from]
    if date_to: trx = [t for t in trx if t.get('Date','') <= date_to]
    if category: trx = [t for t in trx if t.get('Category','') == category]
    return sorted(trx, key=lambda x: x.get('Date',''), reverse=True)

@app.post("/api/transactions")
def api_create_transaction(data: TransactionData, request: Request):
    user = get_current_user(request)
    _validate_transaction_accounts(data.Type, data.DebitAccount, data.CreditAccount)
    record = {
        'Date': data.Date, 'Type': data.Type,
        'DebitAccount': data.DebitAccount, 'CreditAccount': data.CreditAccount,
        'Category': data.Category, 'Amount': str(data.Amount),
        'Description': data.Description, 'Remarks': data.Remarks,
        'CreatedBy': user['Username'], 'Timestamp': datetime.now().isoformat(), 'Status': 'Active'
    }
    created = db.create('Transactions', record)
    _log_audit(user['Username'], 'CREATE_TRANSACTION', created.get('TransactionID',''), str(record))
    return {"status": "success", "data": created}

@app.put("/api/transactions/{trx_id}")
def api_update_transaction(trx_id: str, data: dict, request: Request):
    # Editing a transaction changes historical accounting data, so this is
    # restricted to admins. Normal users can still create new transactions.
    user = require_admin(request)
    existing = db.find_by('Transactions', 'TransactionID', trx_id)
    if not existing:
        raise HTTPException(404, "Transaction not found")
    debit = data.get('DebitAccount', existing[0].get('DebitAccount'))
    credit = data.get('CreditAccount', existing[0].get('CreditAccount'))
    ttype = data.get('Type', existing[0].get('Type'))
    _validate_transaction_accounts(ttype, debit, credit)
    old_value = str(existing[0])
    db.update('Transactions', 'TransactionID', trx_id, data)
    _log_audit(user['Username'], 'UPDATE_TRANSACTION', trx_id, str(data))
    return {"status": "success"}

@app.get("/api/transaction-rules")
def api_get_transaction_rules(request: Request):
    # The single source of truth for which AccountTypes are valid on the
    # Debit/Credit side of each transaction Type. The frontend fetches this
    # instead of hardcoding the mapping, so there is exactly one place
    # (TRANSACTION_TYPE_RULES above) that defines double-entry logic for the
    # whole application.
    get_current_user(request)
    return TRANSACTION_TYPE_RULES

@app.delete("/api/transactions/{trx_id}")
def api_delete_transaction(trx_id: str, request: Request):
    # Soft-delete only (Status -> Cancelled); the record and audit trail are
    # preserved. Restricted to admins.
    user = require_admin(request)
    if not db.find_by('Transactions', 'TransactionID', trx_id):
        raise HTTPException(404, "Transaction not found")
    db.update('Transactions', 'TransactionID', trx_id, {'Status': 'Cancelled'})
    _log_audit(user['Username'], 'DELETE_TRANSACTION', trx_id, 'Status set to Cancelled')
    return {"status": "success"}

@app.post("/api/transactions/{trx_id}/reverse")
def api_reverse_transaction(trx_id: str, request: Request):
    # Creates a new offsetting transaction (Debit/Credit swapped, same
    # amount) rather than mutating history, so the original entry and the
    # reversal are both auditable. Restricted to admins.
    user = require_admin(request)
    matches = db.find_by('Transactions', 'TransactionID', trx_id)
    if not matches:
        raise HTTPException(404, "Transaction not found")
    original = matches[0]
    if original.get('Status') != 'Active':
        raise HTTPException(400, "Only active transactions can be reversed")
    reversal = {
        'Date': date.today().isoformat(), 'Type': original.get('Type'),
        'DebitAccount': original.get('CreditAccount'), 'CreditAccount': original.get('DebitAccount'),
        'Category': original.get('Category', ''), 'Amount': original.get('Amount'),
        'Description': f"Reversal of {trx_id}", 'Remarks': original.get('Description', ''),
        'CreatedBy': user['Username'], 'Timestamp': datetime.now().isoformat(), 'Status': 'Active'
    }
    created = db.create('Transactions', reversal)
    db.update('Transactions', 'TransactionID', trx_id, {'Status': 'Reversed'})
    _log_audit(user['Username'], 'REVERSE_TRANSACTION', trx_id, f"Reversed by {created.get('TransactionID')}")
    return {"status": "success", "data": created}

# ============================================================================
# USERS API - COMPLETE CRUD + PASSWORD RESET + ROLE CHANGE
# ============================================================================

@app.get("/api/users")
def api_get_users(request: Request):
    require_admin(request)
    users = db.read_all('Users')
    return [{k:v for k,v in u.items() if k != 'PasswordHash'} for u in users]

@app.post("/api/users")
def api_create_user(data: UserData, request: Request):
    require_admin(request)
    if db.find_by('Users', 'Username', data.Username.lower()):
        raise HTTPException(400, "Username exists")
    record = {
        'Username': data.Username.lower(), 'FullName': data.FullName,
        'PasswordHash': PasswordManager.hash(data.Password), 'Role': data.Role,
        'Active': 'True', 'CreatedDate': datetime.now().isoformat(),
        'CreatedBy': get_current_user(request)['Username'],
        'FailedLoginAttempts': '0', 'LockedUntil': ''
    }
    created = db.create('Users', record)
    return {"status": "success", "data": {"ID": created['ID'], "Username": created['Username']}}

@app.put("/api/users/{user_id}")
def api_update_user(user_id: str, data: dict, request: Request):
    require_admin(request)
    db.update('Users', 'ID', user_id, data)
    return {"status": "success"}

@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: str, request: Request):
    require_admin(request)
    db.delete_record('Users', 'ID', user_id)
    return {"status": "success"}

@app.post("/api/users/reset-password")
def api_reset_password(data: PasswordResetData, request: Request):
    require_admin(request)
    new_pwd = data.NewPassword if data.NewPassword else PasswordManager.generate()
    hashed = PasswordManager.hash(new_pwd)
    db.update('Users', 'ID', data.UserID, {'PasswordHash': hashed})
    return {"status": "success", "new_password": new_pwd if not data.NewPassword else "Password changed"}

@app.put("/api/users/{user_id}/disable")
def api_disable_user(user_id: str, request: Request):
    require_admin(request)
    db.update('Users', 'ID', user_id, {'Active': 'False'})
    return {"status": "success"}

@app.put("/api/users/{user_id}/enable")
def api_enable_user(user_id: str, request: Request):
    require_admin(request)
    db.update('Users', 'ID', user_id, {'Active': 'True'})
    return {"status": "success"}

@app.put("/api/users/{user_id}/role")
def api_change_role(user_id: str, data: RoleChangeData, request: Request):
    require_admin(request)
    db.update('Users', 'ID', user_id, {'Role': data.Role})
    return {"status": "success"}

# ============================================================================
# REPORTS API - EXPANDED
# ============================================================================

@app.get("/api/reports")
def api_reports(request: Request, report_type: str = "summary"):
    get_current_user(request)
    trx = db.read_all('Transactions')
    active = [t for t in trx if t.get('Status') == 'Active']
    today = date.today()
    
    result = {
        'kpi': AccountingEngine.get_kpi(),
        'balances': AccountingEngine.get_all_balances()
    }
    
    if report_type == "summary" or report_type == "all":
        # Daily summary (last 30 days)
        daily = {}
        for i in range(30):
            d = (today - timedelta(days=i)).isoformat()
            day_trx = [t for t in active if t.get('Date','') == d]
            daily[d] = {
                'income': sum(float(t.get('Amount',0)) for t in day_trx if t.get('Type') == 'Income'),
                'expense': sum(float(t.get('Amount',0)) for t in day_trx if t.get('Type') == 'Expense'),
                'count': len(day_trx)
            }
        result['daily_summary'] = daily
        
        # Weekly summary (last 12 weeks)
        weekly = {}
        for i in range(12):
            week_start = today - timedelta(days=today.weekday() + (i*7))
            week_end = week_start + timedelta(days=6)
            week_trx = [t for t in active if week_start.isoformat() <= t.get('Date','') <= week_end.isoformat()]
            key = f"{week_start.isoformat()} to {week_end.isoformat()}"
            weekly[key] = {
                'income': sum(float(t.get('Amount',0)) for t in week_trx if t.get('Type') == 'Income'),
                'expense': sum(float(t.get('Amount',0)) for t in week_trx if t.get('Type') == 'Expense'),
                'count': len(week_trx)
            }
        result['weekly_summary'] = weekly
        
        # Monthly summary (last 12 months)
        monthly = {}
        for i in range(12):
            month_date = today.replace(day=1) - timedelta(days=i*30)
            month_key = month_date.strftime('%Y-%m')
            month_trx = [t for t in active if t.get('Date','')[:7] == month_key]
            monthly[month_key] = {
                'income': sum(float(t.get('Amount',0)) for t in month_trx if t.get('Type') == 'Income'),
                'expense': sum(float(t.get('Amount',0)) for t in month_trx if t.get('Type') == 'Expense'),
                'deposits': sum(float(t.get('Amount',0)) for t in month_trx if t.get('Type') == 'Owner Deposit'),
                'withdrawals': sum(float(t.get('Amount',0)) for t in month_trx if t.get('Type') == 'Owner Withdrawal'),
                'count': len(month_trx)
            }
        result['monthly_summary'] = monthly
    
    if report_type == "category" or report_type == "all":
        # Category wise
        cats = {}
        for t in active:
            cat = t.get('Category','Uncategorized')
            if cat not in cats: cats[cat] = {'income': 0, 'expense': 0, 'count': 0}
            amt = float(t.get('Amount',0))
            if t.get('Type') == 'Income': cats[cat]['income'] += amt
            else: cats[cat]['expense'] += amt
            cats[cat]['count'] += 1
        result['category_wise'] = cats
    
    if report_type == "account" or report_type == "all":
        # Account wise
        accts = {}
        for acc in db.read_all('Accounts'):
            name = acc['AccountName']
            trx_list = [t for t in active if t.get('DebitAccount') == name or t.get('CreditAccount') == name]
            accts[name] = {
                'debits': sum(float(t.get('Amount',0)) for t in trx_list if t.get('DebitAccount') == name),
                'credits': sum(float(t.get('Amount',0)) for t in trx_list if t.get('CreditAccount') == name),
                'balance': AccountingEngine.calculate_balance(name),
                'count': len(trx_list)
            }
        result['account_wise'] = accts
    
    if report_type == "user" or report_type == "all":
        # User wise
        users = {}
        for t in active:
            u = t.get('CreatedBy','Unknown')
            if u not in users: users[u] = {'income': 0, 'expense': 0, 'count': 0}
            amt = float(t.get('Amount',0))
            if t.get('Type') == 'Income': users[u]['income'] += amt
            else: users[u]['expense'] += amt
            users[u]['count'] += 1
        result['user_wise'] = users
    
    if report_type == "cashflow" or report_type == "all":
        # Cash flow
        result['cash_flow'] = {
            'total_inflow': sum(float(t.get('Amount',0)) for t in active if t.get('Type') in ['Income','Owner Deposit']),
            'total_outflow': sum(float(t.get('Amount',0)) for t in active if t.get('Type') in ['Expense','Owner Withdrawal']),
            'net_cashflow': sum(float(t.get('Amount',0)) for t in active if t.get('Type') in ['Income','Owner Deposit']) - 
                           sum(float(t.get('Amount',0)) for t in active if t.get('Type') in ['Expense','Owner Withdrawal'])
        }
    
    if report_type == "top" or report_type == "all":
        # Top 20 transactions
        top_trx = sorted(active, key=lambda x: float(x.get('Amount',0)), reverse=True)[:20]
        result['top_transactions'] = [{
            'id': t.get('TransactionID'), 'date': t.get('Date'), 'type': t.get('Type'),
            'debit': t.get('DebitAccount'), 'credit': t.get('CreditAccount'),
            'amount': float(t.get('Amount',0)), 'category': t.get('Category'),
            'description': t.get('Description'), 'user': t.get('CreatedBy')
        } for t in top_trx]
    
    return result

# ============================================================================
# AUDIT LOG API
# ============================================================================

@app.get("/api/audit-log")
def api_audit_log(request: Request):
    require_admin(request)
    logs = db.read_all('Audit Log')
    return sorted(logs, key=lambda x: x.get('Timestamp',''), reverse=True)

def _log_audit(user: str, action: str, ref_id: str = '', details: str = ''):
    entry = {
        'LogID': str(uuid.uuid4()),
        'Timestamp': datetime.now().isoformat(),
        'User': user, 'Action': action,
        'TransactionID': ref_id, 'Details': details,
        'OldValue': '', 'NewValue': ''
    }
    db.create('Audit Log', entry)

# ============================================================================
# HTML FRONTEND
# ============================================================================

HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Finance Management System</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        :root {
            --primary: #2E5EFF;
            --primary-dark: #1E3FCC;
            --primary-light: #EAF0FF;
            --success: #16A34A;
            --success-light: #E9F9EF;
            --danger: #E5484D;
            --danger-light: #FDEDEE;
            --warning: #D97706;
            --warning-light: #FEF6E7;
            --info: #2E86AB;
            --info-light: #EAF4FB;
            --purple: #7C4DFF;
            --purple-light: #F1ECFF;

            --dark: #12172B;
            --light: #F8F9FC;
            --bg: #F1F3F9;
            --card-bg: #FFFFFF;
            --surface-alt: #F7F8FC;
            --text: #171B2E;
            --text-muted: #6B7280;
            --border: #E5E8F0;

            --radius: 16px;
            --radius-sm: 10px;
            --shadow-sm: 0 1px 2px rgba(16,24,40,0.05), 0 1px 3px rgba(16,24,40,0.06);
            --shadow-md: 0 8px 24px rgba(16,24,40,0.08);
            --shadow-lg: 0 16px 40px rgba(16,24,40,0.14);
            --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .dark-mode {
            --primary-light: rgba(46,94,255,0.14);
            --success-light: rgba(22,163,74,0.14);
            --danger-light: rgba(229,72,77,0.14);
            --warning-light: rgba(217,119,6,0.16);
            --info-light: rgba(46,134,171,0.16);
            --purple-light: rgba(124,77,255,0.16);

            --bg: #0B0E17;
            --card-bg: #141926;
            --surface-alt: #191F30;
            --text: #EDEFF7;
            --text-muted: #8B93A8;
            --border: #262E42;
            --shadow-sm: 0 1px 2px rgba(0,0,0,0.35);
            --shadow-md: 0 8px 24px rgba(0,0,0,0.4);
            --shadow-lg: 0 20px 48px rgba(0,0,0,0.5);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        *:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
        body {
            font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            transition: background 0.3s ease, color 0.3s ease;
            -webkit-font-smoothing: antialiased;
        }
        h1,h2,h3,h4,h5,h6 { color: var(--text); font-weight: 700; letter-spacing: -0.01em; }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }

        @keyframes fadeInUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes shimmer { 0% { background-position: -400px 0; } 100% { background-position: 400px 0; } }
        @keyframes slideIn { from { transform: translateX(110%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        @keyframes slideOut { from { transform: translateX(0); opacity: 1; } to { transform: translateX(110%); opacity: 0; } }

        .app-container { display: flex; min-height: 100vh; }

        /* ---------------- Sidebar ---------------- */
        .sidebar {
            width: 260px;
            background: linear-gradient(180deg, #131a2e 0%, #0d1220 100%);
            color: white;
            position: fixed;
            top: 0; left: 0; bottom: 0;
            z-index: 1000;
            transition: transform 0.3s ease;
            overflow-y: auto;
            box-shadow: var(--shadow-lg);
        }
        .sidebar-header {
            padding: 1.75rem 1.5rem 1.25rem;
            text-align: center;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        .sidebar-header h3 { margin: 0.35rem 0 0.15rem; font-size: 1.15rem; color: #fff; }
        .sidebar-header .logo { font-size: 2.4rem; line-height: 1; }
        .sidebar-header .tagline { font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; color: rgba(255,255,255,0.4); }
        .nav-menu { padding: 1rem 0.75rem; }
        .nav-item {
            display: flex; align-items: center; gap: 0.85rem;
            padding: 0.7rem 1rem; margin-bottom: 0.15rem; color: rgba(255,255,255,0.65);
            cursor: pointer; transition: all var(--transition); border: none; border-radius: var(--radius-sm);
            background: none; width: 100%; text-align: left; font-size: 0.92rem; font-weight: 500;
        }
        .nav-item:hover { color: #fff; background: rgba(255,255,255,0.08); }
        .nav-item.active {
            color: #fff; background: var(--primary);
            box-shadow: 0 4px 14px rgba(46,94,255,0.35);
        }
        .nav-item i { width: 18px; text-align: center; font-size: 0.95rem; }
        .sidebar-bottom {
            position: absolute; bottom: 0; width: 100%; padding: 1rem 1.5rem 1.5rem;
            background: linear-gradient(0deg, #0d1220 40%, transparent);
        }

        /* ---------------- Main Content ---------------- */
        .main-content {
            flex: 1; margin-left: 260px; padding: 1.5rem 1.75rem;
            transition: margin var(--transition);
        }

        /* ---------------- Header ---------------- */
        .top-header {
            background: var(--card-bg); padding: 1rem 1.5rem;
            border-radius: var(--radius); margin-bottom: 1.5rem;
            display: flex; justify-content: space-between; align-items: center;
            box-shadow: var(--shadow-sm); border: 1px solid var(--border);
            flex-wrap: wrap; gap: 0.75rem;
            position: sticky; top: 0.75rem; z-index: 900;
            backdrop-filter: blur(8px);
        }
        .user-info { display: flex; align-items: center; gap: 0.75rem; }
        .user-badge {
            background: var(--primary); color: white;
            padding: 0.2rem 0.7rem; border-radius: 20px; font-size: 0.72rem; font-weight: 700;
            letter-spacing: 0.03em;
        }
        .icon-btn {
            width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--border);
            background: var(--surface-alt); color: var(--text); display: inline-flex;
            align-items: center; justify-content: center; cursor: pointer; transition: all var(--transition);
            font-size: 1rem;
        }
        .icon-btn:hover { background: var(--primary-light); color: var(--primary); border-color: var(--primary); transform: translateY(-1px); }
        .hamburger-btn { display: none; }

        /* ---------------- KPI Cards ---------------- */
        .kpi-card {
            background: var(--card-bg); padding: 1.35rem 1.4rem;
            border-radius: var(--radius); box-shadow: var(--shadow-sm);
            border: 1px solid var(--border); border-left: 4px solid var(--primary);
            transition: transform var(--transition), box-shadow var(--transition);
            animation: fadeInUp 0.45s ease both;
            position: relative; overflow: hidden;
        }
        .kpi-card:hover { transform: translateY(-4px); box-shadow: var(--shadow-md); }
        .kpi-card .icon {
            font-size: 1.4rem; margin-bottom: 0.65rem; width: 42px; height: 42px;
            display: flex; align-items: center; justify-content: center;
            border-radius: var(--radius-sm); background: var(--primary-light);
        }
        .kpi-card .value { font-size: 1.5rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; letter-spacing: -0.02em; }
        .kpi-card .label { font-size: 0.8rem; color: var(--text-muted); font-weight: 500; margin-top: 0.15rem; }
        /* Distinct accent per KPI position — purely presentational, order matches existing template */
        #kpiCards > div:nth-child(1) .kpi-card { border-left-color: var(--success); }
        #kpiCards > div:nth-child(1) .kpi-card .icon { background: var(--success-light); }
        #kpiCards > div:nth-child(2) .kpi-card { border-left-color: var(--danger); }
        #kpiCards > div:nth-child(2) .kpi-card .icon { background: var(--danger-light); }
        #kpiCards > div:nth-child(3) .kpi-card { border-left-color: var(--primary); }
        #kpiCards > div:nth-child(4) .kpi-card { border-left-color: var(--purple); }
        #kpiCards > div:nth-child(4) .kpi-card .icon { background: var(--purple-light); }
        #kpiCards > div:nth-child(5) .kpi-card { border-left-color: var(--info); }
        #kpiCards > div:nth-child(5) .kpi-card .icon { background: var(--info-light); }
        #kpiCards > div:nth-child(6) .kpi-card { border-left-color: var(--warning); }
        #kpiCards > div:nth-child(6) .kpi-card .icon { background: var(--warning-light); }
        #kpiCards > div:nth-child(2) { animation-delay: 0.05s; }
        #kpiCards > div:nth-child(3) { animation-delay: 0.10s; }
        #kpiCards > div:nth-child(4) { animation-delay: 0.15s; }
        #kpiCards > div:nth-child(5) { animation-delay: 0.20s; }
        #kpiCards > div:nth-child(6) { animation-delay: 0.25s; }

        /* ---------------- Tables ---------------- */
        .table-container {
            background: var(--card-bg); border-radius: var(--radius);
            padding: 1.1rem; box-shadow: var(--shadow-sm); border: 1px solid var(--border);
            animation: fadeIn 0.35s ease both;
        }
        .table-container h6 { font-weight: 700; margin-bottom: 0.85rem; font-size: 0.92rem; }
        table { width: 100%; border-collapse: collapse; }
        th {
            position: sticky; top: 0;
            background: var(--surface-alt); color: var(--text-muted);
            padding: 0.7rem 0.85rem; text-align: left; font-size: 0.72rem;
            text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700;
            border-bottom: 1px solid var(--border);
        }
        td { padding: 0.75rem 0.85rem; border-bottom: 1px solid var(--border); font-size: 0.88rem; color: var(--text); }
        tr { transition: background var(--transition); }
        tr:hover td { background: var(--primary-light); }

        /* ---------------- Forms ---------------- */
        .form-card {
            background: var(--card-bg); padding: 1.6rem;
            border-radius: var(--radius); box-shadow: var(--shadow-sm); border: 1px solid var(--border);
            animation: fadeIn 0.35s ease both;
        }
        .form-label { font-weight: 600; font-size: 0.82rem; color: var(--text); margin-bottom: 0.35rem; }
        .form-control, .form-select {
            border-radius: var(--radius-sm); border: 1px solid var(--border);
            padding: 0.6rem 0.9rem; background: var(--surface-alt); color: var(--text);
            transition: all var(--transition); font-size: 0.9rem;
            caret-color: var(--text);
        }
        .form-control:focus, .form-select:focus {
            border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-light);
            background: var(--card-bg); color: var(--text); caret-color: var(--text);
        }
        /* Root cause of the "text invisible while typing" bug: neither rule
           above ever set `color` explicitly on :hover/:active/:focus-visible,
           so Bootstrap's own .form-control:focus color declaration (loaded
           earlier in <head>) could win that specific state. Making color
           and caret-color explicit on every interaction state closes that
           gap without changing any spacing, border, radius, or background
           already defined above. */
        .form-control:hover, .form-select:hover,
        .form-control:active, .form-select:active,
        .form-control:focus-visible, .form-select:focus-visible {
            color: var(--text); caret-color: var(--text);
        }
        .form-control::placeholder { color: var(--text-muted); opacity: 1; }
        .form-control:disabled, .form-select:disabled,
        .form-control[readonly] {
            color: var(--text-muted); background: var(--surface-alt); opacity: 0.75; cursor: not-allowed;
        }
        textarea.form-control { color: var(--text); caret-color: var(--text); }
        input[type="date"].form-control, input[type="search"].form-control { color: var(--text); caret-color: var(--text); }
        /* Chrome/Edge force a light-yellow background + black text on
           autofilled fields, ignoring our theme entirely (visible even in
           dark mode). Neutralize both without touching layout. */
        .form-control:-webkit-autofill,
        .form-control:-webkit-autofill:hover,
        .form-control:-webkit-autofill:focus {
            -webkit-text-fill-color: var(--text);
            transition: background-color 600000s 0s, color 600000s 0s;
            box-shadow: 0 0 0 1000px var(--surface-alt) inset;
            caret-color: var(--text);
        }
        .btn { border-radius: var(--radius-sm); padding: 0.6rem 1.4rem; font-weight: 600; transition: all var(--transition); font-size: 0.9rem; }
        .btn-primary { background: var(--primary); border: none; box-shadow: var(--shadow-sm); }
        .btn-primary:hover { background: var(--primary-dark); transform: translateY(-1px); box-shadow: var(--shadow-md); }
        .btn-outline-light:hover { transform: translateY(-1px); }
        .btn-outline-secondary { border-color: var(--border); color: var(--text); }
        .btn-outline-secondary:hover { background: var(--surface-alt); border-color: var(--primary); color: var(--primary); }

        .nav-tabs { border-bottom: 1px solid var(--border); gap: 0.25rem; }
        .nav-tabs .nav-link { color: var(--text-muted); font-weight: 600; border: none; border-radius: var(--radius-sm) var(--radius-sm) 0 0; cursor: pointer; }
        .nav-tabs .nav-link.active { color: var(--primary); background: var(--primary-light); border-bottom: 2px solid var(--primary); }

        /* ---------------- Toast ---------------- */
        .toast-container { position: fixed; top: 20px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 0.5rem; }
        .toast {
            background: var(--card-bg); color: var(--text); padding: 0.9rem 1.25rem; border-radius: var(--radius-sm);
            box-shadow: var(--shadow-lg); border-left: 4px solid var(--primary);
            animation: slideIn 0.3s ease; display: flex; align-items: center; gap: 0.65rem;
            font-size: 0.88rem; font-weight: 500; min-width: 240px;
        }
        .toast.toast-success { border-left-color: var(--success); }
        .toast.toast-error { border-left-color: var(--danger); }
        .toast.toast-warning { border-left-color: var(--warning); }
        .toast.toast-info { border-left-color: var(--info); }
        .toast.toast-hide { animation: slideOut 0.25s ease forwards; }

        /* ---------------- Page sections ---------------- */
        .page { display: none; }
        .page.active { display: block; animation: fadeIn 0.3s ease; }
        .app-shell { display: none; }
        .app-shell.active { display: block; animation: fadeIn 0.3s ease; }

        /* ---------------- Login ---------------- */
        .login-container {
            min-height: 100vh; display: flex; align-items: center; justify-content: center;
            background: radial-gradient(circle at 20% 20%, #3654e0 0%, transparent 45%),
                        radial-gradient(circle at 80% 80%, #7c4dff 0%, transparent 45%),
                        linear-gradient(135deg, #12172b 0%, #1c2340 100%);
            padding: 1rem;
        }
        .login-card {
            background: var(--card-bg); padding: 2.5rem; border-radius: 20px;
            width: 400px; max-width: 100%; box-shadow: var(--shadow-lg);
            animation: fadeInUp 0.5s ease both; border: 1px solid var(--border);
        }
        .login-card .logo { font-size: 3.2rem; line-height: 1; }
        .login-card h3 { font-size: 1.3rem; margin-top: 0.5rem; }
        .login-card .subtitle { color: var(--text-muted); font-size: 0.9rem; }
        .login-hint { font-size: 0.82rem; color: var(--text-muted); }

        /* ---------------- Responsive ---------------- */
        @media (max-width: 768px) {
            .sidebar { transform: translateX(-100%); }
            .sidebar.open { transform: translateX(0); }
            .main-content { margin-left: 0; padding: 1rem; }
            .hamburger-btn { display: inline-flex; }
            .top-header { position: static; }
            .sidebar-backdrop.show { display: block; }
        }
        .sidebar-backdrop {
            display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.45);
            z-index: 999; animation: fadeIn 0.2s ease;
        }

        /* ---------------- Status badges ---------------- */
        .badge-active { background: var(--success-light); color: var(--success); padding: 0.28rem 0.65rem; border-radius: 12px; font-size: 0.72rem; font-weight: 700; }
        .badge-inactive { background: var(--danger-light); color: var(--danger); padding: 0.28rem 0.65rem; border-radius: 12px; font-size: 0.72rem; font-weight: 700; }

        /* ---------------- Loading / skeleton ---------------- */
        .loading { text-align: center; padding: 2rem; }
        .spinner { width: 36px; height: 36px; border: 3px solid var(--border); border-top-color: var(--primary); border-radius: 50%; animation: spin 0.7s linear infinite; margin: 0 auto; }
        .skeleton-row {
            height: 18px; border-radius: 6px; margin: 0.6rem 0;
            background: linear-gradient(90deg, var(--surface-alt) 25%, var(--border) 37%, var(--surface-alt) 63%);
            background-size: 400px 100%; animation: shimmer 1.4s ease infinite;
        }
    <style>
    /* ===================== PREMIUM DASHBOARD (scoped) =====================
       Only affects #dashboardPage .premium-dash - every other page keeps
       its existing Bootstrap styling untouched. */
    .premium-dash{
        --pd-bg:#0B0E14; --pd-card:#12161F; --pd-card-2:#161B26; --pd-border:#20242F; --pd-border-soft:#1A1E29;
        --pd-text:#F1F3F8; --pd-dim:#8A8FA3; --pd-faint:#5B6072;
        --pd-primary:#2563EB; --pd-primary-soft:rgba(37,99,235,.15);
        --pd-success:#10B981; --pd-success-soft:rgba(16,185,129,.14);
        --pd-danger:#EF4444; --pd-danger-soft:rgba(239,68,68,.14);
        --pd-warning:#F59E0B; --pd-warning-soft:rgba(245,158,11,.14);
        --pd-purple:#8B5CF6; --pd-purple-soft:rgba(139,92,246,.14);
        background:var(--pd-bg); color:var(--pd-text); border-radius:20px; padding:24px;
        font-family:'Inter',system-ui,sans-serif; margin:-1rem -1rem 0;
    }
    .premium-dash *{ box-sizing:border-box; }
    .pd-head{ display:flex; align-items:center; justify-content:space-between; margin-bottom:20px; flex-wrap:wrap; gap:12px; }
    .pd-head h1{ font-size:20px; font-weight:700; letter-spacing:-.02em; color:#fff; margin:0; }
    .pd-head h1 span{ font-weight:400; color:var(--pd-dim); }
    .pd-kpi-grid{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px,1fr)); gap:14px; margin-bottom:16px; }
    .pd-kpi-card{ background:var(--pd-card); border:1px solid var(--pd-border); border-radius:16px; padding:16px 18px; transition:transform .2s ease, box-shadow .2s ease; }
    .pd-kpi-card:hover{ transform:translateY(-3px); box-shadow:0 12px 32px -12px rgba(0,0,0,.5); }
    .pd-kpi-top{ display:flex; align-items:flex-start; justify-content:space-between; margin-bottom:12px; }
    .pd-kpi-icon{ width:34px; height:34px; border-radius:10px; display:flex; align-items:center; justify-content:center; }
    .pd-kpi-icon svg{ width:17px; height:17px; }
    .pd-kpi-value{ font-size:21px; font-weight:800; letter-spacing:-.02em; margin-bottom:2px; font-variant-numeric:tabular-nums; color:#fff; }
    .pd-kpi-label{ font-size:12px; color:var(--pd-dim); font-weight:500; }
    .pd-grid-main{ display:grid; grid-template-columns:2.1fr 1fr; gap:14px; align-items:start; }
    @media (max-width:1100px){ .pd-grid-main{ grid-template-columns:1fr; } }
    .pd-panel{ background:var(--pd-card); border:1px solid var(--pd-border); border-radius:18px; overflow:hidden; margin-bottom:14px; }
    .pd-panel-head{ display:flex; align-items:center; justify-content:space-between; padding:16px 20px 4px; gap:10px; flex-wrap:wrap; }
    .pd-panel-title{ font-size:14px; font-weight:700; color:#fff; }
    .pd-panel-sub{ font-size:11.5px; color:var(--pd-dim); margin-top:2px; }
    .pd-table{ width:100%; border-collapse:collapse; font-size:12.5px; }
    .pd-table th{ text-align:left; padding:9px 20px; font-size:10.5px; font-weight:700; color:var(--pd-faint); text-transform:uppercase; letter-spacing:.04em; border-bottom:1px solid var(--pd-border); white-space:nowrap; }
    .pd-table td{ padding:11px 20px; border-bottom:1px solid var(--pd-border-soft); color:var(--pd-text); white-space:nowrap; }
    .pd-table tr:last-child td{ border-bottom:none; }
    .pd-table tr:hover td{ background:var(--pd-card-2); }
    .pd-badge{ display:inline-flex; align-items:center; gap:5px; font-size:11px; font-weight:700; padding:3px 9px; border-radius:20px; }
    .pd-badge::before{ content:''; width:6px; height:6px; border-radius:50%; }
    .pd-badge.active{ color:var(--pd-success); background:var(--pd-success-soft); } .pd-badge.active::before{ background:var(--pd-success); }
    .pd-badge.cancelled{ color:var(--pd-danger); background:var(--pd-danger-soft); } .pd-badge.cancelled::before{ background:var(--pd-danger); }
    .pd-badge.reversed{ color:var(--pd-warning); background:var(--pd-warning-soft); } .pd-badge.reversed::before{ background:var(--pd-warning); }
    .pd-amt-pos{ color:var(--pd-success); font-weight:700; font-variant-numeric:tabular-nums; }
    .pd-amt-neg{ color:var(--pd-danger); font-weight:700; font-variant-numeric:tabular-nums; }
    .pd-empty{ padding:24px 20px; text-align:center; color:var(--pd-faint); font-size:12.5px; }
    .pd-cat-row{ display:flex; align-items:center; gap:8px; font-size:12px; padding:5px 0; }
    .pd-cat-dot{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }
    .pd-reveal{ opacity:0; transform:translateY(8px); animation:pdRevealUp .45s cubic-bezier(.16,1,.3,1) forwards; }
    @keyframes pdRevealUp{ to{ opacity:1; transform:translateY(0); } }
    /* Premium dashboard follows the app's light/dark theme too - previously
       hardcoded dark regardless of the .dark-mode toggle. */
    body:not(.dark-mode) .premium-dash{
        --pd-bg:#F8F9FC; --pd-card:#FFFFFF; --pd-card-2:#F5F7FB; --pd-border:#E5E8F0; --pd-border-soft:#EEF0F5;
        --pd-text:#171B2E; --pd-dim:#6B7280; --pd-faint:#9AA1B0;
    }
    body:not(.dark-mode) .premium-dash .pd-head h1{ color:var(--pd-text); }
    body:not(.dark-mode) .premium-dash .pd-kpi-value,
    body:not(.dark-mode) .premium-dash .pd-panel-title{ color:var(--pd-text); }
    </style>
</head>
<body>
    <!-- Toast Container -->
    <div class="toast-container" id="toastContainer"></div>
    
    <!-- Login Page -->
    <div id="loginPage" class="page active">
        <div class="login-container">
            <div class="login-card">
                <div style="text-align:center;margin-bottom:2rem;">
                    <div class="logo">💰</div>
                    <h3>Finance Management System</h3>
                    <p class="subtitle">Sign in to continue</p>
                </div>
                <div class="mb-3">
                    <label class="form-label">Username</label>
                    <input type="text" class="form-control" id="loginUsername" placeholder="Enter username" autocomplete="username">
                </div>
                <div class="mb-3">
                    <label class="form-label">Password</label>
                    <div style="position:relative;">
                        <input type="password" class="form-control" id="loginPassword" placeholder="Enter password" autocomplete="current-password" style="padding-right:42px;">
                        <button type="button" onclick="toggleLoginPasswordVisibility()" aria-label="Show password"
                                style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; cursor:pointer; color:var(--text-muted); padding:4px;">
                            <i class="fas fa-eye" id="loginPwdEyeIcon"></i>
                        </button>
                    </div>
                </div>
                <button class="btn btn-primary w-100" onclick="login()">🔑 Sign In</button>
            </div>
        </div>
    </div>
    
    <!-- Main App -->
    <div id="appPage" class="app-shell">
        <div class="app-container">
            <!-- Sidebar backdrop (mobile only) -->
            <div class="sidebar-backdrop" id="sidebarBackdrop" onclick="toggleSidebar()"></div>

            <!-- Sidebar -->
            <div class="sidebar" id="sidebar">
                <div class="sidebar-header">
                    <div class="logo">💰</div>
                    <h3>Finance Manager</h3>
                    <div class="tagline">Enterprise Edition</div>
                </div>
                <div class="nav-menu">
                    <button class="nav-item active" onclick="showPage('dashboard')"><i class="fas fa-home"></i> Dashboard</button>
                    <button class="nav-item" onclick="showPage('transactions')"><i class="fas fa-exchange-alt"></i> Transactions</button>
                    <button class="nav-item" onclick="showPage('accounts')"><i class="fas fa-folder"></i> Accounts</button>
                    <button class="nav-item" onclick="showPage('categories')"><i class="fas fa-tags"></i> Categories</button>
                    <button class="nav-item admin-only" onclick="showPage('users')"><i class="fas fa-users"></i> Users</button>
                    <button class="nav-item" onclick="showPage('reports')"><i class="fas fa-chart-bar"></i> Reports</button>
                    <button class="nav-item" onclick="showPage('settings')"><i class="fas fa-cog"></i> Settings</button>
                </div>
                <div class="sidebar-bottom">
                    <button class="btn btn-outline-light w-100" onclick="logout()">🚪 Logout</button>
                </div>
            </div>
            
            <!-- Main Content -->
            <div class="main-content" id="mainContent">
                <!-- Header -->
                <div class="top-header">
                    <div style="display:flex;align-items:center;gap:0.75rem;">
                        <button class="icon-btn hamburger-btn" onclick="toggleSidebar()" aria-label="Toggle menu"><i class="fas fa-bars"></i></button>
                        <div>
                            <h5 style="margin:0;">Finance Management System</h5>
                            <small style="color:var(--text-muted);" id="currentTime"></small>
                        </div>
                    </div>
                    <div class="user-info">
                        <button class="icon-btn" id="themeToggleBtn" onclick="toggleTheme()" aria-label="Toggle theme"><span id="themeIcon">🖥️</span></button>
                        <span id="headerUser"></span>
                    </div>
                </div>
                
                <!-- Dashboard Page -->
                <div id="dashboardPage" class="page active">
                    <div class="premium-dash">
                        <div class="pd-head pd-reveal">
                            <h1 id="pdWelcome">Dashboard <span>— financial overview</span></h1>
                        </div>
                        <div class="pd-kpi-grid" id="pdKpiGrid"></div>
                        <div class="pd-grid-main">
                            <div>
                                <div class="pd-panel pd-reveal" style="animation-delay:.1s">
                                    <div class="pd-panel-head">
                                        <div><div class="pd-panel-title">Cash Flow</div><div class="pd-panel-sub">Income vs. Expense over time</div></div>
                                    </div>
                                    <div id="pdMainChart" style="height:260px; padding:4px 10px 10px;"></div>
                                </div>
                                <div class="pd-panel pd-reveal" style="animation-delay:.16s">
                                    <div class="pd-panel-head">
                                        <div><div class="pd-panel-title">Recent Transactions</div></div>
                                    </div>
                                    <div style="overflow-x:auto;">
                                        <table class="pd-table">
                                            <thead><tr><th>Type</th><th>Debit → Credit</th><th>Date</th><th>Status</th><th style="text-align:right">Amount</th></tr></thead>
                                            <tbody id="pdTxBody"></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                            <div>
                                <div class="pd-panel pd-reveal" style="animation-delay:.14s">
                                    <div class="pd-panel-head"><div class="pd-panel-title">Category Breakdown</div></div>
                                    <div id="pdPieChart" style="height:170px; margin-top:-6px;"></div>
                                    <div id="pdCategoryList" style="padding:0 20px 16px;"></div>
                                </div>
                                <div class="pd-panel pd-reveal" style="animation-delay:.2s">
                                    <div class="pd-panel-head"><div class="pd-panel-title">Account Balances</div></div>
                                    <div style="overflow-x:auto;">
                                        <table class="pd-table">
                                            <thead><tr><th>Account</th><th>Type</th><th style="text-align:right">Balance</th></tr></thead>
                                            <tbody id="pdAcctBody"></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Transactions Page -->
                <div id="transactionsPage" class="page">
                    <h4 class="mb-4">💳 Transactions</h4>
                    <ul class="nav nav-tabs mb-3">
                        <li class="nav-item"><a class="nav-link active" onclick="showTab('addTrx')">➕ Add New</a></li>
                        <li class="nav-item"><a class="nav-link" onclick="showTab('viewTrx')">📋 View All</a></li>
                    </ul>
                    <div id="addTrxTab" class="tab-content active">
                        <div class="form-card">
                            <div class="row g-3">
                                <div class="col-md-6">
                                    <label class="form-label">Date *</label>
                                    <input type="date" class="form-control" id="trxDate">
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Type *</label>
                                    <select class="form-select" id="trxType" onchange="updateTrxForm()">
                                        <option>Income</option><option>Expense</option><option>Transfer</option>
                                        <option>Adjustment</option><option>Owner Deposit</option><option>Owner Withdrawal</option>
                                    </select>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Debit Account *</label>
                                    <input class="form-control" id="trxDebit" list="trxDebitList" autocomplete="off" placeholder="Type to search or select..." oninput="refreshAccountExclusions()" onchange="refreshAccountExclusions()">
                                    <datalist id="trxDebitList"></datalist>
                                    <small id="trxDebitHint" class="text-muted"></small>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Credit Account *</label>
                                    <input class="form-control" id="trxCredit" list="trxCreditList" autocomplete="off" placeholder="Type to search or select..." oninput="refreshAccountExclusions()" onchange="refreshAccountExclusions()">
                                    <datalist id="trxCreditList"></datalist>
                                    <small id="trxCreditHint" class="text-muted"></small>
                                </div>
                                <div class="col-md-4">
                                    <label class="form-label">Amount *</label>
                                    <input type="number" class="form-control" id="trxAmount" step="0.01" min="0.01">
                                </div>
                                <div class="col-md-4">
                                    <label class="form-label">Category</label>
                                    <select class="form-select" id="trxCategory"></select>
                                </div>
                                <div class="col-md-4">
                                    <label class="form-label">Description</label>
                                    <input type="text" class="form-control" id="trxDesc">
                                </div>
                                <div class="col-12">
                                    <label class="form-label">Remarks</label>
                                    <textarea class="form-control" id="trxRemarks" rows="2"></textarea>
                                </div>
                                <div class="col-12">
                                    <button class="btn btn-primary" onclick="saveTransaction()">💾 Save Transaction</button>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div id="viewTrxTab" class="tab-content" style="display:none;">
                        <div class="table-container"><div id="allTransactions"></div></div>
                    </div>
                </div>
                
                <!-- Accounts Page -->
                <div id="accountsPage" class="page">
                    <h4 class="mb-4">📁 Accounts</h4>
                    <ul class="nav nav-tabs mb-3">
                        <li class="nav-item"><a class="nav-link active" onclick="showTab('viewAcc')">📋 View</a></li>
                        <li class="nav-item admin-only" style="display:none;"><a class="nav-link" onclick="showTab('addAcc')">➕ Add</a></li>
                        <li class="nav-item admin-only" style="display:none;"><a class="nav-link" onclick="showTab('manageAcc')">⚙ Manage</a></li>
                    </ul>
                    <div id="addAccTab" class="tab-content" style="display:none;">
                        <div class="form-card">
                            <div class="row g-3">
                                <div class="col-md-6">
                                    <label class="form-label">Account Name *</label>
                                    <input type="text" class="form-control" id="newAccName">
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Account Type *</label>
                                    <select class="form-select" id="newAccType">
                                        <option>Cash</option><option>Bank</option><option>Credit Card</option>
                                        <option>Asset</option><option>Liability</option><option>Equity</option>
                                        <option>Income</option><option>Expense</option>
                                    </select>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Opening Balance</label>
                                    <input type="number" class="form-control" id="newAccBalance" value="0" step="0.01">
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Currency</label>
                                    <select class="form-select" id="newAccCurrency"><option>AED</option><option>INR</option><option>USD</option></select>
                                </div>
                                <div class="col-12">
                                    <label class="form-label">Description</label>
                                    <input type="text" class="form-control" id="newAccDesc">
                                </div>
                                <div class="col-12">
                                    <button class="btn btn-primary" onclick="saveAccount()">💾 Save Account</button>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div id="manageAccTab" class="tab-content" style="display:none;">
                        <div class="form-card">
                            <label class="form-label">Select Account</label>
                            <select class="form-select mb-3" id="manageAccSelect" onchange="onManageAccSelect()"></select>
                            <div id="manageAccActions" style="display:none;">
                                <button class="btn btn-primary" id="manageAccToggleBtn" onclick="toggleAccountStatus()">🔄 Toggle Status</button>
                                <button class="btn btn-outline-primary ms-2" onclick="editAccount()">✏️ Edit Details</button>
                                <button class="btn btn-outline-secondary ms-2" onclick="correctAccountBalance()">🛠 Correct Opening Balance</button>
                                <button class="btn btn-danger ms-2" onclick="deleteAccount()">🗑 Delete</button>
                            </div>
                        </div>
                    </div>
                    <div id="viewAccTab" class="tab-content">
                        <div class="table-container"><div id="accountsTable"></div></div>
                    </div>
                </div>
                
                <!-- Categories Page -->
                <div id="categoriesPage" class="page">
                    <h4 class="mb-4">🏷 Categories</h4>
                    <ul class="nav nav-tabs mb-3">
                        <li class="nav-item"><a class="nav-link active" onclick="showTab('viewCat')">📋 View</a></li>
                        <li class="nav-item admin-only" style="display:none;"><a class="nav-link" onclick="showTab('addCat')">➕ Add</a></li>
                        <li class="nav-item admin-only" style="display:none;"><a class="nav-link" onclick="showTab('manageCat')">⚙ Manage</a></li>
                    </ul>
                    <div id="addCatTab" class="tab-content" style="display:none;">
                        <div class="form-card">
                            <div class="row g-3">
                                <div class="col-md-6">
                                    <label class="form-label">Category Name *</label>
                                    <input type="text" class="form-control" id="newCatName">
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Type</label>
                                    <select class="form-select" id="newCatType"><option>Income</option><option>Expense</option></select>
                                </div>
                                <div class="col-12">
                                    <label class="form-label">Description</label>
                                    <input type="text" class="form-control" id="newCatDesc">
                                </div>
                                <div class="col-12">
                                    <button class="btn btn-primary" onclick="saveCategory()">💾 Save Category</button>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div id="manageCatTab" class="tab-content" style="display:none;">
                        <div class="form-card">
                            <label class="form-label">Select Category</label>
                            <select class="form-select mb-3" id="manageCatSelect" onchange="onManageCatSelect()"></select>
                            <div id="manageCatActions" style="display:none;">
                                <button class="btn btn-primary" id="manageCatToggleBtn" onclick="toggleCategoryStatus()">🔄 Toggle Status</button>
                                <button class="btn btn-danger ms-2" onclick="deleteCategory()">🗑 Delete</button>
                            </div>
                        </div>
                    </div>
                    <div id="viewCatTab" class="tab-content">
                        <div class="table-container"><div id="categoriesTable"></div></div>
                    </div>
                </div>
                
                <!-- Users Page -->
                <div id="usersPage" class="page">
                    <h4 class="mb-4">👥 Users</h4>
                    <ul class="nav nav-tabs mb-3">
                        <li class="nav-item"><a class="nav-link active" onclick="showTab('viewUsr')">📋 View</a></li>
                        <li class="nav-item"><a class="nav-link" onclick="showTab('addUsr')">➕ Add</a></li>
                    </ul>
                    <div id="addUsrTab" class="tab-content" style="display:none;">
                        <div class="form-card">
                            <div class="row g-3">
                                <div class="col-md-6">
                                    <label class="form-label">Username *</label>
                                    <input type="text" class="form-control" id="newUserName">
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Full Name *</label>
                                    <input type="text" class="form-control" id="newUserFullName">
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Password *</label>
                                    <input type="password" class="form-control" id="newUserPassword">
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label">Role</label>
                                    <select class="form-select" id="newUserRole"><option value="user">user</option><option value="admin">admin</option></select>
                                </div>
                                <div class="col-12">
                                    <button class="btn btn-primary" onclick="saveUser()">💾 Save User</button>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div id="viewUsrTab" class="tab-content">
                        <div class="table-container"><div id="usersTable"></div></div>
                    </div>
                </div>
                
                <!-- Reports Page -->
                <div id="reportsPage" class="page">
                    <h4 class="mb-4">📊 Reports</h4>
                    <ul class="nav nav-tabs mb-3">
                        <li class="nav-item"><a class="nav-link active" onclick="loadReport('summary')">📋 Summary</a></li>
                        <li class="nav-item"><a class="nav-link" onclick="loadReport('category')">🏷 Categories</a></li>
                        <li class="nav-item"><a class="nav-link" onclick="loadReport('account')">📁 Accounts</a></li>
                        <li class="nav-item"><a class="nav-link" onclick="loadReport('user')">👥 Users</a></li>
                        <li class="nav-item"><a class="nav-link" onclick="loadReport('cashflow')">💵 Cash Flow</a></li>
                        <li class="nav-item"><a class="nav-link" onclick="loadReport('top')">🔝 Top 20</a></li>
                    </ul>
                    <div id="reportCharts"></div>
                </div>
                
                <!-- Settings Page -->
                <div id="settingsPage" class="page">
                    <h4 class="mb-4">⚙ Settings</h4>
                    <div class="form-card">
                        <p><strong>Version:</strong> 6.1.0</p>
                        <p><strong>Currency:</strong> Dhs (AED)</p>
                        <p><strong>Data Store:</strong> Local JSON (finance_data.json)</p>
                        <hr>
                        <h6 class="mb-3">System Statistics</h6>
                        <div id="sysStats"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // ============================================================================
        // GLOBAL STATE

        // ============================================================================
        const API = 'http://localhost:8000/api';
        let token = localStorage.getItem('token');
        let currentUser = null;
        let currentPage = 'dashboard';
        
        // ============================================================================
        // HELPERS
        // ============================================================================
        
        function showToast(message, type = 'success') {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
            toast.innerHTML = `${icons[type] || 'ℹ️'} ${message}`;
            container.appendChild(toast);
            setTimeout(() => { toast.classList.add('toast-hide'); setTimeout(() => toast.remove(), 250); }, 3000);
        }
        
        async async function apiCall(method, endpoint, data = null) {
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;
            
            const options = { method, headers };
            if (data) options.body = JSON.stringify(data);
            
            const response = await fetch(`${API}${endpoint}`, options);
            if (response.status === 401) { logout(); throw new Error('Unauthorized'); }
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Request failed');
            }
            return response.json();
        }
        
        function formatCurrency(amount) {
            return `Dhs ${parseFloat(amount).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        }
        
        function formatDate(dateStr) {
            if (!dateStr) return '';
            const d = new Date(dateStr);
            return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
        }
        
        // ============================================================================
        // THEME: light / dark / auto (follows OS setting), persisted across sessions
        // ============================================================================
        function _pdSystemPrefersDark() {
            return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        }
        function _pdApplyTheme(mode) {
            const resolvedDark = mode === 'dark' || (mode === 'auto' && _pdSystemPrefersDark());
            document.body.classList.toggle('dark-mode', resolvedDark);
            const icon = document.getElementById('themeIcon');
            if (icon) icon.textContent = mode === 'auto' ? '🖥️' : (resolvedDark ? '🌙' : '☀️');
            const btn = document.getElementById('themeToggleBtn');
            if (btn) btn.title = 'Theme: ' + mode.charAt(0).toUpperCase() + mode.slice(1) + ' (click to change)';
            // Re-render charts if the dashboard is currently visible, so colors match immediately
            if (document.getElementById('dashboardPage') && document.getElementById('dashboardPage').classList.contains('active') && token) {
                loadDashboard();
            }
        }
        function getThemeMode() { return localStorage.getItem('themeMode') || 'auto'; }
        function setThemeMode(mode) {
            localStorage.setItem('themeMode', mode);
            _pdApplyTheme(mode);
        }
        function toggleTheme() {
            const order = ['light', 'dark', 'auto'];
            const next = order[(order.indexOf(getThemeMode()) + 1) % order.length];
            setThemeMode(next);
        }
        // Apply saved/system theme immediately on load, before login even
        _pdApplyTheme(getThemeMode());
        if (window.matchMedia) {
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
                if (getThemeMode() === 'auto') _pdApplyTheme('auto');
            });
        }


        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('sidebarBackdrop').classList.toggle('show');
        }

        function showSkeleton(containerId, rows = 4) {
            const el = document.getElementById(containerId);
            if (!el) return;
            el.innerHTML = Array(rows).fill('<div class="skeleton-row"></div>').join('');
        }
        
        // ============================================================================
        // AUTH
        // ============================================================================
        
        function toggleLoginPasswordVisibility() {
            const input = document.getElementById('loginPassword');
            const icon = document.getElementById('loginPwdEyeIcon');
            const showing = input.type === 'text';
            input.type = showing ? 'password' : 'text';
            icon.className = showing ? 'fas fa-eye' : 'fas fa-eye-slash';
        }

        async async function login() {
            const username = document.getElementById('loginUsername').value;
            const password = document.getElementById('loginPassword').value;
            
            if (!username || !password) {
                showToast('Please enter username and password', 'error');
                return;
            }
            
            try {
                const response = await apiCall('POST', '/login', { username, password });
                token = response.token;
                currentUser = response.user;
                localStorage.setItem('token', token);
                localStorage.setItem('user', JSON.stringify(currentUser));
                
                document.getElementById('loginPage').classList.remove('active');
                document.getElementById('appPage').classList.add('active');
                document.getElementById('headerUser').innerHTML = `${currentUser.fullName} <span class="user-badge">${currentUser.role.toUpperCase()}</span>`;
                
                if (currentUser.role === 'admin') {
                    document.querySelectorAll('.admin-only').forEach(el => el.style.display = 'flex');
                }
                
                showToast(`Welcome, ${currentUser.fullName}!`);
                loadDashboard();
                updateTrxForm();
                updateTime();
                setInterval(updateTime, 60000);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        function logout() {
            token = null;
            currentUser = null;
            localStorage.clear();
            document.getElementById('loginPage').classList.add('active');
            document.getElementById('appPage').classList.remove('active');
            document.getElementById('loginUsername').value = '';
            document.getElementById('loginPassword').value = '';
        }
        
        function updateTime() {
            document.getElementById('currentTime').textContent = new Date().toLocaleString('en-US', {
                weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit'
            });
        }
        
        // ============================================================================
        // NAVIGATION
        // ============================================================================
        
        function showPage(page) {
            document.getElementById('sidebar').classList.remove('open');
            document.getElementById('sidebarBackdrop').classList.remove('show');
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            document.getElementById(`${page}Page`).classList.add('active');
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            event.target.closest('.nav-item').classList.add('active');
            currentPage = page;
            
            if (page === 'dashboard') loadDashboard();
            else if (page === 'transactions') { updateTrxForm(); loadTransactionsList(); }
            else if (page === 'accounts') loadAccounts();
            else if (page === 'categories') loadCategories();
            else if (page === 'users') loadUsers();
            else if (page === 'reports') loadReport('summary');
            else if (page === 'settings') loadSettings();
        }
        
        function showTab(tab) {
            const target = document.getElementById(`${tab}Tab`);
            if (!target) return;
            const scope = target.closest('.page') || document;
            scope.querySelectorAll('.tab-content').forEach(t => t.style.display = 'none');
            target.style.display = 'block';
            const tabId = target.id;
            scope.querySelectorAll('.nav-tabs .nav-link').forEach(l => l.classList.remove('active'));
            if (typeof event !== 'undefined' && event.target && event.target.classList.contains('nav-link')) {
                event.target.classList.add('active');
            }
        }
        
        // ============================================================================
        // DASHBOARD
        // ============================================================================
        
        function _pdFmt(n){ return 'Dhs ' + Number(n||0).toLocaleString('en-US', {minimumFractionDigits:0, maximumFractionDigits:0}); }

        function _pdCountUp(el, target){
            const dur = 800, start = performance.now(), from = 0;
            function tick(now){
                const p = Math.min(1, (now-start)/dur);
                const eased = 1 - Math.pow(1-p, 3);
                el.textContent = _pdFmt(from + (target-from)*eased);
                if (p < 1) requestAnimationFrame(tick);
            }
            requestAnimationFrame(tick);
        }

        const PD_ICONS = {
            wallet:'<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/>',
            up:'<path d="M23 6l-9.5 9.5-5-5L1 18"/><path d="M17 6h6v6"/>',
            down:'<path d="M23 18l-9.5-9.5-5 5L1 6"/><path d="M17 18h6v-6"/>',
            swap:'<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
            receipt:'<path d="M4 2h16v20l-3-2-3 2-3-2-3 2-3-2-1 2z"/><path d="M8 7h8M8 11h8M8 15h5"/>',
            deposit:'<path d="M12 2v13"/><path d="m6 10 6 6 6-6"/><path d="M4 20h16"/>',
        };

        function _pdChartColors(){
            const dark = document.body.classList.contains('dark-mode');
            return dark
                ? { font:'#8A8FA3', grid:'#1A1E29', hoverBg:'#161B26', hoverBorder:'#20242F', hoverText:'#F1F3F8', pieLine:'#12161F' }
                : { font:'#6B7280', grid:'#EEF0F5', hoverBg:'#FFFFFF', hoverBorder:'#E5E8F0', hoverText:'#171B2E', pieLine:'#FFFFFF' };
        }

        async function loadDashboard() {
            try {
                const [data, trx, accounts, categories] = await Promise.all([
                    apiCall('GET', '/dashboard'),
                    apiCall('GET', '/transactions'),
                    apiCall('GET', '/accounts'),
                    apiCall('GET', '/categories')
                ]);

                document.getElementById('pdWelcome').innerHTML =
                    `Welcome back, ${currentUser ? currentUser.fullName : ''} <span>— here's your financial overview</span>`;

                // ---- KPI cards (all values are backend-computed, see AccountingEngine.get_kpi) ----
                const cards = [
                    { label:'Total Balance', value:data.total_balance, icon:'wallet', color:'primary' },
                    { label:'Total Income', value:data.total_income, icon:'up', color:'success' },
                    { label:'Total Expenses', value:data.total_expense, icon:'down', color:'danger' },
                    { label:'Total Transfers', value:data.total_transfer, icon:'swap', color:'purple', sub:(data.transfer_count||0)+' transfer'+(data.transfer_count===1?'':'s') },
                    { label:'Local Payments', value:data.total_local_payments, icon:'receipt', color:'warning', sub:(data.local_payment_count||0)+' payment'+(data.local_payment_count===1?'':'s') },
                    { label:'Owner Deposits', value:data.total_deposits, icon:'deposit', color:'success' },
                ];
                document.getElementById('pdKpiGrid').innerHTML = cards.map((c,i) => `
                    <div class="pd-kpi-card pd-reveal" style="animation-delay:${.03*i}s">
                        <div class="pd-kpi-top">
                            <div class="pd-kpi-icon" style="background:var(--pd-${c.color}-soft); color:var(--pd-${c.color})">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PD_ICONS[c.icon]}</svg>
                            </div>
                        </div>
                        <div class="pd-kpi-value" id="pd-kpi-${i}">Dhs 0</div>
                        <div class="pd-kpi-label">${c.label}${c.sub ? ' · ' + c.sub : ''}</div>
                    </div>
                `).join('');
                cards.forEach((c,i) => _pdCountUp(document.getElementById('pd-kpi-'+i), c.value));

                // ---- Recent transactions table ----
                const recent = trx.slice(0, 8);
                const statusClass = s => s === 'Cancelled' ? 'cancelled' : (s === 'Reversed' ? 'reversed' : 'active');
                document.getElementById('pdTxBody').innerHTML = recent.length ? recent.map(t => `
                    <tr>
                        <td style="font-weight:600">${t.Type}</td>
                        <td style="color:var(--pd-dim)">${t.DebitAccount} → ${t.CreditAccount}</td>
                        <td style="color:var(--pd-dim)">${t.Date}</td>
                        <td><span class="pd-badge ${statusClass(t.Status)}">${t.Status}</span></td>
                        <td style="text-align:right" class="${t.Type==='Expense' ? 'pd-amt-neg':'pd-amt-pos'}">${t.Type==='Expense'?'-':'+'}${_pdFmt(t.Amount)}</td>
                    </tr>
                `).join('') : `<tr><td colspan="5" class="pd-empty">No transactions yet — add your first one from the Transactions page.</td></tr>`;

                // ---- Account balances ----
                const balances = data.balances || {};
                const balRows = Object.entries(balances);
                document.getElementById('pdAcctBody').innerHTML = balRows.length ? balRows.map(([name, info]) => `
                    <tr>
                        <td style="font-weight:600">${name}</td>
                        <td style="color:var(--pd-dim)">${info.type || ''}</td>
                        <td style="text-align:right; font-variant-numeric:tabular-nums;">${_pdFmt(info.balance)}</td>
                    </tr>
                `).join('') : `<tr><td colspan="3" class="pd-empty">No accounts yet.</td></tr>`;

                // ---- Category breakdown (active Expense transactions grouped by Category) ----
                const catTotals = {};
                trx.filter(t => t.Status === 'Active' && t.Category).forEach(t => {
                    catTotals[t.Category] = (catTotals[t.Category] || 0) + parseFloat(t.Amount || 0);
                });
                const catColors = ['#EF4444','#F59E0B','#8B5CF6','#2563EB','#10B981','#EC4899','#14B8A6'];
                const catEntries = Object.entries(catTotals).sort((a,b) => b[1]-a[1]);

                if (catEntries.length === 0) {
                    document.getElementById('pdPieChart').innerHTML = '';
                    document.getElementById('pdCategoryList').innerHTML = '<div class="pd-empty">No categorized transactions yet.</div>';
                } else {
                    const cc = _pdChartColors();
                    Plotly.newPlot('pdPieChart', [{
                        labels: catEntries.map(e => e[0]), values: catEntries.map(e => e[1]),
                        type:'pie', hole:.68, sort:false,
                        marker:{ colors: catEntries.map((_,i) => catColors[i % catColors.length]), line:{color:cc.pieLine, width:2} },
                        textinfo:'none', hoverinfo:'label+percent',
                    }], {
                        paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                        font:{ family:'Inter, sans-serif', color:cc.font, size:11 },
                        margin:{t:10,b:10,l:10,r:10}, showlegend:false,
                    }, {displayModeBar:false, responsive:true});

                    const catTotal = catEntries.reduce((s,e) => s+e[1], 0);
                    document.getElementById('pdCategoryList').innerHTML = catEntries.map((e,i) => `
                        <div class="pd-cat-row">
                            <span class="pd-cat-dot" style="background:${catColors[i % catColors.length]}"></span>
                            <span style="flex:1; color:var(--pd-dim);">${e[0]}</span>
                            <span style="font-weight:700; color:#fff;">${Math.round(e[1]/catTotal*100)}%</span>
                        </div>
                    `).join('');
                }

                // ---- Cash flow chart: real transactions grouped by date ----
                const byDate = {};
                trx.filter(t => t.Status === 'Active').forEach(t => {
                    const d = t.Date;
                    if (!byDate[d]) byDate[d] = { income:0, expense:0 };
                    if (t.Type === 'Income') byDate[d].income += parseFloat(t.Amount||0);
                    if (t.Type === 'Expense') byDate[d].expense += parseFloat(t.Amount||0);
                });
                const dates = Object.keys(byDate).sort();
                if (dates.length === 0) {
                    document.getElementById('pdMainChart').innerHTML = '<div class="pd-empty">No transactions yet — the cash flow chart will populate as you add them.</div>';
                } else {
                    const cc2 = _pdChartColors();
                    Plotly.newPlot('pdMainChart', [
                        { x:dates, y:dates.map(d=>byDate[d].income), name:'Income', type:'scatter', mode:'lines',
                          line:{color:'#10B981', width:2.5, shape:'spline'}, fill:'tozeroy', fillcolor:'rgba(16,185,129,.10)' },
                        { x:dates, y:dates.map(d=>byDate[d].expense), name:'Expense', type:'scatter', mode:'lines',
                          line:{color:'#EF4444', width:2.5, shape:'spline'}, fill:'tozeroy', fillcolor:'rgba(239,68,68,.08)' },
                    ], {
                        paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
                        font:{ family:'Inter, sans-serif', color:cc2.font, size:10.5 },
                        margin:{t:10,l:44,r:20,b:30}, showlegend:false,
                        xaxis:{ showgrid:false }, yaxis:{ gridcolor:cc2.grid, zeroline:false },
                        hovermode:'x unified', hoverlabel:{ bgcolor:cc2.hoverBg, bordercolor:cc2.hoverBorder, font:{color:cc2.hoverText, family:'Inter'} },
                    }, {displayModeBar:false, responsive:true});
                }

            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        // ============================================================================
        // TRANSACTIONS
        // ============================================================================
        
        let _trxAccountsCache = [];
        let _trxCategoriesCache = [];
        let _trxRulesCache = null; // fetched once from GET /api/transaction-rules, the single source of truth

        function _accountOptionsHtml(accounts, excludeName) {
            return accounts
                .filter(a => a.AccountName !== excludeName)
                .map(a => `<option value="${a.AccountName}">${a.AccountName} — ${a.AccountType}</option>`)
                .join('');
        }

        // Purely dynamic: there is no hardcoded Debit/Credit AccountType
        // mapping in the frontend anymore. It filters activeAccounts using
        // whatever TRANSACTION_TYPE_RULES the backend returns for this
        // Type. A null side (e.g. Adjustment) means "any AccountType".
        function _accountsForType(type, activeAccounts) {
            const rule = (_trxRulesCache && _trxRulesCache[type]) || { debit: null, credit: null };
            const filterBy = (allowedTypes) => allowedTypes === null
                ? activeAccounts
                : activeAccounts.filter(a => allowedTypes.includes(a.AccountType));
            return { debit: filterBy(rule.debit), credit: filterBy(rule.credit) };
        }

        // Re-renders the datalists using the already-fetched account list
        // (no network call) so that picking one side excludes it from the
        // other side, and re-validates the same-account rule. Cheap enough
        // to run on every keystroke.
        function refreshAccountExclusions() {
            const typeEl = document.getElementById('trxType');
            const debitEl = document.getElementById('trxDebit');
            const creditEl = document.getElementById('trxCredit');
            const debitHint = document.getElementById('trxDebitHint');
            const creditHint = document.getElementById('trxCreditHint');
            if (!typeEl || !debitEl || !creditEl || !_trxAccountsCache.length || !_trxRulesCache) return;

            const type = typeEl.value;
            const activeAccounts = _trxAccountsCache.filter(a => a.Status === 'Active');
            const { debit, credit } = _accountsForType(type, activeAccounts);

            const isAdjustment = type === 'Adjustment';
            document.getElementById('trxDebitList').innerHTML = _accountOptionsHtml(debit, isAdjustment ? null : creditEl.value);
            document.getElementById('trxCreditList').innerHTML = _accountOptionsHtml(credit, isAdjustment ? null : debitEl.value);

            // Client-side mirror of the same check the backend enforces, for
            // instant feedback - the backend is still the final authority
            // (see _validate_transaction_accounts in code.py).
            const debitAcc = activeAccounts.find(a => a.AccountName === debitEl.value);
            const creditAcc = activeAccounts.find(a => a.AccountName === creditEl.value);
            const rule = _trxRulesCache[type] || { debit: null, credit: null };
            const debitTypeInvalid = debitAcc && rule.debit !== null && !rule.debit.includes(debitAcc.AccountType);
            const creditTypeInvalid = creditAcc && rule.credit !== null && !rule.credit.includes(creditAcc.AccountType);
            const sameAccount = debitEl.value && creditEl.value && debitEl.value === creditEl.value && !isAdjustment;
            const blocked = sameAccount || debitTypeInvalid || creditTypeInvalid;

            debitEl.classList.toggle('is-invalid', !!(sameAccount || debitTypeInvalid));
            creditEl.classList.toggle('is-invalid', !!(sameAccount || creditTypeInvalid));
            let msg = '';
            if (sameAccount) msg = '⚠ Debit and Credit must be different accounts (unless Type = Adjustment)';
            else if (debitTypeInvalid) msg = `⚠ For ${type}, Debit must be: ${rule.debit.join(', ')}`;
            else if (creditTypeInvalid) msg = `⚠ For ${type}, Credit must be: ${rule.credit.join(', ')}`;
            if (debitHint) debitHint.textContent = msg;
            if (creditHint) creditHint.textContent = msg;
            return !blocked;
        }

        async function updateTrxForm() {
            try {
                const typeEl = document.getElementById('trxType');
                const debitEl = document.getElementById('trxDebit');
                const creditEl = document.getElementById('trxCredit');
                const categoryEl = document.getElementById('trxCategory');
                if (!typeEl || !debitEl || !creditEl) return; // form not on screen yet

                // Fetch accounts/categories/rules together; rules rarely
                // change so this could be cached longer, but keeping it in
                // the same batch as accounts/categories avoids a second
                // round-trip while still guaranteeing freshness.
                const [accounts, categories, rules] = await Promise.all([
                    apiCall('GET', '/accounts'),
                    apiCall('GET', '/categories'),
                    _trxRulesCache ? Promise.resolve(_trxRulesCache) : apiCall('GET', '/transaction-rules')
                ]);
                _trxAccountsCache = accounts;
                _trxCategoriesCache = categories;
                _trxRulesCache = rules;

                // Type changed -> account choices are no longer guaranteed valid, reset them
                debitEl.value = '';
                creditEl.value = '';

                refreshAccountExclusions();

                if (categoryEl) {
                    categoryEl.innerHTML = '<option value="">Select</option>' +
                        categories.filter(c => c.Status === 'Active').map(c => `<option>${c.CategoryName}</option>`).join('');
                }
            } catch (error) {
                showToast('Failed to load account/category options: ' + error.message, 'error');
            }
        }
        
        async function saveTransaction() {
            const data = {
                Date: document.getElementById('trxDate').value || new Date().toISOString().split('T')[0],
                Type: document.getElementById('trxType').value,
                DebitAccount: document.getElementById('trxDebit').value.trim(),
                CreditAccount: document.getElementById('trxCredit').value.trim(),
                Category: document.getElementById('trxCategory').value,
                Amount: parseFloat(document.getElementById('trxAmount').value),
                Description: document.getElementById('trxDesc').value,
                Remarks: document.getElementById('trxRemarks').value
            };

            if (!data.DebitAccount || !data.CreditAccount || !data.Amount) {
                showToast('Please fill all required fields', 'error');
                return;
            }
            const knownNames = _trxAccountsCache.filter(a => a.Status === 'Active').map(a => a.AccountName);
            if (!knownNames.includes(data.DebitAccount) || !knownNames.includes(data.CreditAccount)) {
                showToast('Please pick an existing account from the suggestions for Debit/Credit', 'error');
                return;
            }
            if (data.DebitAccount === data.CreditAccount && data.Type !== 'Adjustment') {
                showToast('Debit and Credit accounts must be different (unless Type = Adjustment)', 'error');
                return;
            }
            if (_trxRulesCache) {
                const rule = _trxRulesCache[data.Type] || { debit: null, credit: null };
                const debitAcc = _trxAccountsCache.find(a => a.AccountName === data.DebitAccount);
                const creditAcc = _trxAccountsCache.find(a => a.AccountName === data.CreditAccount);
                if (debitAcc && rule.debit !== null && !rule.debit.includes(debitAcc.AccountType)) {
                    showToast(`Invalid Debit account for ${data.Type}: must be ${rule.debit.join(', ')}`, 'error');
                    return;
                }
                if (creditAcc && rule.credit !== null && !rule.credit.includes(creditAcc.AccountType)) {
                    showToast(`Invalid Credit account for ${data.Type}: must be ${rule.credit.join(', ')}`, 'error');
                    return;
                }
            }

            try {
                const result = await apiCall('POST', '/transactions', data);
                showToast(`Transaction ${result.data.TransactionID} saved!`);
                document.getElementById('trxAmount').value = '';
                document.getElementById('trxDesc').value = '';
                document.getElementById('trxRemarks').value = '';
                loadDashboard();
                if (document.getElementById('allTransactions')) loadTransactionsList();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        async function loadTransactionsList() {
            try {
                showSkeleton('allTransactions', 6);
                const trx = await apiCall('GET', '/transactions');
                const isAdmin = currentUser && currentUser.role === 'admin';
                let html = '<table><tr><th>ID</th><th>Date</th><th>Type</th><th>Debit</th><th>Credit</th><th>Amount</th><th>Category</th><th>By</th><th>Status</th>' + (isAdmin ? '<th>Actions</th>' : '') + '</tr>';
                trx.forEach(t => {
                    const canAct = isAdmin && t.Status === 'Active';
                    html += `<tr>
                        <td>${t.TransactionID}</td><td>${t.Date}</td><td>${t.Type}</td>
                        <td>${t.DebitAccount}</td><td>${t.CreditAccount}</td>
                        <td>${formatCurrency(t.Amount)}</td><td>${t.Category || ''}</td>
                        <td>@${t.CreatedBy || ''}</td>
                        <td><span class="${t.Status === 'Active' ? 'badge-active' : 'badge-inactive'}">${t.Status}</span></td>
                        ${isAdmin ? `<td class="text-nowrap">
                            <button class="btn btn-sm btn-outline-secondary" title="Edit" ${canAct ? '' : 'disabled'} onclick="editTransaction('${t.TransactionID}')"><i class="fas fa-pen"></i></button>
                            <button class="btn btn-sm btn-outline-warning" title="Reverse" ${canAct ? '' : 'disabled'} onclick="reverseTransaction('${t.TransactionID}')"><i class="fas fa-rotate-left"></i></button>
                            <button class="btn btn-sm btn-outline-danger" title="Delete" ${canAct ? '' : 'disabled'} onclick="deleteTransaction('${t.TransactionID}')"><i class="fas fa-trash"></i></button>
                        </td>` : ''}
                    </tr>`;
                });
                html += '</table>';
                document.getElementById('allTransactions').innerHTML = html || '<p class="text-muted">No transactions</p>';
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function editTransaction(id) {
            const rows = await apiCall('GET', '/transactions');
            const trx = rows.find(t => t.TransactionID === id);
            if (!trx) { showToast('Transaction not found', 'error'); return; }

            const newDate = prompt('Date (YYYY-MM-DD):', trx.Date);
            if (newDate === null) return;

            const newType = prompt('Type (Income, Expense, Transfer, Owner Deposit, Owner Withdrawal, Adjustment):', trx.Type);
            if (newType === null) return;

            const newDebit = prompt('Debit account:', trx.DebitAccount);
            if (newDebit === null) return;

            const newCredit = prompt('Credit account:', trx.CreditAccount);
            if (newCredit === null) return;

            const newAmountStr = prompt('Amount:', trx.Amount);
            if (newAmountStr === null) return;
            const newAmount = parseFloat(newAmountStr);
            if (isNaN(newAmount) || newAmount <= 0) { showToast('Invalid amount', 'error'); return; }

            const newCategory = prompt('Category (optional):', trx.Category || '');
            if (newCategory === null) return;

            const newDesc = prompt('Description (optional):', trx.Description || '');
            if (newDesc === null) return;

            try {
                await apiCall('PUT', '/transactions/' + id, {
                    Date: newDate, Type: newType, DebitAccount: newDebit, CreditAccount: newCredit,
                    Amount: String(newAmount), Category: newCategory, Description: newDesc
                });
                showToast('Transaction ' + id + ' updated');
                await Promise.all([loadTransactionsList(), loadDashboard()]);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function deleteTransaction(id) {
            if (!confirm('Delete (cancel) transaction ' + id + '? This preserves the record but removes it from balances.')) return;
            try {
                await apiCall('DELETE', '/transactions/' + id);
                showToast('Transaction ' + id + ' deleted');
                await Promise.all([loadTransactionsList(), loadDashboard()]);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function reverseTransaction(id) {
            if (!confirm('Reverse transaction ' + id + '? This creates an offsetting entry and marks the original as Reversed.')) return;
            try {
                const result = await apiCall('POST', '/transactions/' + id + '/reverse');
                showToast('Reversed as ' + result.data.TransactionID);
                await Promise.all([loadTransactionsList(), loadDashboard()]);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        // ============================================================================
        // ACCOUNTS
        // ============================================================================
        
        let _accountsCache = [];

        async function loadAccounts() {
            try {
                showSkeleton('accountsTable', 5);
                const accounts = await apiCall('GET', '/accounts');
                _accountsCache = accounts;
                let html = '<table><tr><th>ID</th><th>Name</th><th>Type</th><th>Balance</th><th>Status</th></tr>';
                accounts.forEach(a => {
                    html += `<tr>
                        <td>${a.ID}</td><td>${a.AccountName}</td><td>${a.AccountType}</td>
                        <td>${formatCurrency(a.CurrentBalance)}</td>
                        <td><span class="${a.Status === 'Active' ? 'badge-active' : 'badge-inactive'}">${a.Status}</span></td>
                    </tr>`;
                });
                html += '</table>';
                document.getElementById('accountsTable').innerHTML = html || '<p class="text-muted">No accounts</p>';

                const select = document.getElementById('manageAccSelect');
                if (select) {
                    select.innerHTML = '<option value="">Select an account...</option>' +
                        accounts.map(a => `<option value="${a.ID}">${a.AccountName} (${a.Status})</option>`).join('');
                    document.getElementById('manageAccActions').style.display = 'none';
                }
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function saveAccount() {
            const name = document.getElementById('newAccName').value.trim();
            const type = document.getElementById('newAccType').value;
            const balance = parseFloat(document.getElementById('newAccBalance').value) || 0;
            const currency = document.getElementById('newAccCurrency').value;
            const desc = document.getElementById('newAccDesc').value.trim();

            if (!name) { showToast('Account name is required', 'error'); return; }

            try {
                await apiCall('POST', '/accounts', {
                    AccountName: name, AccountType: type, OpeningBalance: balance,
                    Currency: currency, Description: desc
                });
                showToast('Account created!');
                document.getElementById('newAccName').value = '';
                document.getElementById('newAccDesc').value = '';
                loadAccounts();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        function onManageAccSelect() {
            const id = document.getElementById('manageAccSelect').value;
            const actions = document.getElementById('manageAccActions');
            if (!id) { actions.style.display = 'none'; return; }
            const acc = _accountsCache.find(a => a.ID === id);
            actions.style.display = 'block';
            document.getElementById('manageAccToggleBtn').textContent =
                acc && acc.Status === 'Active' ? '🔄 Set Inactive' : '🔄 Set Active';
        }

        async function toggleAccountStatus() {
            const id = document.getElementById('manageAccSelect').value;
            if (!id) return;
            const acc = _accountsCache.find(a => a.ID === id);
            const newStatus = acc && acc.Status === 'Active' ? 'Inactive' : 'Active';
            try {
                await apiCall('PUT', '/accounts/' + id, { Status: newStatus });
                showToast('Account status updated!');
                loadAccounts();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function editAccount() {
            const id = document.getElementById('manageAccSelect').value;
            if (!id) return;
            const acc = _accountsCache.find(a => a.ID === id);
            if (!acc) return;

            const ACCOUNT_TYPES = ['Cash','Bank','Credit Card','Asset','Liability','Equity','Income','Expense'];
            const CURRENCIES = ['AED','INR','USD'];

            const newName = prompt('Account name:', acc.AccountName);
            if (newName === null) return;

            const newType = prompt(`Account type (one of: ${ACCOUNT_TYPES.join(', ')}):`, acc.AccountType);
            if (newType === null) return;
            if (!ACCOUNT_TYPES.includes(newType)) { showToast('Invalid account type. Must be one of: ' + ACCOUNT_TYPES.join(', '), 'error'); return; }

            const newCurrency = prompt(`Currency (one of: ${CURRENCIES.join(', ')}):`, acc.Currency || 'AED');
            if (newCurrency === null) return;
            if (!CURRENCIES.includes(newCurrency)) { showToast('Invalid currency. Must be one of: ' + CURRENCIES.join(', '), 'error'); return; }

            const newDesc = prompt('Description:', acc.Description || '');
            if (newDesc === null) return;

            try {
                await apiCall('PUT', '/accounts/' + id, {
                    AccountName: newName.trim(), AccountType: newType, Currency: newCurrency, Description: newDesc
                });
                showToast('Account updated!');
                await Promise.all([loadAccounts(), loadDashboard()]);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function deleteAccount() {
            const id = document.getElementById('manageAccSelect').value;
            if (!id) return;
            if (!confirm('Delete this account? This cannot be undone.')) return;
            try {
                await apiCall('DELETE', '/accounts/' + id);
                showToast('Account deleted!');
                loadAccounts();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function correctAccountBalance() {
            const id = document.getElementById('manageAccSelect').value;
            if (!id) return;
            const acc = _accountsCache.find(a => a.ID === id);
            const current = acc ? acc.OpeningBalance : '';
            const input = prompt(`Corrected OPENING balance for "${acc ? acc.AccountName : id}" (current opening: ${current}). This does not touch existing transactions - all ledger activity since the opening date still applies on top of this figure.`, current);
            if (input === null || input.trim() === '') return;
            const newBalance = parseFloat(input);
            if (isNaN(newBalance)) { showToast('Invalid amount', 'error'); return; }
            const reason = prompt('Reason for this correction (for the audit log):', '') || '';
            try {
                const result = await apiCall('POST', '/accounts/' + id + '/correct-balance', { NewOpeningBalance: newBalance, Reason: reason });
                showToast('Opening balance corrected. New balance: ' + formatCurrency(result.new_balance));
                await Promise.all([loadAccounts(), loadDashboard()]);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        // ============================================================================
        // CATEGORIES
        // ============================================================================
        
        let _categoriesCache = [];

        async function loadCategories() {
            try {
                showSkeleton('categoriesTable', 5);
                const categories = await apiCall('GET', '/categories');
                _categoriesCache = categories;
                let html = '<table><tr><th>ID</th><th>Name</th><th>Type</th><th>Status</th></tr>';
                categories.forEach(c => {
                    html += `<tr>
                        <td>${c.ID}</td><td>${c.CategoryName}</td><td>${c.CategoryType}</td>
                        <td><span class="${c.Status === 'Active' ? 'badge-active' : 'badge-inactive'}">${c.Status}</span></td>
                    </tr>`;
                });
                html += '</table>';
                document.getElementById('categoriesTable').innerHTML = html || '<p class="text-muted">No categories</p>';

                const select = document.getElementById('manageCatSelect');
                if (select) {
                    select.innerHTML = '<option value="">Select a category...</option>' +
                        categories.map(c => `<option value="${c.ID}">${c.CategoryName} (${c.Status})</option>`).join('');
                    document.getElementById('manageCatActions').style.display = 'none';
                }
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function saveCategory() {
            const name = document.getElementById('newCatName').value.trim();
            const type = document.getElementById('newCatType').value;
            const desc = document.getElementById('newCatDesc').value.trim();

            if (!name) { showToast('Category name is required', 'error'); return; }

            try {
                await apiCall('POST', '/categories', { CategoryName: name, CategoryType: type, Description: desc });
                showToast('Category created!');
                document.getElementById('newCatName').value = '';
                document.getElementById('newCatDesc').value = '';
                loadCategories();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        function onManageCatSelect() {
            const id = document.getElementById('manageCatSelect').value;
            const actions = document.getElementById('manageCatActions');
            if (!id) { actions.style.display = 'none'; return; }
            const cat = _categoriesCache.find(c => c.ID === id);
            actions.style.display = 'block';
            document.getElementById('manageCatToggleBtn').textContent =
                cat && cat.Status === 'Active' ? '🔄 Set Inactive' : '🔄 Set Active';
        }

        async function toggleCategoryStatus() {
            const id = document.getElementById('manageCatSelect').value;
            if (!id) return;
            const cat = _categoriesCache.find(c => c.ID === id);
            const newStatus = cat && cat.Status === 'Active' ? 'Inactive' : 'Active';
            try {
                await apiCall('PUT', '/categories/' + id, { Status: newStatus });
                showToast('Category status updated!');
                loadCategories();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function deleteCategory() {
            const id = document.getElementById('manageCatSelect').value;
            if (!id) return;
            if (!confirm('Delete this category? This cannot be undone.')) return;
            try {
                await apiCall('DELETE', '/categories/' + id);
                showToast('Category deleted!');
                loadCategories();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        // ============================================================================
        // USERS
        // ============================================================================
        
        async function loadUsers() {
            try {
                showSkeleton('usersTable', 4);
                const users = await apiCall('GET', '/users');
                let html = '<table><tr><th>ID</th><th>Username</th><th>Full Name</th><th>Role</th><th>Status</th><th>Actions</th></tr>';
                users.forEach(u => {
                    const isSelf = currentUser && currentUser.username === u.Username;
                    const isActive = u.Active === 'True';
                    html += `<tr>
                        <td>${u.ID}</td><td>@${u.Username}</td><td>${u.FullName}</td>
                        <td>${u.Role.toUpperCase()}</td>
                        <td><span class="${isActive ? 'badge-active' : 'badge-inactive'}">${isActive ? 'Active' : 'Inactive'}</span></td>
                        <td>
                            <button class="btn btn-outline-secondary btn-sm" onclick="resetUserPassword('${u.ID}')" title="Reset Password">🔑</button>
                            ${!isSelf ? `
                            <button class="btn btn-outline-secondary btn-sm" onclick="toggleUserStatus('${u.ID}', ${isActive})" title="${isActive ? 'Disable' : 'Enable'}">${isActive ? '🚫' : '✅'}</button>
                            <button class="btn btn-outline-secondary btn-sm" onclick="changeUserRole('${u.ID}', '${u.Role}')" title="Change Role">🔄</button>
                            <button class="btn btn-outline-secondary btn-sm" onclick="deleteUser('${u.ID}')" title="Delete">🗑</button>
                            ` : '<span class="text-muted" style="font-size:0.78rem;">(you)</span>'}
                        </td>
                    </tr>`;
                });
                html += '</table>';
                document.getElementById('usersTable').innerHTML = html || '<p class="text-muted">No users</p>';
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function saveUser() {
            const username = document.getElementById('newUserName').value.trim();
            const fullName = document.getElementById('newUserFullName').value.trim();
            const password = document.getElementById('newUserPassword').value;
            const role = document.getElementById('newUserRole').value;

            if (!username || !fullName || !password) {
                showToast('Username, full name, and password are required', 'error');
                return;
            }

            try {
                await apiCall('POST', '/users', { Username: username, FullName: fullName, Password: password, Role: role });
                showToast(`User '${username}' created!`);
                document.getElementById('newUserName').value = '';
                document.getElementById('newUserFullName').value = '';
                document.getElementById('newUserPassword').value = '';
                loadUsers();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function resetUserPassword(id) {
            const newPassword = prompt('Enter a new password (leave blank to auto-generate a strong one):', '');
            if (newPassword === null) return;
            try {
                const res = await apiCall('POST', '/users/reset-password', { UserID: id, NewPassword: newPassword || '' });
                if (!newPassword) {
                    showToast('Password reset! New password: ' + res.new_password, 'info');
                } else {
                    showToast('Password changed successfully!');
                }
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function toggleUserStatus(id, isCurrentlyActive) {
            try {
                await apiCall('PUT', `/users/${id}/${isCurrentlyActive ? 'disable' : 'enable'}`);
                showToast(`User ${isCurrentlyActive ? 'disabled' : 'enabled'}!`);
                loadUsers();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function changeUserRole(id, currentRole) {
            const newRole = currentRole === 'admin' ? 'user' : 'admin';
            if (!confirm(`Change this user's role to "${newRole}"?`)) return;
            try {
                await apiCall('PUT', '/users/' + id + '/role', { UserID: id, Role: newRole });
                showToast('Role updated!');
                loadUsers();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function deleteUser(id) {
            if (!confirm('Delete this user? This cannot be undone.')) return;
            try {
                await apiCall('DELETE', '/users/' + id);
                showToast('User deleted!');
                loadUsers();
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        // ============================================================================
        // REPORTS
        // ============================================================================
        
        async function loadReport(type) {
            try {
                if (typeof event !== 'undefined' && event.target && event.target.classList.contains('nav-link')) {
                    const tabsContainer = event.target.closest('.nav-tabs');
                    if (tabsContainer) {
                        tabsContainer.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
                        event.target.classList.add('active');
                    }
                }

                const container = document.getElementById('reportCharts');
                container.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

                const data = await apiCall('GET', '/reports?report_type=' + type);
                let html = '';

                if (type === 'summary') {
                    html += `
                        <div class="row g-3 mb-3">
                            <div class="col-md-4"><div class="kpi-card"><div class="value">${formatCurrency(data.kpi.total_income)}</div><div class="label">Total Income</div></div></div>
                            <div class="col-md-4"><div class="kpi-card"><div class="value">${formatCurrency(data.kpi.total_expense)}</div><div class="label">Total Expenses</div></div></div>
                            <div class="col-md-4"><div class="kpi-card"><div class="value">${formatCurrency(data.kpi.net_worth)}</div><div class="label">Net Worth</div></div></div>
                        </div>
                        <div class="row g-3">
                            <div class="col-md-6">
                                <div class="table-container"><h6>Account Balances</h6><div id="reportBalances"></div></div>
                            </div>
                            <div class="col-md-6">
                                <div class="table-container"><h6>Monthly Summary</h6>
                                    <table><tr><th>Month</th><th>Income</th><th>Expense</th><th>Count</th></tr>
                                    ${Object.keys(data.monthly_summary || {}).sort().reverse().map(m => {
                                        const row = data.monthly_summary[m];
                                        return `<tr><td>${m}</td><td>${formatCurrency(row.income)}</td><td>${formatCurrency(row.expense)}</td><td>${row.count}</td></tr>`;
                                    }).join('')}
                                    </table>
                                </div>
                            </div>
                        </div>`;
                    container.innerHTML = html;

                    let balHtml = '';
                    for (const [name, info] of Object.entries(data.balances || {})) {
                        balHtml += `<div style="display:flex;justify-content:space-between;padding:0.6rem 0;border-bottom:1px solid var(--border);">
                            <span>📁 ${name} (${info.type})</span>
                            <strong>${formatCurrency(info.balance)}</strong>
                        </div>`;
                    }
                    document.getElementById('reportBalances').innerHTML = balHtml || '<p class="text-muted">No accounts</p>';
                    return;
                }

                if (type === 'category') {
                    html = `<div class="table-container"><h6>Category-wise Breakdown</h6><table><tr><th>Category</th><th>Income</th><th>Expense</th><th>Count</th></tr>`;
                    for (const [cat, c] of Object.entries(data.category_wise || {})) {
                        html += `<tr><td>${cat}</td><td>${formatCurrency(c.income)}</td><td>${formatCurrency(c.expense)}</td><td>${c.count}</td></tr>`;
                    }
                    html += '</table></div>';
                } else if (type === 'account') {
                    html = `<div class="table-container"><h6>Account-wise Breakdown</h6><table><tr><th>Account</th><th>Debits</th><th>Credits</th><th>Balance</th><th>Count</th></tr>`;
                    for (const [acc, a] of Object.entries(data.account_wise || {})) {
                        html += `<tr><td>${acc}</td><td>${formatCurrency(a.debits)}</td><td>${formatCurrency(a.credits)}</td><td>${formatCurrency(a.balance)}</td><td>${a.count}</td></tr>`;
                    }
                    html += '</table></div>';
                } else if (type === 'user') {
                    html = `<div class="table-container"><h6>User-wise Breakdown</h6><table><tr><th>User</th><th>Income</th><th>Expense</th><th>Count</th></tr>`;
                    for (const [usr, u] of Object.entries(data.user_wise || {})) {
                        html += `<tr><td>@${usr}</td><td>${formatCurrency(u.income)}</td><td>${formatCurrency(u.expense)}</td><td>${u.count}</td></tr>`;
                    }
                    html += '</table></div>';
                } else if (type === 'cashflow') {
                    html = `<div class="row g-3">
                        <div class="col-md-4"><div class="kpi-card"><div class="value">${formatCurrency(data.cash_flow.total_inflow)}</div><div class="label">Total Inflow</div></div></div>
                        <div class="col-md-4"><div class="kpi-card"><div class="value">${formatCurrency(data.cash_flow.total_outflow)}</div><div class="label">Total Outflow</div></div></div>
                        <div class="col-md-4"><div class="kpi-card"><div class="value">${formatCurrency(data.cash_flow.net_cashflow)}</div><div class="label">Net Cash Flow</div></div></div>
                    </div>`;
                } else if (type === 'top') {
                    html = `<div class="table-container"><h6>Top 20 Transactions by Amount</h6><table><tr><th>ID</th><th>Date</th><th>Type</th><th>Debit</th><th>Credit</th><th>Amount</th><th>Category</th><th>User</th></tr>`;
                    (data.top_transactions || []).forEach(t => {
                        html += `<tr><td>${t.id}</td><td>${t.date}</td><td>${t.type}</td><td>${t.debit}</td><td>${t.credit}</td><td>${formatCurrency(t.amount)}</td><td>${t.category || ''}</td><td>@${t.user}</td></tr>`;
                    });
                    html += '</table></div>';
                }

                container.innerHTML = html;
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        // ============================================================================
        // SETTINGS
        // ============================================================================
        
        async function loadSettings() {
            try {
                const [accounts, categories, transactions, users] = await Promise.all([
                    apiCall('GET', '/accounts'),
                    apiCall('GET', '/categories'),
                    apiCall('GET', '/transactions'),
                    apiCall('GET', '/users').catch(() => [])
                ]);
                document.getElementById('sysStats').innerHTML = `
                    <div class="row g-3">
                        <div class="col-md-3"><div class="kpi-card"><div class="value">${users.length}</div><div class="label">Users</div></div></div>
                        <div class="col-md-3"><div class="kpi-card"><div class="value">${accounts.length}</div><div class="label">Accounts</div></div></div>
                        <div class="col-md-3"><div class="kpi-card"><div class="value">${categories.length}</div><div class="label">Categories</div></div></div>
                        <div class="col-md-3"><div class="kpi-card"><div class="value">${transactions.length}</div><div class="label">Transactions</div></div></div>
                    </div>`;
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
        
        // ============================================================================
        // INIT
        // ============================================================================
        
        document.getElementById('trxDate').value = new Date().toISOString().split('T')[0];
        
        // Check if already logged in
        if (token) {
            try {
                currentUser = JSON.parse(localStorage.getItem('user') || '{}');
                document.getElementById('loginPage').classList.remove('active');
                document.getElementById('appPage').classList.add('active');
                document.getElementById('headerUser').innerHTML = `${currentUser.fullName} <span class="user-badge">${currentUser.role.toUpperCase()}</span>`;
                if (currentUser.role === 'admin') {
                    document.querySelectorAll('.admin-only').forEach(el => el.style.display = 'flex');
                }
                loadDashboard();
                updateTime();
                setInterval(updateTime, 60000);
                updateTrxForm();
            } catch {
                logout();
            }
        }
        
        // Enter key for login
        document.getElementById('loginPassword').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') login();
        });
    </script>
</body>
</html>"""

@app.get("/")
def serve_html():
    return HTMLResponse(HTML_CONTENT)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    def find_port(start=8000):
        for port in range(start, start+10):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('0.0.0.0', port))
                    return port
            except OSError:
                continue
        return 8000
    
    port = find_port(8000)
    print(f"""
    ╔══════════════════════════════════════════╗
    ║  🏦 Finance Management System v{Config.VERSION}    ║
    ║  http://localhost:{port}                  ║
    ╚══════════════════════════════════════════╝
    """)
    uvicorn.run(app, host="0.0.0.0", port=port)