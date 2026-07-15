"""Add gender column to users for women safety features."""
revision = "019_user_gender"
down_revision = "018_notification_fcm"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "gender" not in user_cols:
        op.add_column("users", sa.Column("gender", sa.String(length=20), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "gender" in user_cols:
        op.drop_column("users", "gender")
