"""Offer (price) rules against real PostgreSQL, rolled back after each test."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.modules.products import service
from app.modules.products.models import Offer
from app.modules.products.schemas import (
    CategoryCreate,
    OfferCreate,
    ProductCreate,
    ProductUpdate,
    VariantCreate,
    VariantUpdate,
)

pytestmark = pytest.mark.db

BASE = datetime(2031, 1, 1, tzinfo=UTC)


def day(n):
    return BASE + timedelta(days=n)


def window(start, end):
    data = {"starts_at": day(start)}
    if end is not None:
        data["ends_at"] = day(end)
    return data


def new_product(db, skus=("OF-1",), slug="sneakers"):
    service.create_category(db, CategoryCreate(name="Sneakers", slug=slug))
    product = service.create_product(
        db, ProductCreate(name="Air", category_slug=slug, fulfilment_mode="STOCK")
    )
    for i, sku in enumerate(skus):
        product = service.add_variant(
            db, product.public_code, VariantCreate(sku=sku, size=str(40 + i))
        )
    return product


def offer(db, sku="OF-1", amount=100_000, **kw):
    return service.create_offer(db, sku, OfferCreate(price_mmk=amount, **kw))


def test_current_price_follows_the_time_window(db_session):
    variant = new_product(db_session).variants[0]
    offer(db_session, amount=100_000, compare_at_price_mmk=120_000, **window(0, 10))
    offer(db_session, amount=80_000, starts_at=day(10))

    def price_at(moment):
        found = service.current_prices(db_session, [variant.id], at=moment)
        return found[variant.id].price_mmk if variant.id in found else None

    assert price_at(day(0) - timedelta(seconds=1)) is None
    assert price_at(day(0)) == 100_000
    assert price_at(day(5)) == 100_000
    assert price_at(day(10)) == 80_000  # the end of one window is the start of the next
    assert price_at(day(999)) == 80_000


def test_offer_without_start_is_current_now(db_session):
    variant = new_product(db_session).variants[0]
    created = offer(db_session)
    assert service.offer_is_current(created)
    assert variant.id in service.current_prices(db_session, [variant.id])


def test_end_in_the_past_without_start_is_rejected(db_session):
    new_product(db_session)
    with pytest.raises(UnprocessableError, match="ends_at"):
        offer(db_session, ends_at=datetime(2000, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("start", "end"), [(5, 15), (-5, 1), (9, None), (0, 10), (2, 3), (-5, None)]
)
def test_overlapping_windows_conflict(db_session, start, end):
    new_product(db_session)
    offer(db_session, **window(0, 10))
    with pytest.raises(ConflictError, match="overlaps"):
        offer(db_session, amount=90_000, **window(start, end))


@pytest.mark.parametrize(("start", "end"), [(10, None), (-5, 0), (20, 30)])
def test_adjacent_or_separate_windows_are_allowed(db_session, start, end):
    new_product(db_session)
    offer(db_session, **window(0, 10))
    assert offer(db_session, amount=90_000, **window(start, end)).price_mmk == 90_000


def test_cancelled_offer_frees_its_window(db_session):
    new_product(db_session)
    first = offer(db_session, **window(0, 10))
    cancelled = service.cancel_offer(db_session, first.id)
    assert cancelled.status == "CANCELLED"
    assert offer(db_session, amount=70_000, **window(0, 10)).status == "ACTIVE"


def test_cancel_is_idempotent_and_unknown_ids_are_not_found(db_session):
    new_product(db_session)
    first = offer(db_session)
    service.cancel_offer(db_session, first.id)
    assert service.cancel_offer(db_session, first.id).status == "CANCELLED"
    with pytest.raises(NotFoundError):
        service.cancel_offer(db_session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        offer(db_session, sku="NOPE-1")
    with pytest.raises(NotFoundError):
        service.list_offers(db_session, "NOPE-1")


def test_offer_history_is_kept_newest_first(db_session):
    new_product(db_session)
    offer(db_session, amount=100_000, **window(0, 10))
    offer(db_session, amount=80_000, starts_at=day(10))
    history = service.list_offers(db_session, "OF-1")
    assert [o.price_mmk for o in history] == [80_000, 100_000]


@pytest.mark.parametrize(
    "bad",
    [
        {"price_mmk": 0},
        {"price_mmk": 100, "compare_at_price_mmk": 100},
        {"ends_at": BASE},
        {"status": "BOGUS"},
    ],
)
def test_database_rejects_invalid_offers(db_session, bad):
    variant = new_product(db_session).variants[0]
    fields = {"variant_id": variant.id, "price_mmk": 100, "starts_at": BASE} | bad
    db_session.add(Offer(**fields))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# ---- publishing and visibility depend on prices ----------------------------------------------
def test_unpriced_active_variants_are_not_purchasable(db_session):
    product = new_product(db_session, skus=("OF-1", "OF-2"))
    offer(db_session, sku="OF-1")
    ids = [v.id for v in product.variants]
    assert len(service.current_prices(db_session, ids)) == 1


def test_product_disappears_publicly_when_its_price_is_cancelled(db_session):
    product = new_product(db_session)
    code = product.public_code
    priced = offer(db_session)
    service.update_product(db_session, code, ProductUpdate(status="ACTIVE"))
    _, total = service.list_public_products(
        db_session, category_slug="sneakers", q=None, limit=20, offset=0
    )
    assert total == 1
    assert service.get_product(db_session, code, public=True).public_code == code

    service.cancel_offer(db_session, priced.id)
    _, total = service.list_public_products(
        db_session, category_slug="sneakers", q=None, limit=20, offset=0
    )
    assert total == 0
    with pytest.raises(NotFoundError):
        service.get_product(db_session, code, public=True)


def test_cannot_deactivate_last_priced_variant_even_if_unpriced_one_remains(db_session):
    product = new_product(db_session, skus=("OF-1", "OF-2"))
    offer(db_session, sku="OF-1")
    service.update_product(db_session, product.public_code, ProductUpdate(status="ACTIVE"))
    with pytest.raises(UnprocessableError, match="last active"):
        service.update_variant(db_session, "OF-1", VariantUpdate(is_active=False))
