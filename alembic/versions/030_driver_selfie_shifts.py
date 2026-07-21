"""Driver selfie verification + shift tables."""
revision = "030_driver_selfie_shifts"
down_revision = "029_ride_stops"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table("driver_selfie_logs"):
        op.create_table(
            "driver_selfie_logs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("driver_id", UUID(as_uuid=True), sa.ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("shift_id", UUID(as_uuid=True), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("matched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("confidence_score", sa.Float(), nullable=True),
            sa.Column("liveness_passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("liveness_details", JSONB(), nullable=True),
            sa.Column("face_provider", sa.String(50), nullable=True),
            sa.Column("liveness_provider", sa.String(50), nullable=True),
            sa.Column("selfie_image_path", sa.String(500), nullable=True),
            sa.Column("registered_image_path", sa.String(500), nullable=True),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("device_id", sa.String(255), nullable=True),
            sa.Column("source", sa.String(40), nullable=False, server_default="live_camera"),
            sa.Column("ip_address", sa.String(64), nullable=True),
            sa.Column("consumed_for_shift", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_driver_selfie_logs_driver_id", "driver_selfie_logs", ["driver_id"])
        op.create_index("ix_driver_selfie_logs_status", "driver_selfie_logs", ["status"])
        op.create_index(
            "ix_driver_selfie_logs_driver_created",
            "driver_selfie_logs",
            ["driver_id", "created_at"],
        )

    if not _has_table("driver_shifts"):
        op.create_table(
            "driver_shifts",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("driver_id", UUID(as_uuid=True), sa.ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="active"),
            sa.Column("selfie_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("selfie_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("force_close_reason", sa.String(255), nullable=True),
            sa.Column("verification_log_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_driver_shifts_driver_id", "driver_shifts", ["driver_id"])
        op.create_index("ix_driver_shifts_status", "driver_shifts", ["status"])
        op.create_index("ix_driver_shifts_started_at", "driver_shifts", ["started_at"])
        op.create_index("ix_driver_shifts_driver_status", "driver_shifts", ["driver_id", "status"])

    # Add FK from selfie_logs.shift_id → driver_shifts after both tables exist
    bind = op.get_bind()
    inspector = inspect(bind)
    fks = {fk["name"] for fk in inspector.get_foreign_keys("driver_selfie_logs")} if _has_table("driver_selfie_logs") else set()
    if "fk_driver_selfie_logs_shift_id" not in fks and _has_table("driver_selfie_logs") and _has_table("driver_shifts"):
        op.create_foreign_key(
            "fk_driver_selfie_logs_shift_id",
            "driver_selfie_logs",
            "driver_shifts",
            ["shift_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_driver_selfie_logs_shift_id", "driver_selfie_logs", ["shift_id"])


def downgrade() -> None:
    if _has_table("driver_selfie_logs"):
        bind = op.get_bind()
        inspector = inspect(bind)
        fks = {fk["name"] for fk in inspector.get_foreign_keys("driver_selfie_logs")}
        if "fk_driver_selfie_logs_shift_id" in fks:
            op.drop_constraint("fk_driver_selfie_logs_shift_id", "driver_selfie_logs", type_="foreignkey")
        op.drop_table("driver_selfie_logs")
    if _has_table("driver_shifts"):
        op.drop_table("driver_shifts")
