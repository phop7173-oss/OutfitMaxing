"""Request/response schemas for the catalog. Inputs reject unknown fields."""

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

Slug = Annotated[
    str, StringConstraints(min_length=2, max_length=60, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
]
Sku = Annotated[
    str, StringConstraints(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
HttpsUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https://\S+$")]

FulfilmentMode = Literal["STOCK", "PREORDER"]
ProductStatus = Literal["DRAFT", "ACTIVE", "ARCHIVED"]


def _check_json(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > 50 or len(json.dumps(value)) > 4000:
        raise ValueError("too large (max 50 keys, 4000 characters)")
    return value


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryCreate(_Input):
    name: ShortText
    slug: Slug
    parent_slug: Slug | None = None
    sort_order: int = 0


class ProductCreate(_Input):
    name: Name
    category_slug: Slug
    fulfilment_mode: FulfilmentMode
    description: Description | None = None
    brand: ShortText | None = None
    attributes: dict[str, Any] = {}

    @model_validator(mode="after")
    def _size(self) -> "ProductCreate":
        _check_json(self.attributes)
        return self


class ProductUpdate(_Input):
    name: Name | None = None
    category_slug: Slug | None = None
    fulfilment_mode: FulfilmentMode | None = None
    description: Description | None = None
    brand: ShortText | None = None
    attributes: dict[str, Any] | None = None
    status: ProductStatus | None = None

    @model_validator(mode="after")
    def _rules(self) -> "ProductUpdate":
        for field in ("name", "category_slug", "fulfilment_mode", "attributes", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.attributes is not None:
            _check_json(self.attributes)
        return self


class VariantCreate(_Input):
    sku: Sku
    size: Label | None = None
    color: Label | None = None
    extra: dict[str, Any] = {}

    @model_validator(mode="after")
    def _size(self) -> "VariantCreate":
        _check_json(self.extra)
        return self


class VariantUpdate(_Input):
    is_active: bool | None = None
    extra: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _rules(self) -> "VariantUpdate":
        for field in ("is_active", "extra"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.extra is not None:
            _check_json(self.extra)
        return self


class ImageCreate(_Input):
    url: HttpsUrl | None = None
    telegram_file_id: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    alt_text: Annotated[str, StringConstraints(max_length=200)] | None = None
    sort_order: int = 0
    variant_sku: Sku | None = None

    @model_validator(mode="after")
    def _needs_source(self) -> "ImageCreate":
        if not self.url and not self.telegram_file_id:
            raise ValueError("provide url (https) or telegram_file_id")
        return self


class CategoryOut(BaseModel):
    name: str
    slug: str
    parent_slug: str | None
    sort_order: int


class VariantOut(BaseModel):
    sku: str
    size: str | None
    color: str | None
    extra: dict[str, Any]
    is_active: bool


class ImageOut(BaseModel):
    url: str | None
    telegram_file_id: str | None
    alt_text: str | None
    sort_order: int
    variant_sku: str | None


class ProductOut(BaseModel):
    public_code: str
    name: str
    description: str | None
    brand: str | None
    category_slug: str
    category_name: str
    fulfilment_mode: str
    status: str
    attributes: dict[str, Any]
    variants: list[VariantOut]
    images: list[ImageOut]


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int
