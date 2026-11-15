"""offers: prices per variant with validity windows

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _now_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price_mmk", sa.BigInteger(), nullable=False),
        sa.Column("compare_at_price_mmk", sa.BigInteger(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(12), server_default=sa.text("'ACTIVE'"), nullable=False),
        _now_column("created_at"),
        _now_column("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offers")),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_offers_variant_id_product_variants"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("price_mmk > 0", name=op.f("ck_offers_price_positive")),
        sa.CheckConstraint(
            "compare_at_price_mmk IS NULL OR compare_at_price_mmk > price_mmk",
            name=op.f("ck_offers_compare_at_above_price"),
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at", name=op.f("ck_offers_window_valid")
        ),
        sa.CheckConstraint("status IN ('ACTIVE','CANCELLED')", name=op.f("ck_offers_status_valid")),
    )
    op.create_index(
        op.f("ix_offers_variant_window"), "offers", ["variant_id", "starts_at", "ends_at"]
    )


def downgrade() -> None:
    op.drop_table("offers")
