"""Physical stock: locations, balances, reservations and an append-only movement log."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


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


class InventoryLocation(TimestampMixin, Base):
    __tablename__ = "inventory_locations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class InventoryBalance(TimestampMixin, Base):
    """Stock of one variant at one location. available = on_hand - reserved."""

    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint(
            "variant_id", "location_id", name="uq_inventory_balances_variant_location"
        ),
        CheckConstraint("on_hand >= 0", name="on_hand_non_negative"),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
        CheckConstraint("reserved <= on_hand", name="reserved_within_on_hand"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="RESTRICT")
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="RESTRICT")
    )
    on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    reserved: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class InventoryReservation(TimestampMixin, Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "status IN ('ACTIVE','COMMITTED','RELEASED','EXPIRED')", name="status_valid"
        ),
        Index("ix_inventory_reservations_status_expires", "status", "expires_at"),
        Index("ix_inventory_reservations_variant_status", "variant_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="RESTRICT")
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="RESTRICT")
    )
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(12), default="ACTIVE", server_default=text("'ACTIVE'")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    reference_type: Mapped[str | None] = mapped_column(String(30))
    reference_id: Mapped[str | None] = mapped_column(String(64))


class InventoryMovement(Base):
    """Append-only audit log: every change to on_hand or reserved writes one row."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint("delta_on_hand <> 0 OR delta_reserved <> 0", name="has_change"),
        CheckConstraint(
            "reason IN ('RECEIVE','ADJUST','RESERVE','RELEASE','EXPIRE','COMMIT')",
            name="reason_valid",
        ),
        Index("ix_inventory_movements_variant_created", "variant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="RESTRICT")
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventory_locations.id", ondelete="RESTRICT")
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventory_reservations.id", ondelete="RESTRICT")
    )
    delta_on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    delta_reserved: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    reason: Mapped[str] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(String(200))
    actor: Mapped[str | None] = mapped_column(String(60))
    # clock_timestamp() (not now()) so rows written in one transaction still order correctly.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )
