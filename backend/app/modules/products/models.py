"""Catalog tables: categories, products, variants, images. No prices or stock yet (task 2)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

# Readable public product codes: OM-P-000123
product_code_seq = Sequence("product_code_seq", metadata=Base.metadata)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProductCategory(TimestampMixin, Base):
    __tablename__ = "product_categories"

    id: Mapped[uuid.UUID] = _uuid_pk()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_categories.id", ondelete="RESTRICT"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    parent: Mapped["ProductCategory | None"] = relationship(
        "ProductCategory", remote_side="ProductCategory.id"
    )


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','ACTIVE','ARCHIVED')", name="status_valid"),
        CheckConstraint("fulfilment_mode IN ('STOCK','PREORDER')", name="fulfilment_mode_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    public_code: Mapped[str] = mapped_column(String(20), unique=True)
    category_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_categories.id", ondelete="RESTRICT"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(12), default="DRAFT", server_default=text("'DRAFT'"), index=True
    )
    fulfilment_mode: Mapped[str] = mapped_column(String(10))
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    category: Mapped[ProductCategory] = relationship()
    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", order_by="ProductVariant.sku"
    )
    images: Mapped[list["ProductImage"]] = relationship(
        order_by="ProductImage.sort_order, ProductImage.created_at"
    )


class ProductVariant(TimestampMixin, Base):
    # The "same size+color twice" rule is a functional unique index created in the migration
    # (uq_product_variants_combo: product_id, coalesce(size,''), coalesce(color,'')).
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("product_id", "id", name="uq_product_variants_product_id_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    size: Mapped[str | None] = mapped_column(String(30))
    color: Mapped[str | None] = mapped_column(String(30))
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    product: Mapped[Product] = relationship(back_populates="variants")


class ProductImage(Base):
    __tablename__ = "product_images"
    __table_args__ = (
        # A variant-specific image must belong to the same product (enforced by the database).
        ForeignKeyConstraint(
            ["product_id", "variant_id"],
            ["product_variants.product_id", "product_variants.id"],
            name="fk_product_images_variant_same_product",
            ondelete="RESTRICT",
        ),
        CheckConstraint("url IS NOT NULL OR telegram_file_id IS NOT NULL", name="source_present"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    telegram_file_id: Mapped[str | None] = mapped_column(String(200))
    alt_text: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    variant: Mapped[ProductVariant | None] = relationship(viewonly=True)
