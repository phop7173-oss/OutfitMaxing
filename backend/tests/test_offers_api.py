"""Offer endpoints (FastAPI + real PostgreSQL, rolled back after each test)."""

import uuid

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


def new_variant(api, sku="AR-42"):
    api.post("/admin/catalog/categories", json={"name": "Sneakers", "slug": "sneakers"}, headers=H)
    created = api.post(
        "/admin/catalog/products",
        json={"name": "Air Runner", "category_slug": "sneakers", "fulfilment_mode": "STOCK"},
        headers=H,
    )
    code = created.json()["public_code"]
    api.post(f"/admin/catalog/products/{code}/variants", json={"sku": sku, "size": "42"}, headers=H)
    return code


def post_offer(api, sku, **body):
    return api.post(f"/admin/catalog/variants/{sku}/offers", json=body, headers=H)


def test_offer_endpoints_require_the_admin_key(api):
    new_variant(api)
    body = {"price_mmk": 1000}
    assert api.post("/admin/catalog/variants/AR-42/offers", json=body).status_code == 401
    assert api.get("/admin/catalog/variants/AR-42/offers").status_code == 401
    assert api.post(f"/admin/catalog/offers/{uuid.uuid4()}/cancel").status_code == 401


def test_create_list_and_cancel_offers_and_public_visibility(api):
    code = new_variant(api)
    created = post_offer(api, "AR-42", price_mmk=85_000, compare_at_price_mmk=100_000)
    assert created.status_code == 201
    body = created.json()
    assert body["sku"] == "AR-42" and body["is_current"] is True and body["status"] == "ACTIVE"

    api.patch(f"/admin/catalog/products/{code}", json={"status": "ACTIVE"}, headers=H)
    public = api.get(f"/catalog/products/{code}").json()
    assert public["variants"][0]["price_mmk"] == 85_000
    assert public["variants"][0]["compare_at_price_mmk"] == 100_000
    listing = api.get("/catalog/products").json()
    assert listing["items"][0]["variants"][0]["price_mmk"] == 85_000

    history = api.get("/admin/catalog/variants/AR-42/offers", headers=H).json()
    assert [o["price_mmk"] for o in history] == [85_000]

    cancelled = api.post(f"/admin/catalog/offers/{body['id']}/cancel", headers=H)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["is_current"] is False
    assert api.get(f"/catalog/products/{code}").status_code == 404  # no price, not purchasable
    admin_view = api.get(f"/admin/catalog/products/{code}", headers=H).json()
    assert admin_view["variants"][0]["price_mmk"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"price_mmk": 1500.5},
        {"price_mmk": "1500"},
        {"price_mmk": 0},
        {"price_mmk": -5},
        {"price_mmk": True},
        {"price_mmk": 1000, "compare_at_price_mmk": 1000},
        {"price_mmk": 1000, "starts_at": "2031-01-01T00:00:00"},
        {
            "price_mmk": 1000,
            "starts_at": "2031-02-01T00:00:00+06:30",
            "ends_at": "2031-01-01T00:00:00+06:30",
        },
        {"price_mmk": 1000, "currency": "USD"},
    ],
)
def test_invalid_prices_are_rejected(api, bad):
    new_variant(api)
    r = post_offer(api, "AR-42", **bad)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_overlapping_offer_is_a_conflict(api):
    new_variant(api)
    first = post_offer(api, "AR-42", price_mmk=1000, starts_at="2031-01-01T00:00:00+06:30")
    assert first.status_code == 201
    r = post_offer(api, "AR-42", price_mmk=900, starts_at="2031-02-01T00:00:00+06:30")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_unknown_sku_and_bad_offer_id(api):
    assert post_offer(api, "NOPE-1", price_mmk=1000).status_code == 404
    assert api.get("/admin/catalog/variants/NOPE-1/offers", headers=H).status_code == 404
    assert api.post(f"/admin/catalog/offers/{uuid.uuid4()}/cancel", headers=H).status_code == 404
    assert api.post("/admin/catalog/offers/not-a-uuid/cancel", headers=H).status_code == 422


def test_unpriced_variants_are_hidden_from_the_public(api):
    code = new_variant(api)
    api.post(
        f"/admin/catalog/products/{code}/variants", json={"sku": "AR-43", "size": "43"}, headers=H
    )
    post_offer(api, "AR-42", price_mmk=85_000)
    api.patch(f"/admin/catalog/products/{code}", json={"status": "ACTIVE"}, headers=H)
    public = api.get(f"/catalog/products/{code}").json()
    assert [v["sku"] for v in public["variants"]] == ["AR-42"]
    admin_view = api.get(f"/admin/catalog/products/{code}", headers=H).json()
    assert {v["sku"]: v["price_mmk"] for v in admin_view["variants"]} == {
        "AR-42": 85_000,
        "AR-43": None,
    }
