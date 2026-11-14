"""catalog: categories, products, variants, images

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _now_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [_now_column("created_at"), _now_column("updated_at")]


def upgrade() -> None:
    op.execute("CREATE SEQUENCE product_code_seq START WITH 1 INCREMENT BY 1")

    op.create_table(
        "product_categories",
        _id(),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_categories")),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["product_categories.id"],
            name=op.f("fk_product_categories_parent_id_product_categories"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("slug", name=op.f("uq_product_categories_slug")),
    )
    op.create_index(op.f("ix_product_categories_parent_id"), "product_categories", ["parent_id"])

    op.create_table(
        "products",
        _id(),
        sa.Column("public_code", sa.String(20), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("status", sa.String(12), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("fulfilment_mode", sa.String(10), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["product_categories.id"],
            name=op.f("fk_products_category_id_product_categories"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_code", name=op.f("uq_products_public_code")),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACTIVE','ARCHIVED')", name=op.f("ck_products_status_valid")
        ),
        sa.CheckConstraint(
            "fulfilment_mode IN ('STOCK','PREORDER')",
            name=op.f("ck_products_fulfilment_mode_valid"),
        ),
    )
    op.create_index(op.f("ix_products_category_id"), "products", ["category_id"])
    op.create_index(op.f("ix_products_status"), "products", ["status"])

    op.create_table(
        "product_variants",
        _id(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("size", sa.String(30), nullable=True),
        sa.Column("color", sa.String(30), nullable=True),
        sa.Column(
            "extra", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_variants")),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_variants_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("sku", name=op.f("uq_product_variants_sku")),
        sa.UniqueConstraint(
            "product_id", "id", name=op.f("uq_product_variants_product_id_id")
        ),
    )
    op.create_index(op.f("ix_product_variants_product_id"), "product_variants", ["product_id"])
    # Same product cannot have two variants with the same size+color (NULLs count as empty).
    op.create_index(
        op.f("uq_product_variants_combo"),
        "product_variants",
        ["product_id", sa.text("coalesce(size, '')"), sa.text("coalesce(color, '')")],
        unique=True,
    )

    op.create_table(
        "product_images",
        _id(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("telegram_file_id", sa.String(200), nullable=True),
        sa.Column("alt_text", sa.String(200), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _now_column("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_images")),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_images_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "variant_id"],
            ["product_variants.product_id", "product_variants.id"],
            name=op.f("fk_product_images_variant_same_product"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "url IS NOT NULL OR telegram_file_id IS NOT NULL",
            name=op.f("ck_product_images_source_present"),
        ),
    )
    op.create_index(op.f("ix_product_images_product_id"), "product_images", ["product_id"])
    op.create_index(op.f("ix_product_images_variant_id"), "product_images", ["variant_id"])


def downgrade() -> None:
    op.drop_table("product_images")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("product_categories")
    op.execute("DROP SEQUENCE product_code_seq")
