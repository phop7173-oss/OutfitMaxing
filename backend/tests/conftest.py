import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.database.session import get_db

os.environ.setdefault("APP_ENV", "test")


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture(scope="session")
def _db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs TEST_DATABASE_URL (real PostgreSQL)")
    engine = create_engine(url)
    if not inspect(engine).has_table("inventory_movements"):
        pytest.fail("Test database has no schema: run `alembic upgrade head` against it first.")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_db_engine):
    """A session whose work is rolled back after each test (service commits become savepoints)."""
    connection = _db_engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    yield session
    session.close()
    outer.rollback()
    connection.close()


@pytest.fixture
def db_client(client, db_session):
    client.app.dependency_overrides[get_db] = lambda: db_session
    yield client
    client.app.dependency_overrides.clear()


@pytest.fixture
def make_committed_variant(_db_engine):
    """Factory: a published, priced STOCK variant with real committed rows, for concurrency
    tests. Returns (variant_id, sku). All rows it created are deleted at teardown."""
    from app.modules.inventory import service as inventory_service
    from app.modules.products import service as catalog_service
    from app.modules.products.schemas import (
        CategoryCreate,
        OfferCreate,
        ProductCreate,
        ProductUpdate,
        VariantCreate,
    )

    created: list[tuple[str, str]] = []

    def make(stock: int = 0):
        suffix = uuid.uuid4().hex[:8]
        slug, sku = f"t-{suffix}", f"T-{suffix}"
        created.append((slug, sku))
        with Session(_db_engine, expire_on_commit=False) as s:
            catalog_service.create_category(s, CategoryCreate(name=f"Test {suffix}", slug=slug))
            product = catalog_service.create_product(
                s, ProductCreate(name="Race item", category_slug=slug, fulfilment_mode="STOCK")
            )
            product = catalog_service.add_variant(
                s, product.public_code, VariantCreate(sku=sku, size="42")
            )
            variant_id = product.variants[0].id
            catalog_service.create_offer(s, sku, OfferCreate(price_mmk=10_000))
            catalog_service.update_product(s, product.public_code, ProductUpdate(status="ACTIVE"))
            if stock:
                inventory_service.receive_stock(s, sku, stock, note="test stock")
        return variant_id, sku

    yield make

    statements = [
        "DELETE FROM inventory_movements WHERE variant_id IN "
        "(SELECT id FROM product_variants WHERE sku = :sku)",
        "DELETE FROM inventory_reservations WHERE variant_id IN "
        "(SELECT id FROM product_variants WHERE sku = :sku)",
        "DELETE FROM inventory_balances WHERE variant_id IN "
        "(SELECT id FROM product_variants WHERE sku = :sku)",
        "DELETE FROM offers WHERE variant_id IN "
        "(SELECT id FROM product_variants WHERE sku = :sku)",
        "DELETE FROM product_variants WHERE sku = :sku",
        "DELETE FROM products WHERE category_id IN "
        "(SELECT id FROM product_categories WHERE slug = :slug)",
        "DELETE FROM product_categories WHERE slug = :slug",
    ]
    with _db_engine.begin() as conn:
        for slug, sku in created:
            for statement in statements:
                conn.execute(text(statement), {"slug": slug, "sku": sku})
