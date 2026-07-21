"""Corporate B2B tables + ride corporate columns."""
revision = "031_corporate_module"
down_revision = "030_driver_selfie_shifts"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns(table)}
    return column in cols


def upgrade() -> None:
    if not _has_table("companies"):
        op.create_table(
            "companies",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("company_name", sa.String(200), nullable=False),
            sa.Column("company_code", sa.String(40), nullable=False),
            sa.Column("gst_number", sa.String(20), nullable=True),
            sa.Column("pan_number", sa.String(20), nullable=True),
            sa.Column("website", sa.String(255), nullable=True),
            sa.Column("industry", sa.String(100), nullable=True),
            sa.Column("company_size", sa.String(50), nullable=True),
            sa.Column("address", sa.String(500), nullable=True),
            sa.Column("city", sa.String(100), nullable=True),
            sa.Column("state", sa.String(100), nullable=True),
            sa.Column("country", sa.String(100), nullable=False, server_default="India"),
            sa.Column("contact_person", sa.String(150), nullable=False),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("phone", sa.String(20), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("credit_limit", sa.Float(), nullable=False, server_default="0"),
            sa.Column("wallet_balance", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_companies_company_code", "companies", ["company_code"], unique=True)
        op.create_index("ix_companies_email", "companies", ["email"], unique=True)
        op.create_index("ix_companies_gst_number", "companies", ["gst_number"])
        op.create_index("ix_companies_phone", "companies", ["phone"])
        op.create_index("ix_companies_status", "companies", ["status"])
        op.create_index("ix_companies_status_created", "companies", ["status", "created_at"])

    if not _has_table("company_employees"):
        op.create_table(
            "company_employees",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("employee_code", sa.String(50), nullable=False),
            sa.Column("department", sa.String(100), nullable=True),
            sa.Column("designation", sa.String(100), nullable=True),
            sa.Column("ride_limit", sa.Float(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("company_id", "user_id", name="uq_company_employees_company_user"),
            sa.UniqueConstraint("company_id", "employee_code", name="uq_company_employees_code"),
        )
        op.create_index("ix_company_employees_company_id", "company_employees", ["company_id"])
        op.create_index("ix_company_employees_user_id", "company_employees", ["user_id"])
        op.create_index("ix_company_employees_status", "company_employees", ["status"])
        op.create_index("ix_company_employees_user_status", "company_employees", ["user_id", "status"])

    if not _has_table("company_policies"):
        op.create_table(
            "company_policies",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("allowed_vehicle_types", JSONB(), nullable=True),
            sa.Column("max_ride_amount", sa.Float(), nullable=True),
            sa.Column("office_start_time", sa.Time(), nullable=True),
            sa.Column("office_end_time", sa.Time(), nullable=True),
            sa.Column("working_days", JSONB(), nullable=True),
            sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("purpose_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("company_id", name="uq_company_policies_company"),
        )
        op.create_index("ix_company_policies_company_id", "company_policies", ["company_id"])

    if _has_table("rides"):
        if not _has_column("rides", "ride_type"):
            op.add_column(
                "rides",
                sa.Column("ride_type", sa.String(20), nullable=False, server_default="NORMAL"),
            )
            op.create_index("ix_rides_ride_type", "rides", ["ride_type"])
        if not _has_column("rides", "company_id"):
            op.add_column(
                "rides",
                sa.Column(
                    "company_id",
                    UUID(as_uuid=True),
                    sa.ForeignKey("companies.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
            op.create_index("ix_rides_company_id", "rides", ["company_id"])
        if not _has_column("rides", "employee_id"):
            op.add_column(
                "rides",
                sa.Column(
                    "employee_id",
                    UUID(as_uuid=True),
                    sa.ForeignKey("company_employees.id", ondelete="SET NULL"),
                    nullable=True,
                ),
            )
            op.create_index("ix_rides_employee_id", "rides", ["employee_id"])
        if not _has_column("rides", "payment_source"):
            op.add_column(
                "rides",
                sa.Column("payment_source", sa.String(20), nullable=False, server_default="USER"),
            )


def downgrade() -> None:
    if _has_table("rides"):
        if _has_column("rides", "payment_source"):
            op.drop_column("rides", "payment_source")
        if _has_column("rides", "employee_id"):
            op.drop_index("ix_rides_employee_id", table_name="rides")
            op.drop_column("rides", "employee_id")
        if _has_column("rides", "company_id"):
            op.drop_index("ix_rides_company_id", table_name="rides")
            op.drop_column("rides", "company_id")
        if _has_column("rides", "ride_type"):
            op.drop_index("ix_rides_ride_type", table_name="rides")
            op.drop_column("rides", "ride_type")

    if _has_table("company_policies"):
        op.drop_table("company_policies")
    if _has_table("company_employees"):
        op.drop_table("company_employees")
    if _has_table("companies"):
        op.drop_table("companies")
