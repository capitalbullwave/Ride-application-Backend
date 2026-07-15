"""Remove legacy XL / XL-Cab vehicle categories from the database."""
revision = "023_remove_xl"
down_revision = "022_bwr_public_ids"
branch_labels = None
depends_on = None

from alembic import op
from sqlalchemy import text


def upgrade() -> None:
    bind = op.get_bind()
    # Deactivate first so user apps stop showing XL even if hard-delete is blocked by FKs.
    bind.execute(
        text(
            """
            UPDATE vehicle_types
            SET is_active = false
            WHERE slug IN ('xl', 'xl-cab')
               OR lower(trim(name)) IN ('xl', 'xl-cab', 'xl cab')
            """
        )
    )
    # Permanently remove only when nothing references the category.
    bind.execute(
        text(
            """
            DELETE FROM vehicle_types AS vt
            WHERE (vt.slug IN ('xl', 'xl-cab')
                   OR lower(trim(vt.name)) IN ('xl', 'xl-cab', 'xl cab'))
              AND NOT EXISTS (
                    SELECT 1 FROM rides r WHERE r.vehicle_type_id = vt.id
              )
              AND NOT EXISTS (
                    SELECT 1 FROM vehicles v WHERE v.vehicle_type_id = vt.id
              )
            """
        )
    )


def downgrade() -> None:
    # XL categories are not re-created; admins manage vehicles from the panel.
    pass
