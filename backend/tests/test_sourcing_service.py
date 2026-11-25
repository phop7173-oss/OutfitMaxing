"""Sourcing requests: lifecycle, allocation, surplus, alerts (real PostgreSQL, rolled back)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.modules.inventory import service as inventory_service
from app.modules.inventory.models import InventoryBalance
from app.modules.notifications.models import OutboxEvent
from app.modules.products import service as catalog
from app.modules.products.schemas import (
    CategoryCreate,
    OfferCreate,
    ProductCreate,
    ProductUpdate,
    VariantCreate,
)
from app.modules.suppliers import service, sourcing
from app.modules.suppliers.models import SupplierProduct
from app.modules.suppliers.schemas import SupplierCreate, SupplierProductCreate

pytestmark = pytest.mark.db

NOW = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)
WINDOW = (date(2031, 2, 1), date(2031, 2, 15))


def waiting(db, quantities=(2, 3), verify=True, capacity=50):
    """A PREORDER variant, a supplier, and one commitment per quantity (oldest first)."""
    catalog.create_category(db, CategoryCreate(name="Preorders", slug="preorders"))
    product = catalog.create_product(
        db,
        ProductCreate(
            name="Imported Jacket", category_slug="preorders", fulfilment_mode="PREORDER"
        ),
    )
    product = catalog.add_variant(db, product.public_code, VariantCreate(sku="PRE-1", size="M"))
    catalog.create_offer(db, "PRE-1", OfferCreate(price_mmk=250_000))
    catalog.update_product(db, product.public_code, ProductUpdate(status="ACTIVE"))
    variant = product.variants[0]
    service.create_supplier(db, SupplierCreate(code="acme", name="Acme"))
    sp = service.create_supplier_product(
        db,
        "acme",
        SupplierProductCreate(sku="PRE-1", lead_days_min=10, lead_days_max=20, capacity=capacity),
    )
    commitments = []
    for i, qty in enumerate(quantities):
        c = service.commit_capacity(
            db,
            variant.id,
            qty,
            now=NOW + timedelta(minutes=i),
            reference_type="order_item",
            reference_id=f"oi-{i}",
        )
        if verify:
            service.mark_deposit_verified(db, c.id)
        commitments.append(c)
    return variant, sp, commitments


def event_count(db, topic):
    return db.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == topic)
    ).scalar_one()


def capacity_state(db, sp):
    row = db.execute(
        select(SupplierProduct.capacity, SupplierProduct.committed).where(
            SupplierProduct.id == sp.id
        )
    ).one()
    return tuple(row)


def requested_and_confirmed(db, sp):
    request = sourcing.create_request(db, sp.id, now=NOW)
    return sourcing.confirm_request(db, request.id, *WINDOW, now=NOW)


# ---- creating a request ---------------------------------------------------------------------
def test_request_bundles_only_deposit_verified_waiting_preorders(db_session):
    _, sp, commitments = waiting(db_session, quantities=(2, 3), verify=False)
    with pytest.raises(UnprocessableError, match="deposit-verified"):
        sourcing.create_request(db_session, sp.id, now=NOW)
    service.mark_deposit_verified(db_session, commitments[0].id)
    request = sourcing.create_request(db_session, sp.id, note="first batch", now=NOW)
    assert request.quantity == 2 and request.status == "REQUESTED"
    db_session.refresh(commitments[0])
    db_session.refresh(commitments[1])
    assert commitments[0].sourcing_request_id == request.id
    assert commitments[1].sourcing_request_id is None  # unverified deposit: not sent
    with pytest.raises(UnprocessableError):  # nothing new is waiting
        sourcing.create_request(db_session, sp.id, now=NOW)


def test_unknown_ids_are_not_found(db_session):
    with pytest.raises(NotFoundError):
        sourcing.create_request(db_session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        sourcing.confirm_request(db_session, uuid.uuid4(), *WINDOW)
    with pytest.raises(NotFoundError):
        sourcing.ship_request(db_session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        sourcing.receive_request(db_session, uuid.uuid4(), 1)


# ---- confirm, ship, notifications ------------------------------------------------------------
def test_confirmation_sets_the_window_and_notifies_each_changed_customer_once(db_session):
    _, sp, commitments = waiting(db_session, quantities=(2, 3))
    request = sourcing.create_request(db_session, sp.id, now=NOW)
    before = event_count(db_session, "preorder.window_changed")
    confirmed = sourcing.confirm_request(db_session, request.id, *WINDOW, now=NOW)
    assert confirmed.status == "CONFIRMED"
    assert (confirmed.expected_from, confirmed.expected_to) == WINDOW
    for c in commitments:
        db_session.refresh(c)
        assert (c.expected_from, c.expected_to) == WINDOW
    assert event_count(db_session, "preorder.window_changed") == before + 2


def test_status_order_is_enforced(db_session):
    _, sp, _ = waiting(db_session)
    request = sourcing.create_request(db_session, sp.id, now=NOW)
    with pytest.raises(ConflictError):
        sourcing.ship_request(db_session, request.id)  # not confirmed yet
    with pytest.raises(ConflictError):
        sourcing.receive_request(db_session, request.id, 5)
    sourcing.confirm_request(db_session, request.id, *WINDOW, now=NOW)
    with pytest.raises(ConflictError):
        sourcing.confirm_request(db_session, request.id, *WINDOW, now=NOW)
    assert sourcing.ship_request(db_session, request.id, now=NOW).status == "SHIPPED"
    sourcing.fail_request(db_session, request.id)
    with pytest.raises(ConflictError):
        sourcing.fail_request(db_session, request.id)
    with pytest.raises(ConflictError):
        sourcing.cancel_request(db_session, request.id)


# ---- receiving goods ------------------------------------------------------------------------
def test_exact_delivery_fulfils_every_waiting_preorder(db_session):
    _, sp, commitments = waiting(db_session, quantities=(2, 3))
    request = requested_and_confirmed(db_session, sp)
    before = event_count(db_session, "preorder.ready")
    done = sourcing.receive_request(db_session, request.id, 5, now=NOW)
    assert (done.status, done.allocated_quantity, done.surplus_remaining) == ("RECEIVED", 5, 0)
    for c in commitments:
        db_session.refresh(c)
        assert c.status == "FULFILLED" and c.fulfilled_at == NOW
    assert capacity_state(db_session, sp) == (45, 0)  # the batch left the capacity pool
    assert event_count(db_session, "preorder.ready") == before + 2


def test_short_delivery_is_strictly_oldest_first_and_flags_the_rest(db_session):
    _, sp, commitments = waiting(db_session, quantities=(2, 3, 1))
    request = requested_and_confirmed(db_session, sp)
    before = event_count(db_session, "owner.short_delivery")
    done = sourcing.receive_request(db_session, request.id, 4, now=NOW)
    assert (done.allocated_quantity, done.surplus_remaining) == (2, 2)
    for c in commitments:
        db_session.refresh(c)
    assert [c.status for c in commitments] == ["FULFILLED", "HELD", "HELD"]
    assert [c.needs_review for c in commitments] == [False, True, True]  # no queue jumping
    assert event_count(db_session, "owner.short_delivery") == before + 1
    assert capacity_state(db_session, sp) == (48, 4)


def test_surplus_becomes_physical_stock_only_when_the_owner_moves_it(db_session):
    variant, sp, _ = waiting(db_session, quantities=(2, 3))
    request = requested_and_confirmed(db_session, sp)
    done = sourcing.receive_request(db_session, request.id, 8, now=NOW)
    assert done.surplus_remaining == 3
    on_hand = db_session.execute(
        select(InventoryBalance.on_hand).where(InventoryBalance.variant_id == variant.id)
    ).scalar_one_or_none()
    assert on_hand is None  # nothing was added to physical stock automatically

    moved = sourcing.move_surplus_to_stock(db_session, request.id, 2)
    assert moved.surplus_remaining == 1
    on_hand = db_session.execute(
        select(InventoryBalance.on_hand).where(InventoryBalance.variant_id == variant.id)
    ).scalar_one()
    assert on_hand == 2
    with pytest.raises(UnprocessableError, match="surplus"):
        sourcing.move_surplus_to_stock(db_session, request.id, 2)
    with pytest.raises(UnprocessableError, match="Preorder"):  # still a preorder product
        inventory_service.reserve_stock(db_session, variant.id, 1, ttl_minutes=15)


def test_surplus_can_only_be_moved_from_a_received_request(db_session):
    _, sp, _ = waiting(db_session)
    request = requested_and_confirmed(db_session, sp)
    with pytest.raises(ConflictError):
        sourcing.move_surplus_to_stock(db_session, request.id, 1)
    with pytest.raises(UnprocessableError):
        sourcing.receive_request(db_session, request.id, 0)


def test_fulfilled_preorders_cannot_be_released(db_session):
    _, sp, commitments = waiting(db_session, quantities=(2,))
    request = requested_and_confirmed(db_session, sp)
    sourcing.receive_request(db_session, request.id, 2, now=NOW)
    with pytest.raises(UnprocessableError, match="fulfilled"):
        service.release_commitment(db_session, commitments[0].id)


# ---- failure, cancellation, alerts ----------------------------------------------------------
def test_failed_request_flags_preorders_for_the_owner_and_they_can_be_requested_again(db_session):
    _, sp, commitments = waiting(db_session, quantities=(2, 3))
    request = sourcing.create_request(db_session, sp.id, now=NOW)
    before = event_count(db_session, "owner.sourcing_failed")
    failed = sourcing.fail_request(db_session, request.id, note="supplier out of stock")
    assert failed.status == "FAILED"
    assert event_count(db_session, "owner.sourcing_failed") == before + 1
    for c in commitments:
        db_session.refresh(c)
        assert c.sourcing_request_id is None and c.needs_review is True
    retry = sourcing.create_request(db_session, sp.id, now=NOW + timedelta(days=1))
    sourcing.confirm_request(db_session, retry.id, *WINDOW, now=NOW)
    for c in commitments:
        db_session.refresh(c)
        assert c.needs_review is False  # a new confirmed window clears the review flag


def test_cancelled_request_puts_preorders_back_to_waiting(db_session):
    _, sp, commitments = waiting(db_session, quantities=(2,))
    request = sourcing.create_request(db_session, sp.id, now=NOW)
    assert sourcing.cancel_request(db_session, request.id).status == "CANCELLED"
    db_session.refresh(commitments[0])
    assert commitments[0].sourcing_request_id is None and commitments[0].needs_review is False
    assert sourcing.create_request(db_session, sp.id, now=NOW).quantity == 2


def test_owner_is_alerted_once_when_a_supplier_has_not_confirmed_in_three_days(db_session):
    _, sp, _ = waiting(db_session)
    request = sourcing.create_request(db_session, sp.id, now=NOW)
    assert sourcing.list_unconfirmed_requests(db_session, now=NOW + timedelta(days=2)) == []
    due = sourcing.list_unconfirmed_requests(db_session, now=NOW + timedelta(days=3))
    assert [r.id for r in due] == [request.id]
    before = event_count(db_session, "owner.supplier_unconfirmed")
    later = NOW + timedelta(days=4)
    assert sourcing.flag_unconfirmed_requests(db_session, now=later) == 1
    assert sourcing.flag_unconfirmed_requests(db_session, now=later) == 1
    assert event_count(db_session, "owner.supplier_unconfirmed") == before + 1  # one alert only
    assert request.status == "REQUESTED"  # nothing is cancelled automatically
