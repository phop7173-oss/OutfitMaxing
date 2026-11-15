import pytest
from pydantic import ValidationError

from app.modules.products.schemas import OfferCreate


@pytest.mark.parametrize("bad", [1500.5, "1500", 0, -5, True, 10_000_000_001])
def test_price_must_be_a_positive_whole_number(bad):
    with pytest.raises(ValidationError):
        OfferCreate(price_mmk=bad)


def test_whole_kyat_is_accepted():
    assert OfferCreate(price_mmk=85_000).price_mmk == 85_000


def test_compare_at_must_be_higher_than_price():
    with pytest.raises(ValidationError):
        OfferCreate(price_mmk=1000, compare_at_price_mmk=1000)
    with pytest.raises(ValidationError):
        OfferCreate(price_mmk=1000, compare_at_price_mmk=900)
    assert OfferCreate(price_mmk=1000, compare_at_price_mmk=1200).compare_at_price_mmk == 1200


def test_datetimes_must_carry_a_timezone():
    with pytest.raises(ValidationError):
        OfferCreate(price_mmk=1000, starts_at="2031-01-01T00:00:00")
    assert OfferCreate(price_mmk=1000, starts_at="2031-01-01T00:00:00+06:30").starts_at


def test_end_must_be_after_start():
    with pytest.raises(ValidationError):
        OfferCreate(
            price_mmk=1000,
            starts_at="2031-02-01T00:00:00+06:30",
            ends_at="2031-01-01T00:00:00+06:30",
        )


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        OfferCreate(price_mmk=1000, currency="USD")
