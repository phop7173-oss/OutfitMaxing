import pytest
from pydantic import ValidationError

from app.modules.inventory.schemas import StockAdjust, StockReceive
from app.modules.inventory.service import LOW_STOCK_THRESHOLD, availability_label


@pytest.mark.parametrize("bad", [0, -1, 1.5, "5", True, 1_000_001])
def test_receive_quantity_must_be_a_positive_whole_number(bad):
    with pytest.raises(ValidationError):
        StockReceive(quantity=bad)


def test_receive_defaults_to_the_main_location():
    assert StockReceive(quantity=5).location_code == "main"


def test_location_code_is_validated():
    with pytest.raises(ValidationError):
        StockReceive(quantity=5, location_code="Main Warehouse!")


def test_adjust_needs_a_real_note_and_a_nonzero_whole_delta():
    with pytest.raises(ValidationError):
        StockAdjust(delta=-1)
    with pytest.raises(ValidationError):
        StockAdjust(delta=-1, note="x")
    with pytest.raises(ValidationError):
        StockAdjust(delta=0, note="recount")
    with pytest.raises(ValidationError):
        StockAdjust(delta=1.5, note="recount")
    assert StockAdjust(delta=-2, note="damaged in storage").delta == -2


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        StockReceive(quantity=5, price_mmk=100)


def test_availability_labels():
    assert availability_label("STOCK", 0) == "out_of_stock"
    assert availability_label("STOCK", -4) == "out_of_stock"
    assert availability_label("STOCK", 1) == "low_stock"
    assert availability_label("STOCK", LOW_STOCK_THRESHOLD) == "low_stock"
    assert availability_label("STOCK", LOW_STOCK_THRESHOLD + 1) == "in_stock"
    assert availability_label("PREORDER", 100) == "preorder_closed"
    assert availability_label("PREORDER", 0, preorder_open=True) == "preorder"
