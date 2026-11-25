import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.modules.products.schemas import Sku

Code = Annotated[
    str, StringConstraints(min_length=2, max_length=30, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Units = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
PositiveUnits = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
Days = Annotated[int, Field(strict=True, ge=0, le=365)]
Money = Annotated[int, Field(strict=True, ge=0, le=10_000_000_000)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupplierCreate(_Input):
    code: Code
    name: Name
    contact_note: Note | None = None


class SupplierProductCreate(_Input):
    sku: Sku
    supplier_sku: Sku | None = None
    unit_cost_mmk: Money | None = None
    lead_days_min: Days
    lead_days_max: Days
    capacity: Units

    @model_validator(mode="after")
    def _window(self) -> "SupplierProductCreate":
        if self.lead_days_max < self.lead_days_min:
            raise ValueError("lead_days_max must not be below lead_days_min")
        return self


class SupplierProductUpdate(_Input):
    supplier_sku: Sku | None = None
    unit_cost_mmk: Money | None = None
    lead_days_min: Days | None = None
    lead_days_max: Days | None = None
    capacity: Units | None = None
    status: Annotated[str, StringConstraints(pattern=r"^(ACTIVE|INACTIVE)$")] | None = None

    @model_validator(mode="after")
    def _no_null_required(self) -> "SupplierProductUpdate":
        for field in ("lead_days_min", "lead_days_max", "capacity", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class StockCheckCreate(_Input):
    available_quantity: Units
    note: Note | None = None


class SourcingCreate(_Input):
    supplier_product_id: str
    note: Note | None = None

    @field_validator("supplier_product_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        uuid.UUID(value)  # raises ValueError for anything that is not a UUID
        return value


class ConfirmRequest(_Input):
    expected_from: date
    expected_to: date

    @model_validator(mode="after")
    def _order(self) -> "ConfirmRequest":
        if self.expected_to < self.expected_from:
            raise ValueError("expected_to must not be before expected_from")
        return self


class ReceiveRequest(_Input):
    received_quantity: PositiveUnits


class NoteBody(_Input):
    note: Note | None = None


class SurplusMove(_Input):
    quantity: PositiveUnits
    note: Note | None = None


class SupplierOut(BaseModel):
    code: str
    name: str
    contact_note: str | None
    status: str


class SupplierProductOut(BaseModel):
    id: str
    supplier_code: str
    sku: str
    supplier_sku: str | None
    unit_cost_mmk: int | None
    lead_days_min: int
    lead_days_max: int
    capacity: int
    committed: int
    available_capacity: int
    status: str
    last_reported_available: int | None
    last_checked_at: datetime | None


class CommitmentOut(BaseModel):
    id: str
    sku: str
    supplier_product_id: str
    sourcing_request_id: str | None
    quantity: int
    status: str
    deposit_verified: bool
    needs_review: bool
    expected_from: date
    expected_to: date
    reference_type: str | None
    reference_id: str | None


class SourcingRequestOut(BaseModel):
    id: str
    supplier_product_id: str
    sku: str
    quantity: int
    status: str
    note: str | None
    requested_at: datetime
    confirmed_at: datetime | None
    expected_from: date | None
    expected_to: date | None
    shipped_at: datetime | None
    received_at: datetime | None
    received_quantity: int | None
    allocated_quantity: int | None
    surplus_remaining: int
