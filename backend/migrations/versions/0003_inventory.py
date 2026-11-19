"""inventory: locations, balances, reservations, movements (+ the default location)

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def _now_column(name: str, default: str = "now()") -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text(default), nullable=False
    )


def _int(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), server_default=sa.text("0"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "inventory_locations",
        _id(),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_locations")),
        sa.UniqueConstraint("code", name=op.f("uq_inventory_locations_code")),
    )
    op.execute("INSERT INTO inventory_locations (code, name) VALUES ('main', 'Main')")

    op.create_table(
        "inventory_balances",
        _id(),
        _uuid("variant_id"),
        _uuid("location_id"),
        _int("on_hand"),
        _int("reserved"),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_balances")),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_inventory_balances_variant_id_product_variants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["inventory_locations.id"],
            name=op.f("fk_inventory_balances_location_id_inventory_locations"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "variant_id", "location_id", name=op.f("uq_inventory_balances_variant_location")
        ),
        sa.CheckConstraint("on_hand >= 0", name=op.f("ck_inventory_balances_on_hand_non_negative")),
        sa.CheckConstraint(
            "reserved >= 0", name=op.f("ck_inventory_balances_reserved_non_negative")
        ),
        sa.CheckConstraint(
            "reserved <= on_hand", name=op.f("ck_inventory_balances_reserved_within_on_hand")
        ),
    )

    op.create_table(
        "inventory_reservations",
        _id(),
        _uuid("variant_id"),
        _uuid("location_id"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        sa.Column("reference_type", sa.String(30), nullable=True),
        sa.Column("reference_id", sa.String(64), nullable=True),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_reservations")),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_inventory_reservations_variant_id_product_variants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["inventory_locations.id"],
            name=op.f("fk_inventory_reservations_location_id_inventory_locations"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_inventory_reservations_idempotency_key")
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_inventory_reservations_quantity_positive")
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','COMMITTED','RELEASED','EXPIRED')",
            name=op.f("ck_inventory_reservations_status_valid"),
        ),
    )
    op.create_index(
        op.f("ix_inventory_reservations_status_expires"),
        "inventory_reservations",
        ["status", "expires_at"],
    )
    op.create_index(
        op.f("ix_inventory_reservations_variant_status"),
        "inventory_reservations",
        ["variant_id", "status"],
    )

    op.create_table(
        "inventory_movements",
        _id(),
        _uuid("variant_id"),
        _uuid("location_id"),
        _uuid("reservation_id", nullable=True),
        _int("delta_on_hand"),
        _int("delta_reserved"),
        sa.Column("reason", sa.String(12), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("actor", sa.String(60), nullable=True),
        _now_column("created_at", "clock_timestamp()"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_movements")),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_inventory_movements_variant_id_product_variants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["inventory_locations.id"],
            name=op.f("fk_inventory_movements_location_id_inventory_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["inventory_reservations.id"],
            name=op.f("fk_inventory_movements_reservation_id_inventory_reservations"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "delta_on_hand <> 0 OR delta_reserved <> 0",
            name=op.f("ck_inventory_movements_has_change"),
        ),
        sa.CheckConstraint(
            "reason IN ('RECEIVE','ADJUST','RESERVE','RELEASE','EXPIRE','COMMIT')",
            name=op.f("ck_inventory_movements_reason_valid"),
        ),
    )
    op.create_index(
        op.f("ix_inventory_movements_variant_created"),
        "inventory_movements",
        ["variant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("inventory_movements")
    op.drop_table("inventory_reservations")
    op.drop_table("inventory_balances")
    op.drop_table("inventory_locations")
