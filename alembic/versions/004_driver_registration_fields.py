"""Add driver profile/address fields and bank UPI for registration."""
from alembic import op
import sqlalchemy as sa


revision = "004_driver_registration_fields"
down_revision = "003_unified_backend"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    driver_columns = [
        ("date_of_birth", sa.Date(), True),
        ("gender", sa.String(length=20), True),
        ("referral_code", sa.String(length=50), True),
        ("address_line", sa.String(length=500), True),
        ("city", sa.String(length=100), True),
        ("state", sa.String(length=100), True),
        ("country", sa.String(length=100), True),
        ("pin_code", sa.String(length=20), True),
    ]
    for name, col_type, nullable in driver_columns:
        if not _has_column("drivers", name):
            op.add_column("drivers", sa.Column(name, col_type, nullable=nullable))

    if not _has_column("driver_bank_accounts", "upi_id"):
        op.add_column(
            "driver_bank_accounts",
            sa.Column("upi_id", sa.String(length=100), nullable=True),
        )


def downgrade() -> None:
    if _has_column("driver_bank_accounts", "upi_id"):
        op.drop_column("driver_bank_accounts", "upi_id")

    for name in (
        "pin_code",
        "country",
        "state",
        "city",
        "address_line",
        "referral_code",
        "gender",
        "date_of_birth",
    ):
        if _has_column("drivers", name):
            op.drop_column("drivers", name)
