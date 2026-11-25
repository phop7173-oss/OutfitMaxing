"""Supplier, preorder and sourcing admin API and public preorder labels (real PostgreSQL)."""

import pytest

from app.core.config import Settings, get_settings
from app.modules.inventory import service as inventory_service
from app.modules.suppliers import service as supplier_service

pytestmark = pytest.mark.db

ADMIN_KEY = "test-admin-key-0123456789"
H = {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture
def api(db_client):
    db_client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, admin_api_key=ADMIN_KEY
    )
    return db_client


def preorder_product(api, sku="PRE-1", slug="preorders"):
    api.post("/admin/catalog/categories", json={"name": slug.title(), "slug": slug}, headers=H)
    created = api.post(
        "/admin/catalog/products",
        json={"name": "Imported Jacket", "category_slug": slug, "fulfilment_mode": "PREORDER"},
        headers=H,
    )
    code = created.json()["public_code"]
    api.post(f"/admin/catalog/products/{code}/variants", json={"sku": sku, "size": "M"}, headers=H)
    api.post(f"/admin/catalog/variants/{sku}/offers", json={"price_mmk": 250_000}, headers=H)
    api.patch(f"/admin/catalog/products/{code}", json={"status": "ACTIVE"}, headers=H)
    return code


def make_supplier(api, code="acme"):
    return api.post("/admin/suppliers", json={"code": code, "name": "Acme Imports"}, headers=H)


def make_product_entry(api, sku="PRE-1", code="acme", capacity=5, **kw):
    body = {"sku": sku, "lead_days_min": 10, "lead_days_max": 20, "capacity": capacity, **kw}
    return api.post(f"/admin/suppliers/{code}/products", json=body, headers=H)


def public_variant(api, code):
    return api.get(f"/catalog/products/{code}").json()["variants"][0]


def test_supplier_endpoints_require_the_admin_key(api):
    assert api.get("/admin/suppliers").status_code == 401
    assert api.post("/admin/suppliers", json={"code": "acme", "name": "Acme"}).status_code == 401
    assert api.get("/admin/preorders/commitments").status_code == 401
    assert api.post("/admin/sourcing/requests", json={}).status_code == 401
    assert api.get("/admin/sourcing/unconfirmed").status_code == 401


def test_supplier_and_supplier_product_lifecycle(api, db_session):
    preorder_product(api)
    assert make_supplier(api).status_code == 201
    assert make_supplier(api).status_code == 409
    assert [s["code"] for s in api.get("/admin/suppliers", headers=H).json()] == ["acme"]

    created = make_product_entry(api, unit_cost_mmk=120_000, supplier_sku="AC-77")
    assert created.status_code == 201
    entry = created.json()
    assert entry["available_capacity"] == 5 and entry["unit_cost_mmk"] == 120_000
    assert entry["last_reported_available"] is None

    updated = api.patch(
        f"/admin/suppliers/products/{entry['id']}", json={"capacity": 8}, headers=H
    )
    assert updated.status_code == 200 and updated.json()["capacity"] == 8

    variant = inventory_service.get_variant_by_sku(db_session, "PRE-1")
    supplier_service.commit_capacity(db_session, variant.id, 3)
    too_low = api.patch(
        f"/admin/suppliers/products/{entry['id']}", json={"capacity": 2}, headers=H
    )
    assert too_low.status_code == 422 and too_low.json()["error"]["code"] == "unprocessable"

    check = api.post(
        f"/admin/suppliers/products/{entry['id']}/stock-checks",
        json={"available_quantity": 40, "note": "phoned the supplier"},
        headers=H,
    )
    assert check.status_code == 201 and check.json()["last_reported_available"] == 40
    listed = api.get("/admin/suppliers/acme/products", headers=H).json()
    assert listed[0]["committed"] == 3 and listed[0]["available_capacity"] == 5


@pytest.mark.parametrize(
    "bad",
    [
        {"capacity": 1.5},
        {"capacity": "5"},
        {"capacity": -1},
        {"lead_days_min": 30, "lead_days_max": 10},
        {"unknown_field": 1},
    ],
)
def test_invalid_supplier_product_requests_are_rejected(api, bad):
    preorder_product(api)
    make_supplier(api)
    r = make_product_entry(api, **bad)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_unknown_supplier_sku_and_ids(api):
    preorder_product(api)
    assert make_product_entry(api, code="nobody").status_code == 404
    make_supplier(api)
    assert make_product_entry(api, sku="NOPE-1").status_code == 404
    random_id = "00000000-0000-4000-8000-000000000000"
    assert api.patch(
        f"/admin/suppliers/products/{random_id}", json={"capacity": 1}, headers=H
    ).status_code == 404
    assert api.post(f"/admin/sourcing/requests/{random_id}/ship", headers=H).status_code == 404
    assert api.post("/admin/sourcing/requests/not-a-uuid/ship", headers=H).status_code == 422
    bad_body = {"supplier_product_id": "nope"}
    assert api.post("/admin/sourcing/requests", json=bad_body, headers=H).status_code == 422


def test_full_preorder_flow_from_request_to_surplus(api, db_session):
    preorder_product(api)
    make_supplier(api)
    entry = make_product_entry(api, capacity=10).json()
    variant = inventory_service.get_variant_by_sku(db_session, "PRE-1")
    commitment = supplier_service.commit_capacity(
        db_session, variant.id, 2, reference_type="order_item", reference_id="oi-1"
    )
    cid = str(commitment.id)

    waiting = api.get("/admin/preorders/commitments", params={"status": "HELD"}, headers=H)
    assert [c["id"] for c in waiting.json()] == [cid]
    body = {"supplier_product_id": entry["id"]}
    assert api.post("/admin/sourcing/requests", json=body, headers=H).status_code == 422

    verified = api.post(f"/admin/preorders/commitments/{cid}/deposit-verified", headers=H)
    assert verified.json()["deposit_verified"] is True
    request = api.post("/admin/sourcing/requests", json={**body, "note": "batch 1"}, headers=H)
    assert request.status_code == 201 and request.json()["quantity"] == 2
    rid = request.json()["id"]

    assert api.post(f"/admin/sourcing/requests/{rid}/ship", headers=H).status_code == 409
    window = {"expected_from": "2031-02-01", "expected_to": "2031-02-15"}
    confirmed = api.post(f"/admin/sourcing/requests/{rid}/confirm", json=window, headers=H)
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "CONFIRMED"
    assert api.post(f"/admin/sourcing/requests/{rid}/ship", headers=H).json()["status"] == "SHIPPED"

    received = api.post(
        f"/admin/sourcing/requests/{rid}/receive", json={"received_quantity": 3}, headers=H
    ).json()
    assert received["allocated_quantity"] == 2 and received["surplus_remaining"] == 1

    done = api.get("/admin/preorders/commitments", params={"status": "FULFILLED"}, headers=H)
    assert len(done.json()) == 1 and done.json()[0]["expected_from"] == "2031-02-01"

    moved = api.post(
        f"/admin/sourcing/requests/{rid}/move-surplus-to-stock", json={"quantity": 1}, headers=H
    )
    assert moved.status_code == 200 and moved.json()["surplus_remaining"] == 0
    assert api.get("/admin/inventory/variants/PRE-1", headers=H).json()["total_on_hand"] == 1
    entry_now = api.get("/admin/suppliers/acme/products", headers=H).json()[0]
    assert entry_now["capacity"] == 8 and entry_now["committed"] == 0


def test_alert_endpoints_report_counts(api):
    assert api.post("/admin/preorders/flag-overdue", headers=H).json() == {"flagged": 0}
    assert api.post("/admin/sourcing/flag-unconfirmed", headers=H).json() == {"alerted": 0}
    assert api.get("/admin/sourcing/unconfirmed", headers=H).json() == []


def test_public_preorder_label_follows_capacity_and_shows_lead_days(api, db_session):
    code = preorder_product(api)
    assert public_variant(api, code)["availability"] == "preorder_closed"  # no supplier yet
    make_supplier(api)
    make_product_entry(api, capacity=2)
    shown = public_variant(api, code)
    assert shown["availability"] == "preorder"
    assert (shown["preorder_lead_days_min"], shown["preorder_lead_days_max"]) == (10, 20)

    variant = inventory_service.get_variant_by_sku(db_session, "PRE-1")
    supplier_service.commit_capacity(db_session, variant.id, 2)
    closed = public_variant(api, code)
    assert closed["availability"] == "preorder_closed"
    assert closed["preorder_lead_days_min"] is None
    assert "capacity" not in api.get(f"/catalog/products/{code}").text
