"""Catalog API tests (FastAPI + real PostgreSQL, rolled back after each test)."""

import pytest

from app.core.config import Settings, get_settings

pytestmark = pytest.mark.db

ADMIN_KEY = "test-admin-key-0123456789"
H = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture
def api(db_client):
    db_client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, admin_api_key=ADMIN_KEY
    )
    return db_client


def create_category(api, slug="sneakers", name="Sneakers"):
    return api.post("/admin/catalog/categories", json={"name": name, "slug": slug}, headers=H)


def create_product(api, name="Air Runner", category="sneakers"):
    r = api.post(
        "/admin/catalog/products",
        json={"name": name, "category_slug": category, "fulfilment_mode": "STOCK"},
        headers=H,
    )
    assert r.status_code == 201, r.text
    return r.json()["public_code"]


def create_variant(api, code, sku, **kw):
    r = api.post(f"/admin/catalog/products/{code}/variants", json={"sku": sku, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


# ---- authentication -------------------------------------------------------------------------
def test_admin_requires_a_valid_key(api):
    body = {"name": "Sneakers", "slug": "sneakers"}
    missing = api.post("/admin/catalog/categories", json=body)
    wrong = api.post("/admin/catalog/categories", json=body, headers={"X-Admin-Key": "wrong"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["error"]["code"] == "unauthorized"


def test_admin_is_disabled_when_no_key_is_configured(db_client):
    db_client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, admin_api_key=None
    )
    r = db_client.post(
        "/admin/catalog/categories", json={"name": "Sneakers", "slug": "sneakers"}, headers=H
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "service_unavailable"


def test_public_endpoints_need_no_key(api):
    assert api.get("/catalog/categories").status_code == 200
    assert api.get("/catalog/products").status_code == 200


# ---- end-to-end flow ------------------------------------------------------------------------
def test_create_publish_and_browse_flow(api):
    assert create_category(api).status_code == 201
    code = create_product(api)

    assert api.get(f"/catalog/products/{code}").status_code == 404  # draft is invisible
    create_variant(api, code, "AR-42-BLK", size="42", color="black")
    create_variant(api, code, "AR-43-BLK", size="43", color="black")

    published = api.patch(f"/admin/catalog/products/{code}", json={"status": "ACTIVE"}, headers=H)
    assert published.status_code == 200
    assert published.json()["status"] == "ACTIVE"

    detail = api.get(f"/catalog/products/{code}")
    assert detail.status_code == 200
    assert {v["sku"] for v in detail.json()["variants"]} == {"AR-42-BLK", "AR-43-BLK"}

    listing = api.get("/catalog/products", params={"category": "sneakers"}).json()
    assert listing["total"] == 1 and listing["items"][0]["public_code"] == code
    assert api.get("/catalog/products", params={"q": "runner"}).json()["total"] == 1


def test_inactive_variants_and_their_images_are_hidden_publicly(api):
    create_category(api)
    code = create_product(api)
    create_variant(api, code, "AR-42", size="42")
    create_variant(api, code, "AR-43", size="43")
    api.patch(f"/admin/catalog/products/{code}", json={"status": "ACTIVE"}, headers=H)
    api.post(
        f"/admin/catalog/products/{code}/images",
        json={"url": "https://example.com/all.jpg"},
        headers=H,
    )
    api.post(
        f"/admin/catalog/products/{code}/images",
        json={"url": "https://example.com/43.jpg", "variant_sku": "AR-43"},
        headers=H,
    )
    off = api.patch("/admin/catalog/variants/AR-43", json={"is_active": False}, headers=H)
    assert off.status_code == 200

    public = api.get(f"/catalog/products/{code}").json()
    assert [v["sku"] for v in public["variants"]] == ["AR-42"]
    assert [i["url"] for i in public["images"]] == ["https://example.com/all.jpg"]
    admin = api.get(f"/admin/catalog/products/{code}", headers=H).json()
    assert len(admin["variants"]) == 2 and len(admin["images"]) == 2


# ---- errors ---------------------------------------------------------------------------------
def test_validation_errors_use_the_envelope_and_do_not_echo_input(api):
    r = api.post(
        "/admin/catalog/categories", json={"name": "Shoes", "slug": "Bad Slug!"}, headers=H
    )
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"] == r.headers["X-Request-ID"]
    assert "Bad Slug!" not in r.text


def test_duplicate_returns_409_with_request_id(api):
    create_category(api)
    r = create_category(api)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"]


def test_excluded_category_is_rejected(api):
    r = create_category(api, slug="smart-watches", name="Smart Watches")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unprocessable"


def test_prices_are_not_part_of_the_catalog_yet(api):
    create_category(api)
    r = api.post(
        "/admin/catalog/products",
        json={
            "name": "X",
            "category_slug": "sneakers",
            "fulfilment_mode": "STOCK",
            "price_mmk": 5000,
        },
        headers=H,
    )
    assert r.status_code == 422


def test_unknown_product_is_404(api):
    assert api.get("/catalog/products/OM-P-999999").status_code == 404
    assert api.get("/admin/catalog/products/OM-P-999999", headers=H).status_code == 404


def test_paging_parameters_are_validated(api):
    assert api.get("/catalog/products", params={"limit": 1000}).status_code == 422
    assert api.get("/catalog/products", params={"offset": -1}).status_code == 422


def test_public_responses_do_not_expose_internal_ids(api):
    create_category(api)
    code = create_product(api)
    create_variant(api, code, "AR-42", size="42")
    api.patch(f"/admin/catalog/products/{code}", json={"status": "ACTIVE"}, headers=H)
    category = api.get("/catalog/categories").json()[0]
    product = api.get(f"/catalog/products/{code}").json()
    assert set(category) == {"name", "slug", "parent_slug", "sort_order"}
    assert "id" not in product and all("id" not in v for v in product["variants"])
