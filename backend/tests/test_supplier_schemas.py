import pytest
from pydantic import ValidationError

from app.modules.suppliers.schemas import (
    ConfirmRequest,
    ReceiveRequest,
    SourcingCreate,
    SupplierCreate,
    SupplierProductCreate,
    SupplierProductUpdate,
)


@pytest.mark.parametrize("code", ["A", "Acme", "has space", "under_score", "-x", "x-"])
def test_supplier_codes_are_validated(code):
    with pytest.raises(ValidationError):
        SupplierCreate(code=code, name="Acme")


def test_good_supplier():
    assert SupplierCreate(code="acme-imports", name="Acme Imports").code == "acme-imports"


def test_lead_days_window_and_strict_whole_numbers():
    ok = SupplierProductCreate(sku="PRE-1", lead_days_min=10, lead_days_max=20, capacity=5)
    assert ok.capacity == 5
    with pytest.raises(ValidationError):
        SupplierProductCreate(sku="PRE-1", lead_days_min=20, lead_days_max=10, capacity=5)
    for bad in (-1, 1.5, "5", True):
        with pytest.raises(ValidationError):
            SupplierProductCreate(sku="PRE-1", lead_days_min=1, lead_days_max=2, capacity=bad)


def test_update_cannot_null_required_fields():
    with pytest.raises(ValidationError):
        SupplierProductUpdate(capacity=None)
    with pytest.raises(ValidationError):
        SupplierProductUpdate(status="PAUSED")
    assert SupplierProductUpdate(unit_cost_mmk=None).model_fields_set == {"unit_cost_mmk"}


def test_confirm_window_must_be_ordered_and_dates_valid():
    with pytest.raises(ValidationError):
        ConfirmRequest(expected_from="2031-02-10", expected_to="2031-02-01")
    with pytest.raises(ValidationError):
        ConfirmRequest(expected_from="not-a-date", expected_to="2031-02-01")
    assert ConfirmRequest(expected_from="2031-02-01", expected_to="2031-02-01")


def test_sourcing_and_receive_inputs():
    with pytest.raises(ValidationError):
        SourcingCreate(supplier_product_id="not-a-uuid")
    with pytest.raises(ValidationError):
        ReceiveRequest(received_quantity=0)
    with pytest.raises(ValidationError):
        ReceiveRequest(received_quantity=2.5)
    assert ReceiveRequest(received_quantity=3).received_quantity == 3


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        SupplierCreate(code="acme", name="Acme", rating=5)
