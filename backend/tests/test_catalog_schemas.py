import pytest
from pydantic import ValidationError

from app.modules.products.schemas import (
    CategoryCreate,
    ImageCreate,
    ProductCreate,
    ProductUpdate,
    VariantCreate,
)


@pytest.mark.parametrize("slug", ["Sneakers", "a", "has space", "under_score", "-lead", "trail-"])
def test_bad_category_slugs_rejected(slug):
    with pytest.raises(ValidationError):
        CategoryCreate(name="X", slug=slug)


def test_overlong_slug_rejected():
    with pytest.raises(ValidationError):
        CategoryCreate(name="X", slug="x" * 61)


def test_good_slug_accepted():
    assert CategoryCreate(name="Sneakers", slug="sneakers").slug == "sneakers"


@pytest.mark.parametrize("sku", ["a", "-x", "has space", "a/b", "x" * 65])
def test_bad_skus_rejected(sku):
    with pytest.raises(ValidationError):
        VariantCreate(sku=sku)


def test_good_sku_and_optional_size_color():
    v = VariantCreate(sku="NK-AF1-42.BLK")
    assert v.size is None and v.color is None


def test_image_needs_https_url_or_telegram_file():
    with pytest.raises(ValidationError):
        ImageCreate()
    with pytest.raises(ValidationError):
        ImageCreate(url="http://example.com/a.jpg")
    assert ImageCreate(url="https://example.com/a.jpg").url
    assert ImageCreate(telegram_file_id="AgACAgUAAx0").telegram_file_id


def test_unknown_fields_are_rejected_there_is_no_price_yet():
    with pytest.raises(ValidationError):
        ProductCreate(name="x", category_slug="ab", fulfilment_mode="STOCK", price_mmk=1000)


def test_fulfilment_mode_must_be_known():
    with pytest.raises(ValidationError):
        ProductCreate(name="x", category_slug="ab", fulfilment_mode="DROPSHIP")


def test_update_cannot_null_required_fields_but_can_clear_optional_ones():
    with pytest.raises(ValidationError):
        ProductUpdate(name=None)
    with pytest.raises(ValidationError):
        ProductUpdate(status=None)
    assert ProductUpdate(description=None).model_fields_set == {"description"}
    assert ProductUpdate().model_fields_set == set()


def test_attributes_size_limit():
    with pytest.raises(ValidationError):
        ProductCreate(
            name="x", category_slug="ab", fulfilment_mode="STOCK", attributes={"k": "x" * 5000}
        )
