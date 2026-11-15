"""Concurrent offer creation: real commits from separate connections (cleaned up afterwards)."""

import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.modules.products import service
from app.modules.products.schemas import CategoryCreate, OfferCreate, ProductCreate, VariantCreate

pytestmark = pytest.mark.db


def test_only_one_of_many_simultaneous_overlapping_offers_wins(_db_engine):
    suffix = uuid.uuid4().hex[:8]
    slug, sku = f"conc-{suffix}", f"CONC-{suffix}"
    window_start = datetime(2032, 1, 1, tzinfo=UTC)
    workers = 6
    outcomes: list[str] = []
    barrier = threading.Barrier(workers)

    def attempt(amount: int) -> None:
        try:
            with Session(_db_engine, expire_on_commit=False) as session:
                barrier.wait(timeout=20)
                service.create_offer(
                    session, sku, OfferCreate(price_mmk=amount, starts_at=window_start)
                )
            outcomes.append("ok")
        except ConflictError:
            outcomes.append("conflict")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(f"error:{type(exc).__name__}")

    try:
        with Session(_db_engine, expire_on_commit=False) as setup:
            service.create_category(setup, CategoryCreate(name=f"Conc {suffix}", slug=slug))
            product = service.create_product(
                setup, ProductCreate(name="Race", category_slug=slug, fulfilment_mode="STOCK")
            )
            service.add_variant(setup, product.public_code, VariantCreate(sku=sku, size="42"))

        threads = [
            threading.Thread(target=attempt, args=(100_000 - i,)) for i in range(workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert sorted(outcomes) == ["conflict"] * (workers - 1) + ["ok"]
        with _db_engine.connect() as conn:
            active = conn.execute(
                text(
                    "SELECT count(*) FROM offers o JOIN product_variants v ON v.id = o.variant_id "
                    "WHERE v.sku = :sku AND o.status = 'ACTIVE'"
                ),
                {"sku": sku},
            ).scalar_one()
        assert active == 1
    finally:
        with _db_engine.begin() as conn:
            params = {"sku": sku, "slug": slug}
            conn.execute(
                text(
                    "DELETE FROM offers WHERE variant_id IN "
                    "(SELECT id FROM product_variants WHERE sku = :sku)"
                ),
                params,
            )
            conn.execute(text("DELETE FROM product_variants WHERE sku = :sku"), params)
            conn.execute(
                text(
                    "DELETE FROM products WHERE category_id IN "
                    "(SELECT id FROM product_categories WHERE slug = :slug)"
                ),
                params,
            )
            conn.execute(text("DELETE FROM product_categories WHERE slug = :slug"), params)
