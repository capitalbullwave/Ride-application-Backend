"""Use timezone-aware last_login_at on admin_users."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "005_admin_last_login_tz"
down_revision = "004_driver_registration_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "admin_users",
        "last_login_at",
        existing_type=postgresql.TIMESTAMP(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="last_login_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "admin_users",
        "last_login_at",
        existing_type=sa.DateTime(timezone=True),
        type_=postgresql.TIMESTAMP(),
        existing_nullable=True,
    )
