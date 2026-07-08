"""Student pass and subscription migration."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID
import json

revision = "013_student_pass_subscriptions"
down_revision = "012_dual_ratings"
branch_labels = None
depends_on = None

DEFAULT_PLANS = [
    {
        "slug": "free",
        "name": "Free",
        "description": "Essential rides at standard rates",
        "price": 0,
        "period_label": "forever",
        "benefits": ["Book rides anytime", "Standard pricing", "In-app support"],
        "ride_discount_percent": 0,
        "is_popular": False,
        "sort_order": 0,
    },
    {
        "slug": "plus",
        "name": "Plus",
        "description": "Save more on every trip",
        "price": 99,
        "period_label": "month",
        "benefits": [
            "5% off on every ride",
            "Priority booking",
            "No peak-hour surge up to 10%",
            "24/7 chat support",
        ],
        "ride_discount_percent": 5,
        "is_popular": True,
        "sort_order": 1,
    },
    {
        "slug": "premium",
        "name": "Premium",
        "description": "Best value for frequent riders",
        "price": 199,
        "period_label": "month",
        "benefits": [
            "10% off on every ride",
            "Zero surge pricing",
            "Priority driver matching",
            "Free cancellations (2/month)",
            "Dedicated support line",
        ],
        "ride_discount_percent": 10,
        "is_popular": False,
        "sort_order": 2,
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "subscription_plans" not in tables:
        op.create_table(
            "subscription_plans",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("slug", sa.String(50), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price", sa.Float(), nullable=False, server_default="0"),
            sa.Column("period_label", sa.String(30), nullable=False, server_default="month"),
            sa.Column("benefits_json", sa.Text(), nullable=True),
            sa.Column("ride_discount_percent", sa.Float(), nullable=False, server_default="0"),
            sa.Column("is_popular", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("slug", name="uq_subscription_plans_slug"),
        )
        op.create_index("ix_subscription_plans_slug", "subscription_plans", ["slug"])

    if "user_subscriptions" not in tables:
        op.create_table(
            "user_subscriptions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("plan_id", UUID(as_uuid=True), sa.ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_user_subscriptions_user_id"),
        )
        op.create_index("ix_user_subscriptions_user_id", "user_subscriptions", ["user_id"])

    if "student_passes" not in tables:
        op.create_table(
            "student_passes",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("aadhar_number", sa.String(12), nullable=False),
            sa.Column("college_name", sa.String(200), nullable=False),
            sa.Column("aadhar_photo_url", sa.String(500), nullable=True),
            sa.Column("student_id_photo_url", sa.String(500), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("discount_percent", sa.Float(), nullable=False, server_default="20"),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("verified_by_id", UUID(as_uuid=True), sa.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_student_passes_user_id"),
        )
        op.create_index("ix_student_passes_user_id", "student_passes", ["user_id"])
        op.create_index("ix_student_passes_status", "student_passes", ["status"])

    plan_count = bind.execute(sa.text("SELECT COUNT(*) FROM subscription_plans")).scalar()
    if plan_count:
        return

    import uuid

    plans_table = sa.table(
        "subscription_plans",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("price", sa.Float),
        sa.column("period_label", sa.String),
        sa.column("benefits_json", sa.Text),
        sa.column("ride_discount_percent", sa.Float),
        sa.column("is_popular", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )

    rows = []
    for plan in DEFAULT_PLANS:
        rows.append(
            {
                "id": uuid.uuid4(),
                "slug": plan["slug"],
                "name": plan["name"],
                "description": plan["description"],
                "price": plan["price"],
                "period_label": plan["period_label"],
                "benefits_json": json.dumps(plan["benefits"]),
                "ride_discount_percent": plan["ride_discount_percent"],
                "is_popular": plan["is_popular"],
                "is_active": True,
                "sort_order": plan["sort_order"],
            }
        )
    op.bulk_insert(plans_table, rows)


def downgrade() -> None:
    op.drop_table("student_passes")
    op.drop_table("user_subscriptions")
    op.drop_table("subscription_plans")
