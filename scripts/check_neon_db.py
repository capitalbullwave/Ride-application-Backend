from sqlalchemy import create_engine, text

from app.core.config import settings

engine = create_engine(settings.database_sync_url)
with engine.connect() as conn:
    tables = conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    ).fetchall()
    print("TABLES:", ", ".join(t[0] for t in tables) or "(none)")
    try:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        print("ALEMBIC:", version)
    except Exception as exc:
        print("ALEMBIC: (missing)", exc)
