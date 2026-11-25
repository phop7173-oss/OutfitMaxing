"""Inventory admin endpoints and public availability labels (real PostgreSQL, rolled back)."""

import pytest

from app.core.config import Settings, get_settings
from app.modules.inventory import service as inventory_service

pytestmark = pytest.mark.db

ADMIN_KEY = "test-admin-key-0123456789"
H = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture
def api(db_client):
    db_client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, admin_api_key=ADMIN_KEY
    )
    return db_client


def published_variant(api, sku="INV-1", mode="STOCK", slug="sneakers"):
    api.post("/admin/catalog/categories", json={"name": slug.title(), "slug": slug}, headers=H)
    created = api.post(
        "/admin/catalog/products",
        json={"name": "Air Runner", "category_slug": slug, "fulfilment_mode": mode},
        headers=H,
    )
    code = created.json()["public_code"]
    api.post(f"/admin/catalog/products/{code}/variants", json={"sku": sku, "size": "42"}, headers=H)
    api.post(f"/admin/catalog/variants/{sku}/offers", json={"price_mmk": 85_000}, headers=H)
    api.patch(f"/admin/catalog/products/{code}", json={"status": "ACTIVE"}, headers=H)
    return code


def receive(api, sku, quantity, **kw):
    return api.post(
        f"/admin/inventory/variants/{sku}/receive", json={"quantity": quantity, **kw}, headers=H
    )


def public_label(api, code):
    return api.get(f"/catalog/products/{code}").json()["variants"][0]["availability"]


def test_inventory_endpoints_require_the_admin_key(api):
    assert api.get("/admin/inventory/variants/INV-1").status_code == 401
    body = {"quantity": 1}
    assert api.post("/admin/inventory/variants/INV-1/receive", json=body).status_code == 401
    assert api.get("/admin/inventory/variants/INV-1/movements").status_code == 401


def test_receive_adjust_and_movements(api):
    published_variant(api)
    r = receive(api, "INV-1", 10, note="first delivery")
    assert r.status_code == 201
    assert r.json()["total_on_hand"] == 10 and r.json()["total_available"] == 10

    adjusted = api.post(
        "/admin/inventory/variants/INV-1/adjust",
        json={"delta": -2, "note": "damaged in storage"},
        headers=H,
    )
    assert adjusted.status_code == 200
    assert adjusted.json()["locations"][0] == {
        "location_code": "main",
        "location_name": "Main",
        "on_hand": 8,
        "reserved": 0,
        "available": 8,
    }
    log = api.get("/admin/inventory/variants/INV-1/movements", headers=H).json()
    assert [m["reason"] for m in log] == ["ADJUST", "RECEIVE"]
    assert log[0]["delta_on_hand"] == -2 and log[0]["note"] == "damaged in storage"


def test_adjustment_below_reserved_stock_is_refused(api, db_session):
    published_variant(api)
    receive(api, "INV-1", 5)
    variant = inventory_service.get_variant_by_sku(db_session, "INV-1")
    inventory_service.reserve_stock(db_session, variant.id, 4, ttl_minutes=15)
    r = api.post(
        "/admin/inventory/variants/INV-1/adjust",
        json={"delta": -2, "note": "recount"},
        headers=H,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unprocessable"
    assert api.get("/admin/inventory/variants/INV-1", headers=H).json()["total_reserved"] == 4


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("receive", {"quantity": 0}),
        ("receive", {"quantity": 1.5}),
        ("receive", {"quantity": "5"}),
        ("receive", {"quantity": 5, "location_code": "Bad Code!"}),
        ("adjust", {"delta": -1}),
        ("adjust", {"delta": 0, "note": "nothing changes"}),
        ("adjust", {"delta": 2.5, "note": "recount"}),
    ],
)
def test_invalid_stock_requests_are_rejected(api, path, body):
    published_variant(api)
    r = api.post(f"/admin/inventory/variants/INV-1/{path}", json=body, headers=H)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_unknown_sku_and_location(api):
    published_variant(api)
    assert api.get("/admin/inventory/variants/NOPE-1", headers=H).status_code == 404
    assert receive(api, "NOPE-1", 1).status_code == 404
    assert receive(api, "INV-1", 1, location_code="nowhere").status_code == 404


def test_public_availability_labels_follow_stock_without_exact_numbers(api, db_session):
    code = published_variant(api)
    assert public_label(api, code) == "out_of_stock"  # visible, but not available
    receive(api, "INV-1", 10)
    assert public_label(api, code) == "in_stock"

    variant = inventory_service.get_variant_by_sku(db_session, "INV-1")
    inventory_service.reserve_stock(db_session, variant.id, 7, ttl_minutes=15)
    assert public_label(api, code) == "low_stock"
    inventory_service.reserve_stock(db_session, variant.id, 3, ttl_minutes=15)
    assert public_label(api, code) == "out_of_stock"

    public_text = api.get(f"/catalog/products/{code}").text
    assert "on_hand" not in public_text and "reserved" not in public_text
    listing = api.get("/catalog/products").json()
    assert listing["total"] == 1  # an out-of-stock variant stays visible


def test_preorder_products_are_never_shown_as_in_stock(api):
    code = published_variant(api, sku="PRE-1", mode="PREORDER", slug="preorders")
    receive(api, "PRE-1", 50)
    assert public_label(api, code) == "preorder_closed"  # no supplier capacity, never in stock
