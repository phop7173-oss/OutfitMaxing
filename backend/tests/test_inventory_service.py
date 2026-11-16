"""Inventory rules against real PostgreSQL, rolled back after each test."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ConflictError,
    InsufficientStockError,
    NotFoundError,
    UnprocessableError,
)
from app.modules.inventory import service
from app.modules.inventory.models import InventoryBalance, InventoryMovement, InventoryReservation
from app.modules.products import service as catalog
from app.modules.products.schemas import (
    CategoryCreate,
    OfferCreate,
    ProductCreate,
    ProductUpdate,
    VariantCreate,
    VariantUpdate,
)

pytestmark = pytest.mark.db

TTL = 15


def sellable_variant(db, *, sku="INV-1", mode="STOCK", publish=True, slug="sneakers"):
    catalog.create_category(db, CategoryCreate(name="Sneakers", slug=slug))
    product = catalog.create_product(
        db, ProductCreate(name="Air", category_slug=slug, fulfilment_mode=mode)
    )
    product = catalog.add_variant(db, product.public_code, VariantCreate(sku=sku, size="42"))
    catalog.create_offer(db, sku, OfferCreate(price_mmk=100_000))
    if publish:
        catalog.update_product(db, product.public_code, ProductUpdate(status="ACTIVE"))
    return product.variants[0]


def balance(db, variant):
    row = db.execute(
        select(InventoryBalance.on_hand, InventoryBalance.reserved).where(
            InventoryBalance.variant_id == variant.id
        )
    ).one_or_none()
    return tuple(row) if row else (0, 0)


def reserve(db, variant, qty=1, **kw):
    return service.reserve_stock(db, variant.id, qty, ttl_minutes=TTL, **kw)


def movement_reasons(db, variant):
    rows = db.execute(
        select(InventoryMovement.reason).where(InventoryMovement.variant_id == variant.id)
    )
    return sorted(r for (r,) in rows)


def test_receive_creates_then_adds_stock_and_logs_each_change(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 10, note="first delivery")
    service.receive_stock(db_session, "INV-1", 5)
    assert balance(db_session, v) == (15, 0)
    assert movement_reasons(db_session, v) == ["RECEIVE", "RECEIVE"]


def test_unknown_sku_and_location_are_not_found(db_session):
    sellable_variant(db_session)
    with pytest.raises(NotFoundError):
        service.receive_stock(db_session, "NOPE-1", 5)
    with pytest.raises(NotFoundError):
        service.receive_stock(db_session, "INV-1", 5, location_code="nowhere")


def test_adjust_cannot_go_below_reserved_or_zero(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 10)
    reserve(db_session, v, 4)
    service.adjust_stock(db_session, "INV-1", -6, note="damaged units")
    assert balance(db_session, v) == (4, 4)
    with pytest.raises(UnprocessableError, match="below"):
        service.adjust_stock(db_session, "INV-1", -1, note="one more")
    service.adjust_stock(db_session, "INV-1", 3, note="found in storage")
    assert balance(db_session, v) == (7, 4)


def test_adjust_on_a_variant_without_stock_cannot_go_negative(db_session):
    sellable_variant(db_session)
    with pytest.raises(UnprocessableError):
        service.adjust_stock(db_session, "INV-1", -1, note="phantom stock")


# ---- reserving ------------------------------------------------------------------------------
def test_reserve_holds_stock_and_logs_it(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 10)
    r = reserve(db_session, v, 3)
    assert r.status == "ACTIVE" and r.quantity == 3
    assert balance(db_session, v) == (10, 3)
    assert r.expires_at > datetime.now(UTC) + timedelta(minutes=TTL - 1)
    assert movement_reasons(db_session, v) == ["RECEIVE", "RESERVE"]


def test_cannot_reserve_more_than_available(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 2)
    reserve(db_session, v, 2)
    with pytest.raises(InsufficientStockError):
        reserve(db_session, v, 1)
    assert balance(db_session, v) == (2, 2)


def test_cannot_reserve_without_any_stock_record(db_session):
    v = sellable_variant(db_session)
    with pytest.raises(InsufficientStockError):
        reserve(db_session, v, 1)


def test_only_stock_products_on_sale_can_be_reserved(db_session):
    draft = sellable_variant(db_session, sku="DRAFT-1", publish=False)
    service.receive_stock(db_session, "DRAFT-1", 5)
    with pytest.raises(UnprocessableError, match="not on sale"):
        reserve(db_session, draft)

    preorder = sellable_variant(db_session, sku="PRE-1", mode="PREORDER", slug="preorders")
    service.receive_stock(db_session, "PRE-1", 5)
    with pytest.raises(UnprocessableError, match="Preorder"):
        reserve(db_session, preorder)

    with pytest.raises(NotFoundError):
        service.reserve_stock(db_session, uuid.uuid4(), 1, ttl_minutes=TTL)


def test_inactive_variant_cannot_be_reserved(db_session):
    v = sellable_variant(db_session)
    catalog.add_variant(db_session, v.product.public_code, VariantCreate(sku="INV-2", size="43"))
    catalog.create_offer(db_session, "INV-2", OfferCreate(price_mmk=100_000))
    catalog.update_variant(db_session, "INV-2", VariantUpdate(is_active=False))
    service.receive_stock(db_session, "INV-2", 5)
    inactive = service.get_variant_by_sku(db_session, "INV-2")
    with pytest.raises(UnprocessableError, match="not on sale"):
        reserve(db_session, inactive)


def test_non_positive_quantity_is_rejected(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 5)
    with pytest.raises(UnprocessableError):
        reserve(db_session, v, 0)


# ---- release and commit ---------------------------------------------------------------------
def test_release_returns_the_stock_and_is_idempotent(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 5)
    r = reserve(db_session, v, 2)
    assert service.release_reservation(db_session, r.id).status == "RELEASED"
    assert balance(db_session, v) == (5, 0)
    assert service.release_reservation(db_session, r.id).status == "RELEASED"
    assert balance(db_session, v) == (5, 0)
    assert movement_reasons(db_session, v).count("RELEASE") == 1


def test_commit_deducts_exactly_once(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 10)
    r = reserve(db_session, v, 3)
    assert service.commit_reservation(db_session, r.id).status == "COMMITTED"
    assert balance(db_session, v) == (7, 0)
    assert service.commit_reservation(db_session, r.id).status == "COMMITTED"
    assert balance(db_session, v) == (7, 0)
    assert movement_reasons(db_session, v).count("COMMIT") == 1


def test_committed_reservation_cannot_be_released(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 5)
    r = reserve(db_session, v, 1)
    service.commit_reservation(db_session, r.id)
    with pytest.raises(UnprocessableError, match="committed"):
        service.release_reservation(db_session, r.id)


def test_commit_after_release_conflicts(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 5)
    r = reserve(db_session, v, 1)
    service.release_reservation(db_session, r.id)
    with pytest.raises(ConflictError, match="no longer active"):
        service.commit_reservation(db_session, r.id)
    assert balance(db_session, v) == (5, 0)


def test_unknown_reservation_is_not_found(db_session):
    with pytest.raises(NotFoundError):
        service.commit_reservation(db_session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        service.release_reservation(db_session, uuid.uuid4())


# ---- expiry ---------------------------------------------------------------------------------
def test_committing_an_expired_hold_fails_and_frees_the_stock(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 5)
    long_ago = datetime.now(UTC) - timedelta(minutes=60)
    r = reserve(db_session, v, 2, now=long_ago)
    with pytest.raises(ConflictError, match="expired"):
        service.commit_reservation(db_session, r.id)
    assert balance(db_session, v) == (5, 0)
    assert db_session.get(InventoryReservation, r.id).status == "EXPIRED"


def test_an_expired_hold_is_freed_for_the_next_buyer(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 1)
    old = reserve(db_session, v, 1, now=datetime.now(UTC) - timedelta(minutes=60))
    new = reserve(db_session, v, 1)
    assert new.status == "ACTIVE"
    assert db_session.get(InventoryReservation, old.id).status == "EXPIRED"
    assert balance(db_session, v) == (1, 1)


def test_sweeper_releases_every_expired_hold(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 10)
    live = reserve(db_session, v, 1)
    # created after the live hold, so no reserve() call frees them before the sweeper runs
    past = datetime.now(UTC) - timedelta(minutes=60)
    reserve(db_session, v, 2, now=past)
    reserve(db_session, v, 3, now=past)
    assert service.expire_due_reservations(db_session) == 2
    assert service.expire_due_reservations(db_session) == 0
    assert balance(db_session, v) == (10, 1)
    assert db_session.get(InventoryReservation, live.id).status == "ACTIVE"


def test_availability_ignores_expired_holds_and_never_goes_negative(db_session):
    v = sellable_variant(db_session)
    assert service.available_quantities(db_session, [v.id]) == {v.id: 0}
    service.receive_stock(db_session, "INV-1", 5)
    reserve(db_session, v, 2, now=datetime.now(UTC) - timedelta(minutes=60))  # already expired
    reserve(db_session, v, 1, now=datetime.now(UTC))
    # the expired hold is freed by the second reserve; availability = 5 - 1
    assert service.available_quantities(db_session, [v.id]) == {v.id: 4}


# ---- idempotency ----------------------------------------------------------------------------
def test_same_idempotency_key_returns_the_same_reservation(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 10)
    first = reserve(db_session, v, 2, idempotency_key="cart-123")
    again = reserve(db_session, v, 2, idempotency_key="cart-123")
    assert again.id == first.id
    assert balance(db_session, v) == (10, 2)
    with pytest.raises(ConflictError, match="idempotency"):
        reserve(db_session, v, 3, idempotency_key="cart-123")


# ---- audit and constraints ------------------------------------------------------------------
def test_movements_always_add_up_to_the_balance(db_session):
    v = sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 10)
    a = reserve(db_session, v, 3)
    b = reserve(db_session, v, 2)
    service.release_reservation(db_session, b.id)
    service.commit_reservation(db_session, a.id)
    reserve(db_session, v, 1, now=datetime.now(UTC) - timedelta(minutes=60))
    reserve(db_session, v, 1)  # expires the previous one
    service.adjust_stock(db_session, "INV-1", -1, note="damaged unit")
    totals = db_session.execute(
        select(
            func.sum(InventoryMovement.delta_on_hand), func.sum(InventoryMovement.delta_reserved)
        ).where(InventoryMovement.variant_id == v.id)
    ).one()
    assert (int(totals[0]), int(totals[1])) == balance(db_session, v)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE inventory_balances SET reserved = on_hand + 1",
        "UPDATE inventory_balances SET on_hand = -1, reserved = 0",
        "UPDATE inventory_balances SET reserved = -1",
    ],
)
def test_database_refuses_impossible_stock_levels(db_session, statement):
    sellable_variant(db_session)
    service.receive_stock(db_session, "INV-1", 5)
    with pytest.raises(IntegrityError):
        db_session.execute(text(statement))
    db_session.rollback()
