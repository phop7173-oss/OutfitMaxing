"""Preorder sourcing. Kept completely separate from physical stock (inventory_* tables)."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.modules.products.models import ProductVariant


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


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="status_valid"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    contact_note: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(10), default="ACTIVE", server_default=text("'ACTIVE'")
    )


class SupplierProduct(TimestampMixin, Base):
    """A variant a supplier can source, with the preorder capacity the owner is willing to sell.

    available preorder capacity = capacity - committed."""

    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "variant_id", name="uq_supplier_products_supplier_variant"),
        CheckConstraint("lead_days_min >= 0", name="lead_min_non_negative"),
        CheckConstraint("lead_days_max >= lead_days_min", name="lead_max_not_below_min"),
        CheckConstraint("capacity >= 0", name="capacity_non_negative"),
        CheckConstraint("committed >= 0", name="committed_non_negative"),
        CheckConstraint("committed <= capacity", name="committed_within_capacity"),
        CheckConstraint("unit_cost_mmk IS NULL OR unit_cost_mmk >= 0", name="unit_cost_valid"),
        CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="status_valid"),
        Index("ix_supplier_products_variant_status", "variant_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT")
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="RESTRICT")
    )
    supplier_sku: Mapped[str | None] = mapped_column(String(64))
    unit_cost_mmk: Mapped[int | None] = mapped_column(BigInteger)  # private: admin only
    lead_days_min: Mapped[int] = mapped_column(Integer)
    lead_days_max: Mapped[int] = mapped_column(Integer)
    capacity: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    committed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String(10), default="ACTIVE", server_default=text("'ACTIVE'")
    )

    supplier: Mapped[Supplier] = relationship()
    variant: Mapped[ProductVariant] = relationship()


class SupplierStockCheck(Base):
    """What the supplier reported as available, at a point in time (informational record)."""

    __tablename__ = "supplier_stock_checks"
    __table_args__ = (CheckConstraint("available_quantity >= 0", name="quantity_non_negative"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    supplier_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("supplier_products.id", ondelete="RESTRICT"),
        index=True,
    )
    available_quantity: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(200))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


class SourcingRequest(TimestampMixin, Base):
    __tablename__ = "sourcing_requests"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "status IN ('REQUESTED','CONFIRMED','SHIPPED','RECEIVED','FAILED','CANCELLED')",
            name="status_valid",
        ),
        CheckConstraint(
            "expected_from IS NULL OR expected_to >= expected_from", name="expected_window_valid"
        ),
        CheckConstraint("surplus_remaining >= 0", name="surplus_non_negative"),
        Index("ix_sourcing_requests_status_requested", "status", "requested_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    supplier_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("supplier_products.id", ondelete="RESTRICT"),
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(10), default="REQUESTED", server_default=text("'REQUESTED'")
    )
    note: Mapped[str | None] = mapped_column(String(200))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_from: Mapped[date | None] = mapped_column(Date)
    expected_to: Mapped[date | None] = mapped_column(Date)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_quantity: Mapped[int | None] = mapped_column(Integer)
    allocated_quantity: Mapped[int | None] = mapped_column(Integer)
    surplus_remaining: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    supplier_product: Mapped[SupplierProduct] = relationship()


class PreorderCommitment(TimestampMixin, Base):
    """Units of preorder capacity promised to one customer order line.

    HELD -> FULFILLED (supplier goods allocated to this customer) or RELEASED (cancelled)."""

    __tablename__ = "preorder_commitments"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("status IN ('HELD','FULFILLED','RELEASED')", name="status_valid"),
        CheckConstraint("expected_to >= expected_from", name="expected_window_valid"),
        Index("ix_preorder_commitments_sp_status", "supplier_product_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="RESTRICT")
    )
    supplier_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("supplier_products.id", ondelete="RESTRICT")
    )
    sourcing_request_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sourcing_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(10), default="HELD", server_default=text("'HELD'")
    )
    deposit_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    deposit_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    needs_review: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    expected_from: Mapped[date] = mapped_column(Date)
    expected_to: Mapped[date] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    variant: Mapped[ProductVariant] = relationship()
