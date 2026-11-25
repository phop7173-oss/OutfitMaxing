"""Supplier records and preorder capacity (real PostgreSQL, rolled back after each test)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ConflictError,
    InsufficientCapacityError,
    NotFoundError,
    UnprocessableError,
)
from app.modules.notifications.models import OutboxEvent
from app.modules.products import service as catalog
from app.modules.products.schemas import (
    CategoryCreate,
    OfferCreate,
    ProductCreate,
    ProductUpdate,
    VariantCreate,
)
from app.modules.suppliers import service
from app.modules.suppliers.models import SupplierProduct
from app.modules.suppliers.schemas import (
    StockCheckCreate,
    SupplierCreate,
    SupplierProductCreate,
    SupplierProductUpdate,
)

pytestmark = pytest.mark.db

NOW = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)


def preorder_variant(db, sku="PRE-1", slug="preorders", mode="PREORDER", publish=True):
    catalog.create_category(db, CategoryCreate(name=slug.title(), slug=slug))
    product = catalog.create_product(
        db, ProductCreate(name="Imported Jacket", category_slug=slug, fulfilment_mode=mode)
    )
    product = catalog.add_variant(db, product.public_code, VariantCreate(sku=sku, size="M"))
    catalog.create_offer(db, sku, OfferCreate(price_mmk=250_000))
    if publish:
        catalog.update_product(db, product.public_code, ProductUpdate(status="ACTIVE"))
    return product.variants[0]


def new_supplier(db, code="acme"):
    return service.create_supplier(db, SupplierCreate(code=code, name=f"Supplier {code}"))


def new_sp(db, code="acme", sku="PRE-1", capacity=5, lead=(10, 20)):
    data = SupplierProductCreate(
        sku=sku, lead_days_min=lead[0], lead_days_max=lead[1], capacity=capacity
    )
    return service.create_supplier_product(db, code, data)


def state(db, sp):
    return tuple(
        db.execute(
            select(SupplierProduct.capacity, SupplierProduct.committed).where(
                SupplierProduct.id == sp.id
            )
        ).one()
    )


def commit(db, variant, qty=1, **kw):
    return service.commit_capacity(db, variant.id, qty, now=NOW, **kw)


def event_count(db, topic):
    return db.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == topic)
    ).scalar_one()


# ---- suppliers and supplier products --------------------------------------------------------
def test_duplicate_supplier_code_conflicts(db_session):
    new_supplier(db_session)
    with pytest.raises(ConflictError, match="code"):
        new_supplier(db_session)


def test_supplier_products_are_only_for_preorder_items(db_session):
    preorder_variant(db_session, sku="STK-1", slug="sneakers", mode="STOCK")
    new_supplier(db_session)
    with pytest.raises(UnprocessableError, match="PREORDER"):
        new_sp(db_session, sku="STK-1")
    with pytest.raises(NotFoundError):
        new_sp(db_session, sku="NOPE-1")
    with pytest.raises(NotFoundError):
        new_sp(db_session, code="nobody")


def test_one_entry_per_supplier_and_variant(db_session):
    preorder_variant(db_session)
    new_supplier(db_session)
    new_sp(db_session)
    with pytest.raises(ConflictError, match="already has an entry"):
        new_sp(db_session)


def test_capacity_cannot_drop_below_what_is_committed(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    sp = new_sp(db_session, capacity=5)
    commit(db_session, v, 3)
    with pytest.raises(UnprocessableError, match="below"):
        service.update_supplier_product(db_session, sp.id, SupplierProductUpdate(capacity=2))
    updated = service.update_supplier_product(
        db_session, sp.id, SupplierProductUpdate(capacity=3, lead_days_min=5, lead_days_max=8)
    )
    assert (updated.capacity, updated.lead_days_min, updated.lead_days_max) == (3, 5, 8)
    with pytest.raises(UnprocessableError, match="lead_days_max"):
        service.update_supplier_product(db_session, sp.id, SupplierProductUpdate(lead_days_min=30))


def test_stock_checks_are_recorded_and_latest_wins(db_session):
    preorder_variant(db_session)
    new_supplier(db_session)
    sp = new_sp(db_session)
    service.record_stock_check(db_session, sp.id, StockCheckCreate(available_quantity=40))
    service.record_stock_check(db_session, sp.id, StockCheckCreate(available_quantity=25))
    latest = service.last_stock_checks(db_session, [sp.id])
    assert latest[sp.id].available_quantity == 25
    with pytest.raises(NotFoundError):
        service.record_stock_check(db_session, uuid.uuid4(), StockCheckCreate(available_quantity=1))


# ---- committing capacity --------------------------------------------------------------------
def test_commitment_window_uses_the_myanmar_date_and_lead_days(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    sp = new_sp(db_session, lead=(10, 20))
    late_utc = datetime(2031, 1, 1, 20, 0, tzinfo=UTC)  # already 2 January in Myanmar
    c = service.commit_capacity(db_session, v.id, 2, now=late_utc)
    assert (c.expected_from, c.expected_to) == (date(2031, 1, 12), date(2031, 1, 22))
    assert c.status == "HELD" and c.deposit_verified is False
    assert state(db_session, sp) == (5, 2)


def test_cannot_commit_more_than_the_owners_capacity(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    sp = new_sp(db_session, capacity=2)
    commit(db_session, v, 2)
    with pytest.raises(InsufficientCapacityError):
        commit(db_session, v, 1)
    assert state(db_session, sp) == (2, 2)


def test_no_capacity_or_inactive_supplier_means_no_preorders(db_session):
    v = preorder_variant(db_session)
    with pytest.raises(InsufficientCapacityError):  # no supplier at all
        commit(db_session, v)
    new_supplier(db_session)
    sp = new_sp(db_session, capacity=0)
    with pytest.raises(InsufficientCapacityError):
        commit(db_session, v)
    service.update_supplier_product(
        db_session, sp.id, SupplierProductUpdate(capacity=5, status="INACTIVE")
    )
    with pytest.raises(InsufficientCapacityError):
        commit(db_session, v)


def test_only_preorder_items_on_sale_take_commitments(db_session):
    stock_variant = preorder_variant(db_session, sku="STK-1", slug="sneakers", mode="STOCK")
    with pytest.raises(UnprocessableError, match="Only preorder"):
        commit(db_session, stock_variant)
    draft = preorder_variant(db_session, sku="DRAFT-1", slug="drafts", publish=False)
    with pytest.raises(UnprocessableError, match="not on sale"):
        commit(db_session, draft)
    with pytest.raises(NotFoundError):
        service.commit_capacity(db_session, uuid.uuid4(), 1)
    v = preorder_variant(db_session, sku="PRE-9", slug="more")
    with pytest.raises(UnprocessableError):
        commit(db_session, v, 0)


def test_faster_supplier_is_used_first_then_the_next(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session, "slow")
    new_supplier(db_session, "fast")
    slow = new_sp(db_session, code="slow", capacity=5, lead=(30, 40))
    fast = new_sp(db_session, code="fast", capacity=1, lead=(5, 7))
    first = commit(db_session, v)
    second = commit(db_session, v)
    assert first.supplier_product_id == fast.id
    assert second.supplier_product_id == slow.id
    assert state(db_session, fast) == (1, 1) and state(db_session, slow) == (5, 1)


def test_idempotency_key_returns_the_same_commitment(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    sp = new_sp(db_session)
    first = commit(db_session, v, 2, idempotency_key="order-1")
    again = commit(db_session, v, 2, idempotency_key="order-1")
    assert again.id == first.id and state(db_session, sp) == (5, 2)
    with pytest.raises(ConflictError, match="idempotency"):
        commit(db_session, v, 3, idempotency_key="order-1")


def test_release_returns_capacity_and_is_idempotent(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    sp = new_sp(db_session)
    c = commit(db_session, v, 2)
    assert service.release_commitment(db_session, c.id).status == "RELEASED"
    assert service.release_commitment(db_session, c.id).status == "RELEASED"
    assert state(db_session, sp) == (5, 0)
    with pytest.raises(NotFoundError):
        service.release_commitment(db_session, uuid.uuid4())


def test_deposit_verification_is_idempotent_and_only_for_waiting_preorders(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    new_sp(db_session)
    c = commit(db_session, v)
    first = service.mark_deposit_verified(db_session, c.id, now=NOW)
    again = service.mark_deposit_verified(db_session, c.id, now=NOW + timedelta(hours=1))
    assert first.deposit_verified and again.deposit_verified_at == NOW
    service.release_commitment(db_session, c.id)
    with pytest.raises(UnprocessableError, match="waiting"):
        service.mark_deposit_verified(db_session, c.id)


# ---- what customers see ---------------------------------------------------------------------
def test_preorder_info_shows_open_items_with_lead_times(db_session):
    v = preorder_variant(db_session)
    assert service.preorder_info(db_session, [v.id]) == {}  # no supplier: closed
    new_supplier(db_session, "slow")
    new_supplier(db_session, "fast")
    new_sp(db_session, code="slow", capacity=2, lead=(30, 40))
    new_sp(db_session, code="fast", capacity=1, lead=(5, 7))
    assert service.preorder_info(db_session, [v.id]) == {v.id: (5, 40)}
    commit(db_session, v)  # uses the faster supplier's only unit
    assert service.preorder_info(db_session, [v.id]) == {v.id: (30, 40)}
    assert service.preorder_info(db_session, []) == {}


# ---- overdue windows ------------------------------------------------------------------------
def test_overdue_verified_preorders_are_flagged_once_with_one_notification(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    new_sp(db_session, capacity=10)
    verified = commit(db_session, v)
    service.mark_deposit_verified(db_session, verified.id)
    commit(db_session, v)  # deposit not verified: customer is not waiting yet
    before = event_count(db_session, "preorder.window_passed")
    later = NOW + timedelta(days=60)
    assert service.flag_overdue_commitments(db_session, now=NOW + timedelta(days=1)) == 0
    assert service.flag_overdue_commitments(db_session, now=later) == 1
    assert service.flag_overdue_commitments(db_session, now=later) == 0
    assert event_count(db_session, "preorder.window_passed") == before + 1
    db_session.refresh(verified)
    assert verified.needs_review is True


def test_database_refuses_commitments_above_capacity(db_session):
    v = preorder_variant(db_session)
    new_supplier(db_session)
    new_sp(db_session, capacity=2)
    commit(db_session, v, 2)
    with pytest.raises(IntegrityError):
        db_session.execute(text("UPDATE supplier_products SET committed = capacity + 1"))
    db_session.rollback()
