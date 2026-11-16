from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.products.schemas import Slug

Quantity = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
Delta = Annotated[int, Field(strict=True, ge=-1_000_000, le=1_000_000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StockReceive(_Input):
    quantity: Quantity
    location_code: Slug = "main"
    note: Note | None = None


class StockAdjust(_Input):
    delta: Delta
    note: Note  # required: every manual correction must say why
    location_code: Slug = "main"

    @field_validator("delta")
    @classmethod
    def _not_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("delta must not be zero")
        return value


class LocationStockOut(BaseModel):
    location_code: str
    location_name: str
    on_hand: int
    reserved: int
    available: int


class VariantStockOut(BaseModel):
    sku: str
    total_on_hand: int
    total_reserved: int
    total_available: int
    locations: list[LocationStockOut]


class MovementOut(BaseModel):
    created_at: datetime
    location_code: str
    reason: str
    delta_on_hand: int
    delta_reserved: int
    note: str | None
    actor: str | None
    reservation_id: str | None
