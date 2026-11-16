"""Inventory service: atomic reservations, stock changes, expiry. Owns its transactions.

Invariants (also enforced by CHECK constraints): on_hand >= 0, reserved >= 0,
reserved <= on_hand. available = on_hand - reserved. Every change to on_hand or reserved
writes one inventory_movements row in the same transaction.

Locking order (to avoid deadlocks): reservation rows first, then the balance row."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictError,
    InsufficientStockError,
    NotFoundError,
    UnprocessableError,
)
from app.modules.inventory.models import (
    InventoryBalance,
    InventoryLocation,
    InventoryMovement,
    InventoryReservation,
)
from app.modules.products.models import Product, ProductVariant

# Public display only (no exact quantities): "low stock" at or below this many units.
LOW_STOCK_THRESHOLD = 3


def _now() -> datetime:
    return datetime.now(UTC)


# ---- lookups --------------------------------------------------------------------------------
def get_variant_by_sku(session: Session, sku: str) -> ProductVariant:
    variant = session.execute(
        select(ProductVariant).where(ProductVariant.sku == sku)
    ).scalar_one_or_none()
    if variant is None:
        raise NotFoundError("Variant not found")
    return variant


def get_location(session: Session, code: str) -> InventoryLocation:
    location = session.execute(
        select(InventoryLocation).where(
            InventoryLocation.code == code, InventoryLocation.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if location is None:
        raise NotFoundError("Stock location not found")
    return location


# ---- manual stock changes -------------------------------------------------------------------
def _change_on_hand(
    session: Session,
    variant: ProductVariant,
    location: InventoryLocation,
    delta: int,
    *,
    reason: str,
    note: str | None,
    actor: str,
) -> None:
    if delta > 0:
        stmt = pg_insert(InventoryBalance).values(
            variant_id=variant.id, location_id=location.id, on_hand=delta, reserved=0
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["variant_id", "location_id"],
                set_={"on_hand": InventoryBalance.on_hand + delta, "updated_at": func.now()},
            )
        )
    else:
        result = session.execute(
            update(InventoryBalance)
            .where(
                InventoryBalance.variant_id == variant.id,
                InventoryBalance.location_id == location.id,
                InventoryBalance.on_hand + delta >= InventoryBalance.reserved,
            )
            .values(on_hand=InventoryBalance.on_hand + delta)
        )
        if result.rowcount != 1:
            session.rollback()
            raise UnprocessableError(
                "That reduction would take stock below the amount reserved (or below zero)"
            )
    session.add(
        InventoryMovement(
            variant_id=variant.id,
            location_id=location.id,
            delta_on_hand=delta,
            reason=reason,
            note=note,
            actor=actor,
        )
    )
    session.commit()


def receive_stock(
    session: Session,
    sku: str,
    quantity: int,
    *,
    location_code: str = "main",
    note: str | None = None,
    actor: str = "admin",
) -> None:
    if quantity < 1:
        raise UnprocessableError("quantity must be at least 1")
    variant = get_variant_by_sku(session, sku)
    location = get_location(session, location_code)
    _change_on_hand(session, variant, location, quantity, reason="RECEIVE", note=note, actor=actor)


def adjust_stock(
    session: Session,
    sku: str,
    delta: int,
    *,
    note: str,
    location_code: str = "main",
    actor: str = "admin",
) -> None:
    if delta == 0:
        raise UnprocessableError("delta must not be zero")
    variant = get_variant_by_sku(session, sku)
    location = get_location(session, location_code)
    _change_on_hand(session, variant, location, delta, reason="ADJUST", note=note, actor=actor)


# ---- reading stock --------------------------------------------------------------------------
def stock_levels(session: Session, sku: str) -> tuple[ProductVariant, list[dict]]:
    variant = get_variant_by_sku(session, sku)
    stmt = (
        select(
            InventoryLocation.code,
            InventoryLocation.name,
            InventoryBalance.on_hand,
            InventoryBalance.reserved,
        )
        .select_from(InventoryLocation)
        .outerjoin(
            InventoryBalance,
            and_(
                InventoryBalance.location_id == InventoryLocation.id,
                InventoryBalance.variant_id == variant.id,
            ),
        )
        .where(InventoryLocation.is_active.is_(True))
        .order_by(InventoryLocation.code)
    )
    levels = []
    for code, name, on_hand, reserved in session.execute(stmt):
        on_hand, reserved = on_hand or 0, reserved or 0
        levels.append(
            {
                "location_code": code,
                "location_name": name,
                "on_hand": on_hand,
                "reserved": reserved,
                "available": on_hand - reserved,
            }
        )
    return variant, levels


def list_movements(
    session: Session, sku: str, limit: int = 50
) -> list[tuple[InventoryMovement, str]]:
    variant = get_variant_by_sku(session, sku)
    stmt = (
        select(InventoryMovement, InventoryLocation.code)
        .join(InventoryLocation, InventoryLocation.id == InventoryMovement.location_id)
        .where(InventoryMovement.variant_id == variant.id)
        .order_by(InventoryMovement.created_at.desc())
        .limit(limit)
    )
    return [(m, code) for m, code in session.execute(stmt)]


def available_quantities(
    session: Session, variant_ids: list[uuid.UUID], now: datetime | None = None
) -> dict[uuid.UUID, int]:
    """Sellable units per variant right now: on_hand minus unexpired active holds, never below 0.

    Uses the reservations table (not balances.reserved) so a hold that has expired but has not
    been swept yet no longer reduces what is shown."""
    if not variant_ids:
        return {}
    now = now or _now()
    on_hand = dict(
        session.execute(
            select(InventoryBalance.variant_id, func.sum(InventoryBalance.on_hand))
            .join(InventoryLocation, InventoryLocation.id == InventoryBalance.location_id)
            .where(
                InventoryBalance.variant_id.in_(variant_ids),
                InventoryLocation.is_active.is_(True),
            )
            .group_by(InventoryBalance.variant_id)
        ).all()
    )
    held = dict(
        session.execute(
            select(InventoryReservation.variant_id, func.sum(InventoryReservation.quantity))
            .join(InventoryLocation, InventoryLocation.id == InventoryReservation.location_id)
            .where(
                InventoryReservation.variant_id.in_(variant_ids),
                InventoryReservation.status == "ACTIVE",
                InventoryReservation.expires_at > now,
                InventoryLocation.is_active.is_(True),
            )
            .group_by(InventoryReservation.variant_id)
        ).all()
    )
    return {vid: max(int(on_hand.get(vid, 0)) - int(held.get(vid, 0)), 0) for vid in variant_ids}


def availability_label(fulfilment_mode: str, available: int) -> str:
    """Customer-facing stock state. Preorder items are never shown as physically in stock."""
    if fulfilment_mode == "PREORDER":
        return "preorder"
    if available <= 0:
        return "out_of_stock"
    if available <= LOW_STOCK_THRESHOLD:
        return "low_stock"
    return "in_stock"


# ---- reservations ---------------------------------------------------------------------------
def _add_movement(
    session: Session,
    reservation: InventoryReservation,
    *,
    reason: str,
    delta_on_hand: int = 0,
    delta_reserved: int = 0,
) -> None:
    session.add(
        InventoryMovement(
            variant_id=reservation.variant_id,
            location_id=reservation.location_id,
            reservation_id=reservation.id,
            delta_on_hand=delta_on_hand,
            delta_reserved=delta_reserved,
            reason=reason,
        )
    )


def _release_hold(
    session: Session, reservation: InventoryReservation, new_status: str, reason: str, now: datetime
) -> None:
    result = session.execute(
        update(InventoryBalance)
        .where(
            InventoryBalance.variant_id == reservation.variant_id,
            InventoryBalance.location_id == reservation.location_id,
            InventoryBalance.reserved >= reservation.quantity,
        )
        .values(reserved=InventoryBalance.reserved - reservation.quantity)
    )
    if result.rowcount != 1:
        raise RuntimeError("inventory invariant violated: hold without matching reserved stock")
    reservation.status = new_status
    reservation.resolved_at = now
    _add_movement(session, reservation, reason=reason, delta_reserved=-reservation.quantity)


def _expire_due(session: Session, *, now: datetime, variant_id: uuid.UUID | None = None) -> int:
    stmt = (
        select(InventoryReservation)
        .where(InventoryReservation.status == "ACTIVE", InventoryReservation.expires_at <= now)
        .order_by(InventoryReservation.id)
        .limit(1000)
        .with_for_update(skip_locked=True)
    )
    if variant_id is not None:
        stmt = stmt.where(InventoryReservation.variant_id == variant_id)
    due = list(session.execute(stmt).scalars())
    for reservation in due:
        _release_hold(session, reservation, "EXPIRED", "EXPIRE", now)
    return len(due)


def expire_due_reservations(session: Session, *, now: datetime | None = None) -> int:
    """Release every expired hold (for a periodic job). Returns how many were released."""
    count = _expire_due(session, now=now or _now())
    session.commit()
    return count


def _existing_by_key(session: Session, key: str) -> InventoryReservation | None:
    return session.execute(
        select(InventoryReservation).where(InventoryReservation.idempotency_key == key)
    ).scalar_one_or_none()


def _same_request(
    existing: InventoryReservation, variant_id: uuid.UUID, quantity: int
) -> InventoryReservation:
    if existing.variant_id != variant_id or existing.quantity != quantity:
        raise ConflictError("That idempotency key was already used for a different reservation")
    return existing


def _sellable_variant(session: Session, variant_id: uuid.UUID) -> None:
    variant = session.get(ProductVariant, variant_id)
    if variant is None:
        raise NotFoundError("Variant not found")
    product = session.get(Product, variant.product_id)
    if product is None or product.status != "ACTIVE" or not variant.is_active:
        raise UnprocessableError("This variant is not on sale")
    if product.fulfilment_mode != "STOCK":
        raise UnprocessableError("Preorder products cannot be reserved from physical stock")


def reserve_stock(
    session: Session,
    variant_id: uuid.UUID,
    quantity: int,
    *,
    ttl_minutes: int,
    location_code: str = "main",
    idempotency_key: str | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
    now: datetime | None = None,
) -> InventoryReservation:
    """Hold `quantity` units for `ttl_minutes`. Atomic: the check and the hold are one UPDATE,
    so two buyers can never both take the last unit."""
    if quantity < 1:
        raise UnprocessableError("quantity must be at least 1")
    now = now or _now()
    if idempotency_key:
        existing = _existing_by_key(session, idempotency_key)
        if existing is not None:
            return _same_request(existing, variant_id, quantity)
    _sellable_variant(session, variant_id)
    location = get_location(session, location_code)
    _expire_due(session, now=now, variant_id=variant_id)  # frees holds whose time is up
    result = session.execute(
        update(InventoryBalance)
        .where(
            InventoryBalance.variant_id == variant_id,
            InventoryBalance.location_id == location.id,
            InventoryBalance.on_hand - InventoryBalance.reserved >= quantity,
        )
        .values(reserved=InventoryBalance.reserved + quantity)
    )
    if result.rowcount != 1:
        session.rollback()
        raise InsufficientStockError("Not enough stock available")
    reservation = InventoryReservation(
        variant_id=variant_id,
        location_id=location.id,
        quantity=quantity,
        status="ACTIVE",
        expires_at=now + timedelta(minutes=ttl_minutes),
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    session.add(reservation)
    session.flush()
    _add_movement(session, reservation, reason="RESERVE", delta_reserved=quantity)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if idempotency_key:
            existing = _existing_by_key(session, idempotency_key)
            if existing is not None:  # a concurrent request with the same key won
                return _same_request(existing, variant_id, quantity)
        raise ConflictError("Reservation conflicts with an existing record") from exc
    return reservation


def _locked_reservation(session: Session, reservation_id: uuid.UUID) -> InventoryReservation:
    reservation = session.execute(
        select(InventoryReservation)
        .where(InventoryReservation.id == reservation_id)
        .with_for_update()
    ).scalar_one_or_none()
    if reservation is None:
        raise NotFoundError("Reservation not found")
    return reservation


def release_reservation(
    session: Session, reservation_id: uuid.UUID, *, now: datetime | None = None
) -> InventoryReservation:
    """Give the held stock back. Idempotent for already released or expired reservations."""
    now = now or _now()
    reservation = _locked_reservation(session, reservation_id)
    if reservation.status == "COMMITTED":
        session.rollback()
        raise UnprocessableError("Reservation is already committed")
    if reservation.status == "ACTIVE":
        _release_hold(session, reservation, "RELEASED", "RELEASE", now)
        session.commit()
    else:
        session.rollback()
    return reservation


def commit_reservation(
    session: Session, reservation_id: uuid.UUID, *, now: datetime | None = None
) -> InventoryReservation:
    """Turn a hold into a real deduction, exactly once. Repeating the call changes nothing."""
    now = now or _now()
    reservation = _locked_reservation(session, reservation_id)
    if reservation.status == "COMMITTED":
        session.rollback()
        return reservation
    if reservation.status != "ACTIVE":
        session.rollback()
        raise ConflictError("Reservation is no longer active; reserve again")
    if reservation.expires_at <= now:
        _release_hold(session, reservation, "EXPIRED", "EXPIRE", now)
        session.commit()
        raise ConflictError("Reservation expired; reserve again")
    result = session.execute(
        update(InventoryBalance)
        .where(
            InventoryBalance.variant_id == reservation.variant_id,
            InventoryBalance.location_id == reservation.location_id,
            InventoryBalance.reserved >= reservation.quantity,
            InventoryBalance.on_hand >= reservation.quantity,
        )
        .values(
            on_hand=InventoryBalance.on_hand - reservation.quantity,
            reserved=InventoryBalance.reserved - reservation.quantity,
        )
    )
    if result.rowcount != 1:
        raise RuntimeError("inventory invariant violated: commit without matching stock")
    reservation.status = "COMMITTED"
    reservation.resolved_at = now
    _add_movement(
        session,
        reservation,
        reason="COMMIT",
        delta_on_hand=-reservation.quantity,
        delta_reserved=-reservation.quantity,
    )
    session.commit()
    return reservation
