"""preorders: suppliers, capacity, commitments, sourcing requests, outbox events

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
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


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _fk(column: str, target: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(name), ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "suppliers",
        _id(),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("contact_note", sa.String(200), nullable=True),
        sa.Column("status", sa.String(10), server_default=sa.text("'ACTIVE'"), nullable=False),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("code", name=op.f("uq_suppliers_code")),
        sa.CheckConstraint(
            "status IN ('ACTIVE','INACTIVE')", name=op.f("ck_suppliers_status_valid")
        ),
    )

    op.create_table(
        "supplier_products",
        _id(),
        _uuid("supplier_id"),
        _uuid("variant_id"),
        sa.Column("supplier_sku", sa.String(64), nullable=True),
        sa.Column("unit_cost_mmk", sa.BigInteger(), nullable=True),
        sa.Column("lead_days_min", sa.Integer(), nullable=False),
        sa.Column("lead_days_max", sa.Integer(), nullable=False),
        _int("capacity"),
        _int("committed"),
        sa.Column("status", sa.String(10), server_default=sa.text("'ACTIVE'"), nullable=False),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_products")),
        _fk("supplier_id", "suppliers.id", "fk_supplier_products_supplier_id_suppliers"),
        _fk(
            "variant_id",
            "product_variants.id",
            "fk_supplier_products_variant_id_product_variants",
        ),
        sa.UniqueConstraint(
            "supplier_id", "variant_id", name=op.f("uq_supplier_products_supplier_variant")
        ),
        sa.CheckConstraint(
            "lead_days_min >= 0", name=op.f("ck_supplier_products_lead_min_non_negative")
        ),
        sa.CheckConstraint(
            "lead_days_max >= lead_days_min",
            name=op.f("ck_supplier_products_lead_max_not_below_min"),
        ),
        sa.CheckConstraint(
            "capacity >= 0", name=op.f("ck_supplier_products_capacity_non_negative")
        ),
        sa.CheckConstraint(
            "committed >= 0", name=op.f("ck_supplier_products_committed_non_negative")
        ),
        sa.CheckConstraint(
            "committed <= capacity", name=op.f("ck_supplier_products_committed_within_capacity")
        ),
        sa.CheckConstraint(
            "unit_cost_mmk IS NULL OR unit_cost_mmk >= 0",
            name=op.f("ck_supplier_products_unit_cost_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','INACTIVE')", name=op.f("ck_supplier_products_status_valid")
        ),
    )
    op.create_index(
        op.f("ix_supplier_products_variant_status"), "supplier_products", ["variant_id", "status"]
    )

    op.create_table(
        "supplier_stock_checks",
        _id(),
        _uuid("supplier_product_id"),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        _now_column("checked_at", "clock_timestamp()"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_stock_checks")),
        _fk(
            "supplier_product_id",
            "supplier_products.id",
            "fk_supplier_stock_checks_supplier_product_id_supplier_products",
        ),
        sa.CheckConstraint(
            "available_quantity >= 0", name=op.f("ck_supplier_stock_checks_quantity_non_negative")
        ),
    )
    op.create_index(
        op.f("ix_supplier_stock_checks_supplier_product_id"),
        "supplier_stock_checks",
        ["supplier_product_id"],
    )

    op.create_table(
        "sourcing_requests",
        _id(),
        _uuid("supplier_product_id"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), server_default=sa.text("'REQUESTED'"), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        _ts("confirmed_at"),
        sa.Column("expected_from", sa.Date(), nullable=True),
        sa.Column("expected_to", sa.Date(), nullable=True),
        _ts("shipped_at"),
        _ts("received_at"),
        sa.Column("received_quantity", sa.Integer(), nullable=True),
        sa.Column("allocated_quantity", sa.Integer(), nullable=True),
        _int("surplus_remaining"),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sourcing_requests")),
        _fk(
            "supplier_product_id",
            "supplier_products.id",
            "fk_sourcing_requests_supplier_product_id_supplier_products",
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_sourcing_requests_quantity_positive")),
        sa.CheckConstraint(
            "status IN ('REQUESTED','CONFIRMED','SHIPPED','RECEIVED','FAILED','CANCELLED')",
            name=op.f("ck_sourcing_requests_status_valid"),
        ),
        sa.CheckConstraint(
            "expected_from IS NULL OR expected_to >= expected_from",
            name=op.f("ck_sourcing_requests_expected_window_valid"),
        ),
        sa.CheckConstraint(
            "surplus_remaining >= 0", name=op.f("ck_sourcing_requests_surplus_non_negative")
        ),
    )
    op.create_index(
        op.f("ix_sourcing_requests_supplier_product_id"),
        "sourcing_requests",
        ["supplier_product_id"],
    )
    op.create_index(
        op.f("ix_sourcing_requests_status_requested"),
        "sourcing_requests",
        ["status", "requested_at"],
    )

    op.create_table(
        "preorder_commitments",
        _id(),
        _uuid("variant_id"),
        _uuid("supplier_product_id"),
        _uuid("sourcing_request_id", nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), server_default=sa.text("'HELD'"), nullable=False),
        sa.Column(
            "deposit_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        _ts("deposit_verified_at"),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expected_from", sa.Date(), nullable=False),
        sa.Column("expected_to", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        sa.Column("reference_type", sa.String(30), nullable=True),
        sa.Column("reference_id", sa.String(64), nullable=True),
        _ts("fulfilled_at"),
        _ts("released_at"),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_preorder_commitments")),
        _fk(
            "variant_id",
            "product_variants.id",
            "fk_preorder_commitments_variant_id_product_variants",
        ),
        _fk(
            "supplier_product_id",
            "supplier_products.id",
            "fk_preorder_commitments_supplier_product_id_supplier_products",
        ),
        _fk(
            "sourcing_request_id",
            "sourcing_requests.id",
            "fk_preorder_commitments_sourcing_request_id_sourcing_requests",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_preorder_commitments_idempotency_key")
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_preorder_commitments_quantity_positive")),
        sa.CheckConstraint(
            "status IN ('HELD','FULFILLED','RELEASED')",
            name=op.f("ck_preorder_commitments_status_valid"),
        ),
        sa.CheckConstraint(
            "expected_to >= expected_from",
            name=op.f("ck_preorder_commitments_expected_window_valid"),
        ),
    )
    op.create_index(
        op.f("ix_preorder_commitments_sourcing_request_id"),
        "preorder_commitments",
        ["sourcing_request_id"],
    )
    op.create_index(
        op.f("ix_preorder_commitments_sp_status"),
        "preorder_commitments",
        ["supplier_product_id", "status"],
    )

    op.create_table(
        "outbox_events",
        _id(),
        sa.Column("topic", sa.String(60), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(10), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("dedupe_key", sa.String(120), nullable=True),
        _int("attempts"),
        _now_column("next_attempt_at"),
        _now_column("created_at"),
        _ts("sent_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_outbox_events_dedupe_key")),
        sa.CheckConstraint(
            "status IN ('PENDING','SENT','FAILED')", name=op.f("ck_outbox_events_status_valid")
        ),
    )
    op.create_index(
        op.f("ix_outbox_events_status_next_attempt"),
        "outbox_events",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("preorder_commitments")
    op.drop_table("sourcing_requests")
    op.drop_table("supplier_stock_checks")
    op.drop_table("supplier_products")
    op.drop_table("suppliers")
