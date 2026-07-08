"""Add device metadata and notification delivery status for FCM."""
revision = "018_notification_fcm"
down_revision = "017_vehicle_commission"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    driver_cols = {c["name"] for c in insp.get_columns("drivers")}
    notification_cols = {c["name"] for c in insp.get_columns("notifications")}

    if "device_type" not in user_cols:
        op.add_column("users", sa.Column("device_type", sa.String(length=20), nullable=True))
    if "device_id" not in user_cols:
        op.add_column("users", sa.Column("device_id", sa.String(length=255), nullable=True))
    if "last_login_device" not in user_cols:
        op.add_column("users", sa.Column("last_login_device", sa.String(length=255), nullable=True))

    if "device_type" not in driver_cols:
        op.add_column("drivers", sa.Column("device_type", sa.String(length=20), nullable=True))
    if "device_id" not in driver_cols:
        op.add_column("drivers", sa.Column("device_id", sa.String(length=255), nullable=True))
    if "last_login_device" not in driver_cols:
        op.add_column("drivers", sa.Column("last_login_device", sa.String(length=255), nullable=True))

    if "status" not in notification_cols:
        op.add_column(
            "notifications",
            sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        )
    if "sent_at" not in notification_cols:
        op.add_column("notifications", sa.Column("sent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("notifications", "sent_at")
    op.drop_column("notifications", "status")

    op.drop_column("drivers", "last_login_device")
    op.drop_column("drivers", "device_id")
    op.drop_column("drivers", "device_type")

    op.drop_column("users", "last_login_device")
    op.drop_column("users", "device_id")
    op.drop_column("users", "device_type")
