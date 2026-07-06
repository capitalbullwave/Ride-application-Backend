"""Module 2 — new tables, columns, and indexes.

Revision ID: 002_module2_database
Revises: 001_initial
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_module2_database"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in _inspector().get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    if not _has_table(table):
        return False
    return index in {i["name"] for i in _inspector().get_indexes(table)}


def upgrade() -> None:
    # --- users ---
    if not _has_column("users", "referral_code"):
        op.add_column("users", sa.Column("referral_code", sa.String(length=20), nullable=True))
    if not _has_column("users", "referred_by_id"):
        op.add_column("users", sa.Column("referred_by_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key("fk_users_referred_by", "users", "users", ["referred_by_id"], ["id"], ondelete="SET NULL")
    if not _has_column("users", "google_id"):
        op.add_column("users", sa.Column("google_id", sa.String(length=255), nullable=True))
    if not _has_index("users", "ix_users_referral_code"):
        op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)
    if not _has_index("users", "ix_users_google_id"):
        op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)
    if not _has_index("users", "ix_users_active_verified"):
        op.create_index("ix_users_active_verified", "users", ["is_active", "is_verified"])

    # --- vehicle_types ---
    if not _has_column("vehicle_types", "slug"):
        op.add_column("vehicle_types", sa.Column("slug", sa.String(length=50), nullable=True))
        op.execute("UPDATE vehicle_types SET slug = lower(replace(name, ' ', '-')) WHERE slug IS NULL")
        op.alter_column("vehicle_types", "slug", nullable=False)
    if not _has_index("vehicle_types", "ix_vehicle_types_slug"):
        op.create_index("ix_vehicle_types_slug", "vehicle_types", ["slug"], unique=True)

    # --- wallets ---
    if not _has_column("wallets", "bonus_balance"):
        op.add_column("wallets", sa.Column("bonus_balance", sa.Float(), server_default="0", nullable=False))
    if not _has_column("wallets", "referral_balance"):
        op.add_column("wallets", sa.Column("referral_balance", sa.Float(), server_default="0", nullable=False))
    constraints = {c["name"] for c in _inspector().get_check_constraints("wallets")}
    if "ck_wallet_owner_xor" not in constraints:
        op.create_check_constraint(
            "ck_wallet_owner_xor",
            "wallets",
            "(user_id IS NOT NULL AND driver_id IS NULL) OR (user_id IS NULL AND driver_id IS NOT NULL)",
        )

    # --- ratings ---
    if not _has_column("ratings", "rater_type"):
        op.add_column("ratings", sa.Column("rater_type", sa.String(length=10), server_default="USER", nullable=False))

    if not _has_table("auth_devices"):
        op.create_table(
            "auth_devices",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("driver_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("device_name", sa.String(length=100), nullable=True),
            sa.Column("device_type", sa.String(length=20), server_default="ANDROID", nullable=False),
            sa.Column("fcm_token", sa.String(length=500), nullable=True),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_auth_devices_user_id", "auth_devices", ["user_id"])
        op.create_index("ix_auth_devices_driver_id", "auth_devices", ["driver_id"])

    if not _has_table("user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("driver_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("refresh_token_hash", sa.String(length=255), nullable=False),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["device_id"], ["auth_devices.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
        op.create_index("ix_user_sessions_driver_id", "user_sessions", ["driver_id"])
        op.create_index("ix_user_sessions_admin_user_id", "user_sessions", ["admin_user_id"])
        op.create_index("ix_user_sessions_refresh_token_hash", "user_sessions", ["refresh_token_hash"])
        op.create_index("ix_user_sessions_active_expires", "user_sessions", ["is_active", "expires_at"])

    if not _has_table("otp_logs"):
        op.create_table(
            "otp_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("phone", sa.String(length=20), nullable=False),
            sa.Column("otp_hash", sa.String(length=255), nullable=False),
            sa.Column("purpose", sa.String(length=30), nullable=False),
            sa.Column("is_verified", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_otp_logs_phone", "otp_logs", ["phone"])
        op.create_index("ix_otp_logs_phone_created", "otp_logs", ["phone", "created_at"])

    if not _has_table("driver_bank_accounts"):
        op.create_table(
            "driver_bank_accounts",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("driver_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("account_holder_name", sa.String(length=150), nullable=False),
            sa.Column("account_number_masked", sa.String(length=30), nullable=False),
            sa.Column("ifsc_code", sa.String(length=20), nullable=False),
            sa.Column("bank_name", sa.String(length=100), nullable=False),
            sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("is_verified", sa.Boolean(), server_default="false", nullable=False),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_driver_bank_accounts_driver_id", "driver_bank_accounts", ["driver_id"])
        op.create_index("ix_driver_bank_primary", "driver_bank_accounts", ["driver_id", "is_primary"])

    if not _has_table("ride_events"):
        op.create_table(
            "ride_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("ride_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("event_type", sa.String(length=30), nullable=False),
            sa.Column("actor_type", sa.String(length=20), server_default="SYSTEM", nullable=False),
            sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.ForeignKeyConstraint(["ride_id"], ["rides.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ride_events_ride_id", "ride_events", ["ride_id"])
        op.create_index("ix_ride_events_ride_type", "ride_events", ["ride_id", "event_type"])

    if not _has_table("withdrawal_requests"):
        op.create_table(
            "withdrawal_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("driver_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("bank_account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
            sa.Column("rejection_reason", sa.String(length=500), nullable=True),
            sa.Column("processed_by", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["bank_account_id"], ["driver_bank_accounts.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_withdrawal_requests_driver_id", "withdrawal_requests", ["driver_id"])
        op.create_index("ix_withdrawal_requests_wallet_id", "withdrawal_requests", ["wallet_id"])
        op.create_index("ix_withdrawal_requests_status", "withdrawal_requests", ["status"])
        op.create_index("ix_withdrawals_driver_status", "withdrawal_requests", ["driver_id", "status"])

    if not _has_table("referral_codes"):
        op.create_table(
            "referral_codes",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("code", sa.String(length=20), nullable=False),
            sa.Column("reward_amount", sa.Float(), server_default="0", nullable=False),
            sa.Column("uses_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("max_uses", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_referral_codes_user_id", "referral_codes", ["user_id"])
        op.create_index("ix_referral_codes_code", "referral_codes", ["code"])

    if not _has_table("support_ticket_replies"):
        op.create_table(
            "support_ticket_replies",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sender_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sender_type", sa.String(length=10), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_support_ticket_replies_ticket_id", "support_ticket_replies", ["ticket_id"])

    if not _has_table("faqs"):
        op.create_table(
            "faqs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("category", sa.String(length=50), nullable=False),
            sa.Column("question", sa.String(length=500), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_faqs_category_active", "faqs", ["category", "is_active"])

    index_ops = [
        ("drivers", "ix_drivers_status_active", ["status", "is_active"]),
        ("drivers", "ix_drivers_kyc_status", ["kyc_status"]),
        ("driver_documents", "ix_driver_documents_type_status", ["driver_id", "document_type", "status"]),
        ("saved_addresses", "ix_saved_addresses_user_default", ["user_id", "is_default"]),
        ("ride_tracking", "ix_ride_tracking_ride_created", ["ride_id", "created_at"]),
        ("wallet_transactions", "ix_wallet_tx_wallet_created", ["wallet_id", "created_at"]),
        ("payments", "ix_payments_status_created", ["status", "created_at"]),
        ("notifications", "ix_notifications_user_read", ["user_id", "is_read"]),
        ("support_tickets", "ix_support_tickets_status_priority", ["status", "priority"]),
    ]
    for table, index_name, columns in index_ops:
        if _has_table(table) and not _has_index(table, index_name):
            op.create_index(index_name, table, columns)


def downgrade() -> None:
    if _has_index("support_tickets", "ix_support_tickets_status_priority"):
        op.drop_index("ix_support_tickets_status_priority", table_name="support_tickets")
    if _has_index("notifications", "ix_notifications_user_read"):
        op.drop_index("ix_notifications_user_read", table_name="notifications")
    if _has_index("payments", "ix_payments_status_created"):
        op.drop_index("ix_payments_status_created", table_name="payments")
    if _has_index("wallet_transactions", "ix_wallet_tx_wallet_created"):
        op.drop_index("ix_wallet_tx_wallet_created", table_name="wallet_transactions")
    if _has_index("ride_tracking", "ix_ride_tracking_ride_created"):
        op.drop_index("ix_ride_tracking_ride_created", table_name="ride_tracking")
    if _has_index("saved_addresses", "ix_saved_addresses_user_default"):
        op.drop_index("ix_saved_addresses_user_default", table_name="saved_addresses")
    if _has_index("driver_documents", "ix_driver_documents_type_status"):
        op.drop_index("ix_driver_documents_type_status", table_name="driver_documents")
    if _has_index("drivers", "ix_drivers_kyc_status"):
        op.drop_index("ix_drivers_kyc_status", table_name="drivers")
    if _has_index("drivers", "ix_drivers_status_active"):
        op.drop_index("ix_drivers_status_active", table_name="drivers")

    for table in ("faqs", "support_ticket_replies", "referral_codes", "withdrawal_requests", "ride_events", "driver_bank_accounts", "otp_logs", "user_sessions", "auth_devices"):
        if _has_table(table):
            op.drop_table(table)

    if _has_column("ratings", "rater_type"):
        op.drop_column("ratings", "rater_type")
    constraints = {c["name"] for c in _inspector().get_check_constraints("wallets")} if _has_table("wallets") else set()
    if "ck_wallet_owner_xor" in constraints:
        op.drop_constraint("ck_wallet_owner_xor", "wallets", type_="check")
    if _has_column("wallets", "referral_balance"):
        op.drop_column("wallets", "referral_balance")
    if _has_column("wallets", "bonus_balance"):
        op.drop_column("wallets", "bonus_balance")
    if _has_index("vehicle_types", "ix_vehicle_types_slug"):
        op.drop_index("ix_vehicle_types_slug", table_name="vehicle_types")
    if _has_column("vehicle_types", "slug"):
        op.drop_column("vehicle_types", "slug")
    if _has_index("users", "ix_users_active_verified"):
        op.drop_index("ix_users_active_verified", table_name="users")
    if _has_index("users", "ix_users_google_id"):
        op.drop_index("ix_users_google_id", table_name="users")
    if _has_index("users", "ix_users_referral_code"):
        op.drop_index("ix_users_referral_code", table_name="users")
    if _has_column("users", "referred_by_id"):
        op.drop_constraint("fk_users_referred_by", "users", type_="foreignkey")
    if _has_column("users", "google_id"):
        op.drop_column("users", "google_id")
    if _has_column("users", "referred_by_id"):
        op.drop_column("users", "referred_by_id")
    if _has_column("users", "referral_code"):
        op.drop_column("users", "referral_code")
