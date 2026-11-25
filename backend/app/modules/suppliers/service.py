"""Supplier records and preorder capacity. Owns its transactions.

Preorder capacity is separate from physical stock: a commitment promises supplier-sourced units
to a customer; it never touches the inventory_* tables.
available capacity = capacity - committed (both on supplier_products)."""

import uuid
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import (
    ConflictError,
    InsufficientCapacityError,
    NotFoundError,
    UnprocessableError,
)
from app.modules.inventory import service as inventory_service
from app.modules.notifications.service import emit
from app.modules.products.models import Product, ProductVariant
from app.modules.suppliers.models import (
    PreorderCommitment,
    Supplier,
    SupplierProduct,
    SupplierStockCheck,
)
from app.modules.suppliers.schemas import (
    StockCheckCreate,
    SupplierCreate,
    SupplierProductCreate,
    SupplierProductUpdate,
)

MYANMAR_TZ = timezone(timedelta(hours=6, minutes=30))  # no daylight saving

CONFLICT_MESSAGES = {
    "uq_suppliers_code": "A supplier with this code already exists",
    "uq_supplier_products_supplier_variant": "This supplier already has an entry for that variant",
}


def _now() -> datetime:
    return datetime.now(UTC)


def local_date(now: datetime) -> date:
    """Calendar date in Myanmar time (used for arrival estimates)."""
    return now.astimezone(MYANMAR_TZ).date()


def _commit(session: Session, what: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diag = getattr(exc.orig, "diag", None)
        name = getattr(diag, "constraint_name", None)
        message = CONFLICT_MESSAGES.get(name, f"{what} conflicts with an existing record")
        raise ConflictError(message) from exc


# ---- suppliers and supplier products --------------------------------------------------------
def create_supplier(session: Session, data: SupplierCreate) -> Supplier:
    supplier = Supplier(code=data.code, name=data.name, contact_note=data.contact_note)
    session.add(supplier)
    _commit(session, "Supplier")
    return supplier


def list_suppliers(session: Session) -> list[Supplier]:
    return list(session.execute(select(Supplier).order_by(Supplier.code)).scalars())


def get_supplier(session: Session, code: str) -> Supplier:
    supplier = session.execute(select(Supplier).where(Supplier.code == code)).scalar_one_or_none()
    if supplier is None:
        raise NotFoundError("Supplier not found")
    return supplier


def get_supplier_product(
    session: Session, supplier_product_id: uuid.UUID, *, lock: bool = False
) -> SupplierProduct:
    stmt = select(SupplierProduct).where(SupplierProduct.id == supplier_product_id)
    if lock:
        stmt = stmt.with_for_update()
    else:
        stmt = stmt.options(
            joinedload(SupplierProduct.supplier), joinedload(SupplierProduct.variant)
        )
    supplier_product = session.execute(stmt).scalar_one_or_none()
    if supplier_product is None:
        raise NotFoundError("Supplier product not found")
    return supplier_product


def create_supplier_product(
    session: Session, supplier_code: str, data: SupplierProductCreate
) -> SupplierProduct:
    supplier = get_supplier(session, supplier_code)
    variant = inventory_service.get_variant_by_sku(session, data.sku)
    product = session.get(Product, variant.product_id)
    if product is None or product.fulfilment_mode != "PREORDER":
        raise UnprocessableError("Supplier capacity is only for PREORDER products")
    supplier_product = SupplierProduct(
        supplier_id=supplier.id,
        variant_id=variant.id,
        supplier_sku=data.supplier_sku,
        unit_cost_mmk=data.unit_cost_mmk,
        lead_days_min=data.lead_days_min,
        lead_days_max=data.lead_days_max,
        capacity=data.capacity,
    )
    session.add(supplier_product)
    _commit(session, "Supplier product")
    return supplier_product


def update_supplier_product(
    session: Session, supplier_product_id: uuid.UUID, data: SupplierProductUpdate
) -> SupplierProduct:
    sp = get_supplier_product(session, supplier_product_id, lock=True)
    fields = data.model_fields_set
    lead_min = data.lead_days_min if "lead_days_min" in fields else sp.lead_days_min
    lead_max = data.lead_days_max if "lead_days_max" in fields else sp.lead_days_max
    if lead_max < lead_min:
        session.rollback()
        raise UnprocessableError("lead_days_max must not be below lead_days_min")
    if "capacity" in fields and data.capacity is not None and data.capacity < sp.committed:
        session.rollback()
        raise UnprocessableError("Capacity cannot be set below the units already committed")
    sp.lead_days_min, sp.lead_days_max = lead_min, lead_max
    for field in ("capacity", "status", "supplier_sku", "unit_cost_mmk"):
        if field in fields:
            setattr(sp, field, getattr(data, field))
    _commit(session, "Supplier product")
    return sp


def record_stock_check(
    session: Session, supplier_product_id: uuid.UUID, data: StockCheckCreate
) -> SupplierStockCheck:
    sp = get_supplier_product(session, supplier_product_id)
    check = SupplierStockCheck(
        supplier_product_id=sp.id, available_quantity=data.available_quantity, note=data.note
    )
    session.add(check)
    _commit(session, "Stock check")
    return check


def list_supplier_products(session: Session, supplier_code: str) -> list[SupplierProduct]:
    supplier = get_supplier(session, supplier_code)
    stmt = (
        select(SupplierProduct)
        .where(SupplierProduct.supplier_id == supplier.id)
        .options(joinedload(SupplierProduct.supplier), joinedload(SupplierProduct.variant))
        .order_by(SupplierProduct.created_at, SupplierProduct.id)
    )
    return list(session.execute(stmt).scalars())


def last_stock_checks(
    session: Session, supplier_product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SupplierStockCheck]:
    if not supplier_product_ids:
        return {}
    stmt = (
        select(SupplierStockCheck)
        .where(SupplierStockCheck.supplier_product_id.in_(supplier_product_ids))
        .order_by(SupplierStockCheck.checked_at.desc())
    )
    latest: dict[uuid.UUID, SupplierStockCheck] = {}
    for check in session.execute(stmt).scalars():
        latest.setdefault(check.supplier_product_id, check)
    return latest


def preorder_info(
    session: Session, variant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    """Variants that can currently take a preorder -> (min, max) lead days shown to customers.
    A variant with no active supplier, or no capacity left, is absent (preorders closed)."""
    if not variant_ids:
        return {}
    available = SupplierProduct.capacity - SupplierProduct.committed
    stmt = (
        select(
            SupplierProduct.variant_id,
            func.min(SupplierProduct.lead_days_min),
            func.max(SupplierProduct.lead_days_max),
        )
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .where(
            SupplierProduct.variant_id.in_(variant_ids),
            SupplierProduct.status == "ACTIVE",
            Supplier.status == "ACTIVE",
            available > 0,
        )
        .group_by(SupplierProduct.variant_id)
    )
    return {vid: (int(low), int(high)) for vid, low, high in session.execute(stmt)}


# ---- preorder commitments -------------------------------------------------------------------
def _preorder_variant(session: Session, variant_id: uuid.UUID) -> None:
    variant = session.get(ProductVariant, variant_id)
    if variant is None:
        raise NotFoundError("Variant not found")
    product = session.get(Product, variant.product_id)
    if product is None or product.status != "ACTIVE" or not variant.is_active:
        raise UnprocessableError("This variant is not on sale")
    if product.fulfilment_mode != "PREORDER":
        raise UnprocessableError("Only preorder products can take preorder commitments")


def _existing_by_key(session: Session, key: str) -> PreorderCommitment | None:
    return session.execute(
        select(PreorderCommitment).where(PreorderCommitment.idempotency_key == key)
    ).scalar_one_or_none()


def _same_request(
    existing: PreorderCommitment, variant_id: uuid.UUID, quantity: int
) -> PreorderCommitment:
    if existing.variant_id != variant_id or existing.quantity != quantity:
        raise ConflictError("That idempotency key was already used for a different preorder")
    return existing


def commit_capacity(
    session: Session,
    variant_id: uuid.UUID,
    quantity: int,
    *,
    idempotency_key: str | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
    supplier_product_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> PreorderCommitment:
    """Promise `quantity` preorder units. Atomic: the capacity check and the update are one
    UPDATE, so concurrent customers can never exceed the owner's capacity. The expected window
    is today plus the supplier's lead days (Myanmar date)."""
    if quantity < 1:
        raise UnprocessableError("quantity must be at least 1")
    now = now or _now()
    if idempotency_key:
        existing = _existing_by_key(session, idempotency_key)
        if existing is not None:
            return _same_request(existing, variant_id, quantity)
    _preorder_variant(session, variant_id)
    candidates = select(
        SupplierProduct.id, SupplierProduct.lead_days_min, SupplierProduct.lead_days_max
    ).join(Supplier, Supplier.id == SupplierProduct.supplier_id).where(
        SupplierProduct.variant_id == variant_id,
        SupplierProduct.status == "ACTIVE",
        Supplier.status == "ACTIVE",
    ).order_by(SupplierProduct.lead_days_max, SupplierProduct.id)
    if supplier_product_id is not None:
        candidates = candidates.where(SupplierProduct.id == supplier_product_id)
    chosen: tuple[uuid.UUID, int, int] | None = None
    for sp_id, lead_min, lead_max in session.execute(candidates).all():
        result = session.execute(
            update(SupplierProduct)
            .where(
                SupplierProduct.id == sp_id,
                SupplierProduct.status == "ACTIVE",
                SupplierProduct.capacity - SupplierProduct.committed >= quantity,
            )
            .values(committed=SupplierProduct.committed + quantity)
        )
        if result.rowcount == 1:
            chosen = (sp_id, lead_min, lead_max)
            break
    if chosen is None:
        session.rollback()
        raise InsufficientCapacityError("No preorder capacity available for this item")
    today = local_date(now)
    commitment = PreorderCommitment(
        variant_id=variant_id,
        supplier_product_id=chosen[0],
        quantity=quantity,
        status="HELD",
        created_at=now,
        expected_from=today + timedelta(days=chosen[1]),
        expected_to=today + timedelta(days=chosen[2]),
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    session.add(commitment)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if idempotency_key:
            existing = _existing_by_key(session, idempotency_key)
            if existing is not None:  # a concurrent request with the same key won
                return _same_request(existing, variant_id, quantity)
        raise ConflictError("Preorder conflicts with an existing record") from exc
    return commitment


def _locked_commitment(session: Session, commitment_id: uuid.UUID) -> PreorderCommitment:
    commitment = session.execute(
        select(PreorderCommitment).where(PreorderCommitment.id == commitment_id).with_for_update()
    ).scalar_one_or_none()
    if commitment is None:
        raise NotFoundError("Preorder commitment not found")
    return commitment


def release_commitment(
    session: Session, commitment_id: uuid.UUID, *, now: datetime | None = None
) -> PreorderCommitment:
    """Cancel a waiting preorder and give its capacity back. Idempotent. A fulfilled preorder
    (goods already allocated) cannot be released here: that is a refund/return decision."""
    now = now or _now()
    commitment = _locked_commitment(session, commitment_id)
    if commitment.status == "FULFILLED":
        session.rollback()
        raise UnprocessableError("Preorder is already fulfilled")
    if commitment.status == "HELD":
        result = session.execute(
            update(SupplierProduct)
            .where(
                SupplierProduct.id == commitment.supplier_product_id,
                SupplierProduct.committed >= commitment.quantity,
            )
            .values(committed=SupplierProduct.committed - commitment.quantity)
        )
        if result.rowcount != 1:
            raise RuntimeError("preorder invariant violated: release without committed capacity")
        commitment.status = "RELEASED"
        commitment.released_at = now
        session.commit()
    else:
        session.rollback()
    return commitment


def mark_deposit_verified(
    session: Session, commitment_id: uuid.UUID, *, now: datetime | None = None
) -> PreorderCommitment:
    """Record that the deposit was verified by the owner (or, later, a payment provider)."""
    now = now or _now()
    commitment = _locked_commitment(session, commitment_id)
    if commitment.status != "HELD":
        session.rollback()
        raise UnprocessableError("Only waiting preorders can have a deposit verified")
    if not commitment.deposit_verified:
        commitment.deposit_verified = True
        commitment.deposit_verified_at = now
        session.commit()
    else:
        session.rollback()
    return commitment


def list_commitments(
    session: Session,
    *,
    status: str | None = None,
    needs_review: bool | None = None,
    limit: int = 100,
) -> list[PreorderCommitment]:
    stmt = select(PreorderCommitment).options(joinedload(PreorderCommitment.variant))
    if status:
        stmt = stmt.where(PreorderCommitment.status == status)
    if needs_review is not None:
        stmt = stmt.where(PreorderCommitment.needs_review.is_(needs_review))
    stmt = stmt.order_by(PreorderCommitment.created_at.desc(), PreorderCommitment.id).limit(limit)
    return list(session.execute(stmt).scalars())


def flag_overdue_commitments(session: Session, *, now: datetime | None = None) -> int:
    """Preorders whose expected window has passed: flag for owner review and queue ONE customer
    notification each. Intended for a periodic job. Returns how many were flagged."""
    today = local_date(now or _now())
    stmt = (
        select(PreorderCommitment)
        .where(
            PreorderCommitment.status == "HELD",
            PreorderCommitment.deposit_verified.is_(True),
            PreorderCommitment.needs_review.is_(False),
            PreorderCommitment.expected_to < today,
        )
        .order_by(PreorderCommitment.id)
        .limit(500)
        .with_for_update(skip_locked=True)
    )
    overdue = list(session.execute(stmt).scalars())
    for c in overdue:
        c.needs_review = True
        emit(
            session,
            "preorder.window_passed",
            {
                "commitment_id": str(c.id),
                "reference_type": c.reference_type,
                "reference_id": c.reference_id,
                "expected_to": c.expected_to.isoformat(),
            },
            dedupe_key=f"passed:{c.id}:{c.expected_to.isoformat()}",
        )
    session.commit()
    return len(overdue)
