"""Catalog service tests against real PostgreSQL (rolled back after each test)."""

import re

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.modules.products import service
from app.modules.products.models import ProductImage
from app.modules.products.schemas import (
    CategoryCreate,
    ImageCreate,
    ProductCreate,
    ProductUpdate,
    VariantCreate,
    VariantUpdate,
)

pytestmark = pytest.mark.db


def make_category(db, slug="sneakers", name="Sneakers", **kw):
    return service.create_category(db, CategoryCreate(name=name, slug=slug, **kw))


def make_product(db, category="sneakers", name="Air Runner", mode="STOCK", **kw):
    data = ProductCreate(name=name, category_slug=category, fulfilment_mode=mode, **kw)
    return service.create_product(db, data)


def add_variant(db, code, sku, **kw):
    return service.add_variant(db, code, VariantCreate(sku=sku, **kw))


def set_status(db, code, status):
    return service.update_product(db, code, ProductUpdate(status=status))


# ---- categories -----------------------------------------------------------------------------
def test_create_category_and_duplicate_slug_conflict(db_session):
    make_category(db_session)
    with pytest.raises(ConflictError, match="slug"):
        make_category(db_session)


def test_child_category_and_unknown_parent(db_session):
    make_category(db_session, slug="shoes", name="Shoes")
    child = make_category(db_session, slug="boots", name="Boots", parent_slug="shoes")
    assert child.parent_id is not None
    with pytest.raises(NotFoundError):
        make_category(db_session, slug="kids", name="Kids", parent_slug="does-not-exist")


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Watches", "watches"),
        ("Smart Gadgets", "electronics"),
        ("Skin Care", "cosmetics"),
        ("Power", "battery-packs"),
        ("Beauty", "makeup-kits"),
    ],
)
def test_excluded_categories_are_rejected(db_session, name, slug):
    with pytest.raises(UnprocessableError, match="not allowed"):
        make_category(db_session, slug=slug, name=name)


@pytest.mark.parametrize(
    ("name", "slug"),
    [("Sneakers", "sneakers"), ("Wallets", "wallets"), ("Caps", "caps"), ("Belts", "belts")],
)
def test_allowed_categories_are_accepted(db_session, name, slug):
    assert make_category(db_session, slug=slug, name=name).slug == slug


def test_is_excluded_category_is_token_based():
    assert service.is_excluded_category("Watches", "watches")
    assert not service.is_excluded_category("Watermelon Print Tees", "watermelon-tees")


# ---- products -------------------------------------------------------------------------------
def test_public_codes_are_unique_and_formatted(db_session):
    make_category(db_session)
    a = make_product(db_session)
    b = make_product(db_session, name="Air Walker")
    assert re.fullmatch(r"OM-P-\d{6}", a.public_code)
    assert a.public_code != b.public_code
    assert a.status == "DRAFT"


def test_product_requires_existing_category(db_session):
    with pytest.raises(NotFoundError):
        make_product(db_session, category="nope")


def test_update_product_fields_and_clear_optional(db_session):
    make_category(db_session)
    p = make_product(db_session, description="old", brand="Acme")
    updated = service.update_product(
        db_session, p.public_code, ProductUpdate(name="New Name", description=None)
    )
    assert updated.name == "New Name"
    assert updated.description is None
    assert updated.brand == "Acme"


# ---- variants -------------------------------------------------------------------------------
def test_duplicate_sku_and_duplicate_size_color_are_conflicts(db_session):
    make_category(db_session)
    code = make_product(db_session).public_code
    add_variant(db_session, code, "SKU-1", size="42", color="black")
    with pytest.raises(ConflictError, match="SKU"):
        add_variant(db_session, code, "SKU-1", size="43")
    with pytest.raises(ConflictError, match="same size and color"):
        add_variant(db_session, code, "SKU-2", size="42", color="black")
    product = add_variant(db_session, code, "SKU-3", size="42", color="white")
    assert {v.sku for v in product.variants} == {"SKU-1", "SKU-3"}


def test_two_variants_without_size_or_color_conflict(db_session):
    make_category(db_session, slug="wallets", name="Wallets")
    code = make_product(db_session, category="wallets", name="Slim Wallet").public_code
    add_variant(db_session, code, "WAL-1")
    with pytest.raises(ConflictError, match="same size and color"):
        add_variant(db_session, code, "WAL-2")


def test_same_size_and_color_allowed_on_different_products(db_session):
    make_category(db_session)
    c1 = make_product(db_session).public_code
    c2 = make_product(db_session, name="Other").public_code
    add_variant(db_session, c1, "A-1", size="42", color="black")
    add_variant(db_session, c2, "B-1", size="42", color="black")


# ---- status rules ---------------------------------------------------------------------------
def test_publish_needs_an_active_variant(db_session):
    make_category(db_session)
    code = make_product(db_session).public_code
    with pytest.raises(UnprocessableError, match="active variant"):
        set_status(db_session, code, "ACTIVE")
    add_variant(db_session, code, "SKU-1", size="42")
    assert set_status(db_session, code, "ACTIVE").status == "ACTIVE"


def test_status_transitions_are_whitelisted(db_session):
    make_category(db_session)
    code = make_product(db_session).public_code
    add_variant(db_session, code, "SKU-1", size="42")
    set_status(db_session, code, "ACTIVE")
    assert set_status(db_session, code, "ARCHIVED").status == "ARCHIVED"
    with pytest.raises(UnprocessableError, match="Cannot change status"):
        set_status(db_session, code, "ACTIVE")
    assert set_status(db_session, code, "DRAFT").status == "DRAFT"


def test_cannot_deactivate_last_active_variant_of_active_product(db_session):
    make_category(db_session)
    code = make_product(db_session).public_code
    add_variant(db_session, code, "SKU-1", size="42")
    add_variant(db_session, code, "SKU-2", size="43")
    set_status(db_session, code, "ACTIVE")
    service.update_variant(db_session, "SKU-1", VariantUpdate(is_active=False))
    with pytest.raises(UnprocessableError, match="last active variant"):
        service.update_variant(db_session, "SKU-2", VariantUpdate(is_active=False))


def test_unknown_variant_and_product(db_session):
    with pytest.raises(NotFoundError):
        service.update_variant(db_session, "NOPE-1", VariantUpdate(is_active=False))
    with pytest.raises(NotFoundError):
        service.get_product(db_session, "OM-P-999999", public=False)


# ---- public listing -------------------------------------------------------------------------
def publish(db, name, sku, category="sneakers", **kw):
    code = make_product(db, category=category, name=name, **kw).public_code
    add_variant(db, code, sku, size="42")
    set_status(db, code, "ACTIVE")
    return code


def test_public_listing_shows_only_active_products(db_session):
    make_category(db_session)
    live = publish(db_session, "Live Shoe", "LIVE-1")
    draft = make_product(db_session, name="Draft Shoe").public_code
    archived = publish(db_session, "Old Shoe", "OLD-1")
    set_status(db_session, archived, "ARCHIVED")
    items, total = service.list_public_products(
        db_session, category_slug="sneakers", q=None, limit=20, offset=0
    )
    assert [p.public_code for p in items] == [live]
    assert total == 1
    with pytest.raises(NotFoundError):
        service.get_product(db_session, draft, public=True)


def test_search_matches_name_and_treats_wildcards_literally(db_session):
    make_category(db_session)
    publish(db_session, "Plain Tee", "TEE-1")
    cotton = publish(db_session, "100% Cotton Tee", "TEE-2")
    items, _ = service.list_public_products(
        db_session, category_slug="sneakers", q="cotton", limit=20, offset=0
    )
    assert [p.public_code for p in items] == [cotton]
    items, total = service.list_public_products(
        db_session, category_slug="sneakers", q="%", limit=20, offset=0
    )
    assert total == 1 and items[0].public_code == cotton


def test_pagination_and_category_filter(db_session):
    make_category(db_session)
    make_category(db_session, slug="wallets", name="Wallets")
    for i in range(3):
        publish(db_session, f"Shoe {i}", f"SH-{i}")
    publish(db_session, "Wallet", "WAL-1", category="wallets")
    items, total = service.list_public_products(
        db_session, category_slug="sneakers", q=None, limit=2, offset=0
    )
    assert total == 3 and len(items) == 2
    rest, _ = service.list_public_products(
        db_session, category_slug="sneakers", q=None, limit=2, offset=2
    )
    assert len(rest) == 1
    _, wallets_total = service.list_public_products(
        db_session, category_slug="wallets", q=None, limit=20, offset=0
    )
    assert wallets_total == 1


# ---- images ---------------------------------------------------------------------------------
def test_image_variant_must_belong_to_the_product(db_session):
    make_category(db_session)
    code_a = make_product(db_session).public_code
    code_b = make_product(db_session, name="Other").public_code
    add_variant(db_session, code_b, "B-1", size="42")
    with pytest.raises(UnprocessableError, match="does not belong"):
        service.add_image(
            db_session, code_a, ImageCreate(url="https://example.com/a.jpg", variant_sku="B-1")
        )


def test_image_for_a_variant_is_linked(db_session):
    make_category(db_session)
    code = make_product(db_session).public_code
    add_variant(db_session, code, "A-1", size="42")
    product = service.add_image(
        db_session, code, ImageCreate(url="https://example.com/a.jpg", variant_sku="A-1")
    )
    assert product.images[0].variant.sku == "A-1"


def test_database_blocks_cross_product_variant_images(db_session):
    make_category(db_session)
    p1 = make_product(db_session)
    p2 = make_product(db_session, name="Other")
    p2 = add_variant(db_session, p2.public_code, "B-1", size="42")
    db_session.add(
        ProductImage(
            product_id=p1.id, variant_id=p2.variants[0].id, url="https://example.com/a.jpg"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_database_blocks_image_without_any_source(db_session):
    make_category(db_session)
    p = make_product(db_session)
    db_session.add(ProductImage(product_id=p.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_database_blocks_invalid_status(db_session):
    make_category(db_session)
    p = make_product(db_session)
    p.status = "BOGUS"
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
