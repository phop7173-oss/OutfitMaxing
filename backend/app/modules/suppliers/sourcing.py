"""Sourcing requests: asking a supplier for the units customers have preordered.

Lifecycle: REQUESTED -> CONFIRMED -> SHIPPED -> RECEIVED, or FAILED / CANCELLED.
A request is only built from preorders whose deposit is verified. Goods that arrive are
allocated to the waiting preorders first (oldest first); leftover units are NOT added to physical
stock unless the owner moves them explicitly (move_surplus_to_stock)."""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.modules.inventory import service as inventory_service
from app.modules.notifications.service import emit
from app.modules.suppliers.models import PreorderCommitment, SourcingRequest, SupplierProduct
from app.modules.suppliers.service import _now, get_supplier_product

SUPPLIER_CONFIRMATION_DAYS = 3  # owner is alerted after this long without a confirmation


def _locked_request(session: Session, request_id: uuid.UUID) -> SourcingRequest:
    request = session.execute(
        select(SourcingRequest).where(SourcingRequest.id == request_id).with_for_update()
    ).scalar_one_or_none()
    if request is None:
        raise NotFoundError("Sourcing request not found")
    return request


def _require_status(session: Session, request: SourcingRequest, allowed: set[str]) -> None:
    if request.status not in allowed:
        session.rollback()
        raise ConflictError(
            f"A {request.status} request cannot do that (needs: {', '.join(sorted(allowed))})"
        )


def _linked_held(session: Session, request_id: uuid.UUID) -> list[PreorderCommitment]:
    stmt = (
        select(PreorderCommitment)
        .where(
            PreorderCommitment.sourcing_request_id == request_id,
            PreorderCommitment.status == "HELD",
        )
        .order_by(PreorderCommitment.created_at, PreorderCommitment.id)
        .with_for_update()
    )
    return list(session.execute(stmt).scalars())


def _unlink(session: Session, request: SourcingRequest, *, needs_review: bool) -> None:
    values: dict[str, object] = {"sourcing_request_id": None}
    if needs_review:
        values["needs_review"] = True
    session.execute(
        update(PreorderCommitment)
        .where(
            PreorderCommitment.sourcing_request_id == request.id,
            PreorderCommitment.status == "HELD",
        )
        .values(**values)
    )


def create_request(
    session: Session,
    supplier_product_id: uuid.UUID,
    *,
    note: str | None = None,
    now: datetime | None = None,
) -> SourcingRequest:
    """Bundle every deposit-verified, not-yet-requested preorder of this supplier product."""
    now = now or _now()
    supplier_product = get_supplier_product(session, supplier_product_id)
    stmt = (
        select(PreorderCommitment)
        .where(
            PreorderCommitment.supplier_product_id == supplier_product.id,
            PreorderCommitment.status == "HELD",
            PreorderCommitment.sourcing_request_id.is_(None),
            PreorderCommitment.deposit_verified.is_(True),
        )
        .order_by(PreorderCommitment.created_at, PreorderCommitment.id)
        .with_for_update()
    )
    waiting = list(session.execute(stmt).scalars())
    if not waiting:
        session.rollback()
        raise UnprocessableError("No deposit-verified preorders are waiting for this supplier")
    request = SourcingRequest(
        supplier_product_id=supplier_product.id,
        quantity=sum(c.quantity for c in waiting),
        status="REQUESTED",
        note=note,
        requested_at=now,
    )
    session.add(request)
    session.flush()
    for c in waiting:
        c.sourcing_request_id = request.id
    session.commit()
    return request


def confirm_request(
    session: Session,
    request_id: uuid.UUID,
    expected_from: date,
    expected_to: date,
    *,
    now: datetime | None = None,
) -> SourcingRequest:
    """Supplier confirmed. The confirmed window replaces the estimate on each linked preorder;
    every customer whose window changed gets one queued notification."""
    now = now or _now()
    request = _locked_request(session, request_id)
    _require_status(session, request, {"REQUESTED"})
    request.status = "CONFIRMED"
    request.confirmed_at = now
    request.expected_from, request.expected_to = expected_from, expected_to
    for c in _linked_held(session, request.id):
        changed = (c.expected_from, c.expected_to) != (expected_from, expected_to)
        c.expected_from, c.expected_to = expected_from, expected_to
        c.needs_review = False
        if changed:
            emit(
                session,
                "preorder.window_changed",
                {
                    "commitment_id": str(c.id),
                    "reference_type": c.reference_type,
                    "reference_id": c.reference_id,
                    "expected_from": expected_from.isoformat(),
                    "expected_to": expected_to.isoformat(),
                },
                dedupe_key=f"window:{c.id}:{expected_from.isoformat()}:{expected_to.isoformat()}",
            )
    session.commit()
    return request


def ship_request(
    session: Session, request_id: uuid.UUID, *, now: datetime | None = None
) -> SourcingRequest:
    request = _locked_request(session, request_id)
    _require_status(session, request, {"CONFIRMED"})
    request.status = "SHIPPED"
    request.shipped_at = now or _now()
    session.commit()
    return request


def receive_request(
    session: Session, request_id: uuid.UUID, received_quantity: int, *, now: datetime | None = None
) -> SourcingRequest:
    """Goods arrived. Allocate strictly oldest-first to the waiting preorders; the first one that
    cannot be covered and everything after it is flagged for owner review. Units left over stay
    on the request as `surplus_remaining` and do NOT become physical stock automatically."""
    if received_quantity < 1:
        raise UnprocessableError("received_quantity must be at least 1")
    now = now or _now()
    request = _locked_request(session, request_id)
    _require_status(session, request, {"CONFIRMED", "SHIPPED"})
    remaining, allocated, short = received_quantity, 0, 0
    exhausted = False
    for c in _linked_held(session, request.id):
        if not exhausted and remaining >= c.quantity:
            remaining -= c.quantity
            allocated += c.quantity
            c.status = "FULFILLED"
            c.fulfilled_at = now
            result = session.execute(
                update(SupplierProduct)
                .where(
                    SupplierProduct.id == c.supplier_product_id,
                    SupplierProduct.committed >= c.quantity,
                    SupplierProduct.capacity >= c.quantity,
                )
                .values(
                    capacity=SupplierProduct.capacity - c.quantity,
                    committed=SupplierProduct.committed - c.quantity,
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("preorder invariant violated: fulfil without capacity")
            emit(
                session,
                "preorder.ready",
                {
                    "commitment_id": str(c.id),
                    "reference_type": c.reference_type,
                    "reference_id": c.reference_id,
                },
                dedupe_key=f"ready:{c.id}",
            )
        else:
            exhausted = True
            c.needs_review = True
            short += 1
    request.status = "RECEIVED"
    request.received_at = now
    request.received_quantity = received_quantity
    request.allocated_quantity = allocated
    request.surplus_remaining = remaining
    if short:
        emit(
            session,
            "owner.short_delivery",
            {"request_id": str(request.id), "short_preorders": short},
            dedupe_key=f"short:{request.id}",
        )
    session.commit()
    return request


def fail_request(
    session: Session, request_id: uuid.UUID, *, note: str | None = None
) -> SourcingRequest:
    """Supplier cannot deliver. Preorders are unlinked and flagged for the owner, who decides
    (re-request elsewhere, or release and refund under the approved policy)."""
    request = _locked_request(session, request_id)
    _require_status(session, request, {"REQUESTED", "CONFIRMED", "SHIPPED"})
    _unlink(session, request, needs_review=True)
    request.status = "FAILED"
    if note:
        request.note = note
    emit(
        session,
        "owner.sourcing_failed",
        {"request_id": str(request.id)},
        dedupe_key=f"failed:{request.id}",
    )
    session.commit()
    return request


def cancel_request(
    session: Session, request_id: uuid.UUID, *, note: str | None = None
) -> SourcingRequest:
    """Owner withdraws the request; its preorders go back to waiting and can be re-requested."""
    request = _locked_request(session, request_id)
    _require_status(session, request, {"REQUESTED", "CONFIRMED", "SHIPPED"})
    _unlink(session, request, needs_review=False)
    request.status = "CANCELLED"
    if note:
        request.note = note
    session.commit()
    return request


def move_surplus_to_stock(
    session: Session,
    request_id: uuid.UUID,
    quantity: int,
    *,
    note: str | None = None,
    location_code: str = "main",
) -> SourcingRequest:
    """Explicit owner action: add leftover supplier units to physical stock (and log it)."""
    request = _locked_request(session, request_id)
    _require_status(session, request, {"RECEIVED"})
    if quantity < 1 or quantity > request.surplus_remaining:
        session.rollback()
        raise UnprocessableError("quantity must be between 1 and the surplus remaining")
    supplier_product = session.get(SupplierProduct, request.supplier_product_id)
    sku = supplier_product.variant.sku  # type: ignore[union-attr]
    request.surplus_remaining -= quantity
    try:
        # receive_stock commits this same transaction: surplus decrement and stock change together
        inventory_service.receive_stock(
            session,
            sku,
            quantity,
            location_code=location_code,
            note=note or f"surplus from sourcing request {request.id}",
        )
    except Exception:
        session.rollback()
        raise
    return request


def list_requests(
    session: Session, *, status: str | None = None, limit: int = 100
) -> list[SourcingRequest]:
    stmt = select(SourcingRequest).options(
        joinedload(SourcingRequest.supplier_product).joinedload(SupplierProduct.variant)
    )
    if status:
        stmt = stmt.where(SourcingRequest.status == status)
    stmt = stmt.order_by(SourcingRequest.requested_at.desc(), SourcingRequest.id).limit(limit)
    return list(session.execute(stmt).scalars())


def list_unconfirmed_requests(
    session: Session, *, now: datetime | None = None, days: int = SUPPLIER_CONFIRMATION_DAYS
) -> list[SourcingRequest]:
    cutoff = (now or _now()) - timedelta(days=days)
    stmt = (
        select(SourcingRequest)
        .where(SourcingRequest.status == "REQUESTED", SourcingRequest.requested_at <= cutoff)
        .options(joinedload(SourcingRequest.supplier_product).joinedload(SupplierProduct.variant))
        .order_by(SourcingRequest.requested_at)
    )
    return list(session.execute(stmt).scalars())


def flag_unconfirmed_requests(
    session: Session, *, now: datetime | None = None, days: int = SUPPLIER_CONFIRMATION_DAYS
) -> int:
    """Queue ONE owner alert per request that has waited too long. Nothing is cancelled."""
    items = list_unconfirmed_requests(session, now=now, days=days)
    for request in items:
        emit(
            session,
            "owner.supplier_unconfirmed",
            {"request_id": str(request.id), "requested_at": request.requested_at.isoformat()},
            dedupe_key=f"unconfirmed:{request.id}",
        )
    session.commit()
    return len(items)
