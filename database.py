"""
database.py - SQLAlchemy engine/session setup for Supabase PostgreSQL.

Reads DATABASE_URL from .env (python-dotenv). Requires the real Postgres
connection string with your DB password filled in, e.g.:

    DATABASE_URL=postgresql://postgres:YOUR-ACTUAL-PASSWORD@db.rnhphksualvsprnjxcmy.supabase.co:5432/postgres

The SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY / SUPABASE_JWKS_URL values are
for Supabase's REST/Auth API - they are NOT usable as a Postgres password,
so they are not read here. DATABASE_URL is a separate, additional value
you add to .env yourself (Supabase dashboard -> Project Settings ->
Database -> Connection string -> URI).
"""
import os
from contextlib import contextmanager
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set in .env. Add your Supabase Postgres connection "
        "string, e.g.:\n"
        "  DATABASE_URL=postgresql://postgres:YOUR-PASSWORD@db.rnhphksualvsprnjxcmy.supabase.co:5432/postgres\n"
        "Find your DB password in Supabase -> Project Settings -> Database."
    )

# Pool sized for comfortably more than the "at least 10 concurrent users"
# requirement, with overflow headroom for bursts. pool_pre_ping avoids
# handing out dead connections after Supabase idles one out.
engine = create_engine(
    DATABASE_URL,
    pool_size=15,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


@contextmanager
def get_session():
    """
    One session per unit of work, always committed or rolled back, always
    closed. This - not any manual locking - is what makes concurrent access
    safe: Postgres itself handles row-level locking and MVCC isolation
    between simultaneous sessions, so N users hitting the API at once each
    get their own short-lived transaction rather than sharing one global
    mutable dict the way the old JSON store did.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """Create tables if they don't exist yet. Safe to call on every startup
    (idempotent) - real schema changes should go through Alembic instead."""
    import models  # noqa: F401 (ensures models are registered on Base before create_all)
    Base.metadata.create_all(bind=engine)
