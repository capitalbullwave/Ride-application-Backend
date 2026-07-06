"""Initialize local PostgreSQL role + database for the backend.

Usage (PowerShell):
    $env:PGPASSWORD = "your-postgres-superuser-password"
    python scripts/init_postgres.py

Optional env:
    PGHOST=localhost  PGPORT=5432  PGUSER=postgres
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

APP_USER = "rideuser"
APP_PASSWORD = "ridepass"
APP_DB = "ridebooking"


def main() -> int:
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    superuser = os.getenv("PGUSER", "postgres")
    superpassword = os.getenv("PGPASSWORD")

    if not superpassword:
        print(
            "Set PGPASSWORD to your local PostgreSQL superuser password, then rerun.\n"
            "Example: $env:PGPASSWORD = 'your-password'; python scripts/init_postgres.py",
            file=sys.stderr,
        )
        return 1

    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=superuser,
            password=superpassword,
            dbname="postgres",
        )
    except psycopg2.Error as exc:
        print(f"Could not connect as '{superuser}': {exc}", file=sys.stderr)
        return 1

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_USER,))
    if cur.fetchone():
        cur.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(APP_USER)),
            (APP_PASSWORD,),
        )
        print(f"Updated role '{APP_USER}' password.")
    else:
        cur.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(APP_USER)),
            (APP_PASSWORD,),
        )
        print(f"Created role '{APP_USER}'.")

    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (APP_DB,))
    if cur.fetchone():
        print(f"Database '{APP_DB}' already exists.")
    else:
        cur.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(APP_DB),
                sql.Identifier(APP_USER),
            )
        )
        print(f"Created database '{APP_DB}'.")

    cur.close()
    conn.close()

    print("\nDatabase ready. URLs:")
    print(f"  DATABASE_URL=postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{APP_DB}")
    print(f"  DATABASE_SYNC_URL=postgresql://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{APP_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
