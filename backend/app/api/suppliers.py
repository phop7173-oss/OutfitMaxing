"""Supplier, preorder and sourcing admin API. There is deliberately no HTTP endpoint that
creates a preorder commitment: the order engine will call the service, like reservations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.database.session import get_db
from app.modules.suppliers import service, sourcing
from app.modules.suppliers.models import (
    PreorderCommitment,
    SourcingRequest,
    SupplierProduct,
    SupplierStockCheck,
)
from app.modules.suppliers.schemas import (
    CommitmentOut,
    ConfirmRequest,
    NoteBody,
    ReceiveRequest,
    SourcingCreate,
    SourcingRequestOut,
    StockCheckCreate,
    SupplierCreate,
    SupplierOut,
    SupplierProductCreate,
    SupplierProductOut,
    SupplierProductUpdate,
    SurplusMove,
)

admin_router = APIRouter(
    prefix="/admin", tags=["suppliers-admin"], dependencies=[Depends(require_admin)]
)
Db = Annotated[Session, Depends(get_db)]
Status = Annotated[str | None, Query(pattern=r"^[A-Z_]{3,12}$")]


def supplier_out(s) -> SupplierOut:
    return SupplierOut(code=s.code, name=s.name, contact_note=s.contact_note, status=s.status)


def product_out(sp: SupplierProduct, check: SupplierStockCheck | None) -> SupplierProductOut:
    return SupplierProductOut(
        id=str(sp.id),
        supplier_code=sp.supplier.code,
        sku=sp.variant.sku,
        supplier_sku=sp.supplier_sku,
        unit_cost_mmk=sp.unit_cost_mmk,
        lead_days_min=sp.lead_days_min,
        lead_days_max=sp.lead_days_max,
        capacity=sp.capacity,
        committed=sp.committed,
        available_capacity=sp.capacity - sp.committed,
        status=sp.status,
        last_reported_available=check.available_quantity if check else None,
        last_checked_at=check.checked_at if check else None,
    )


def single_product_out(db: Session, sp: SupplierProduct) -> SupplierProductOut:
    checks = service.last_stock_checks(db, [sp.id])
    return product_out(sp, checks.get(sp.id))


def commitment_out(c: PreorderCommitment) -> CommitmentOut:
    return CommitmentOut(
        id=str(c.id),
        sku=c.variant.sku,
        supplier_product_id=str(c.supplier_product_id),
        sourcing_request_id=str(c.sourcing_request_id) if c.sourcing_request_id else None,
        quantity=c.quantity,
        status=c.status,
        deposit_verified=c.deposit_verified,
        needs_review=c.needs_review,
        expected_from=c.expected_from,
        expected_to=c.expected_to,
        reference_type=c.reference_type,
        reference_id=c.reference_id,
    )


def request_out(r: SourcingRequest) -> SourcingRequestOut:
    return SourcingRequestOut(
        id=str(r.id),
        supplier_product_id=str(r.supplier_product_id),
        sku=r.supplier_product.variant.sku,
        quantity=r.quantity,
        status=r.status,
        note=r.note,
        requested_at=r.requested_at,
        confirmed_at=r.confirmed_at,
        expected_from=r.expected_from,
        expected_to=r.expected_to,
        shipped_at=r.shipped_at,
        received_at=r.received_at,
        received_quantity=r.received_quantity,
        allocated_quantity=r.allocated_quantity,
        surplus_remaining=r.surplus_remaining,
    )


# ---- suppliers ------------------------------------------------------------------------------
@admin_router.post("/suppliers", status_code=201)
def create_supplier(data: SupplierCreate, db: Db) -> SupplierOut:
    return supplier_out(service.create_supplier(db, data))


@admin_router.get("/suppliers")
def list_suppliers(db: Db) -> list[SupplierOut]:
    return [supplier_out(s) for s in service.list_suppliers(db)]


@admin_router.post("/suppliers/{code}/products", status_code=201)
def create_supplier_product(code: str, data: SupplierProductCreate, db: Db) -> SupplierProductOut:
    return single_product_out(db, service.create_supplier_product(db, code, data))


@admin_router.get("/suppliers/{code}/products")
def list_supplier_products(code: str, db: Db) -> list[SupplierProductOut]:
    items = service.list_supplier_products(db, code)
    checks = service.last_stock_checks(db, [sp.id for sp in items])
    return [product_out(sp, checks.get(sp.id)) for sp in items]


@admin_router.patch("/suppliers/products/{supplier_product_id}")
def update_supplier_product(
    supplier_product_id: UUID, data: SupplierProductUpdate, db: Db
) -> SupplierProductOut:
    return single_product_out(db, service.update_supplier_product(db, supplier_product_id, data))


@admin_router.post("/suppliers/products/{supplier_product_id}/stock-checks", status_code=201)
def record_stock_check(
    supplier_product_id: UUID, data: StockCheckCreate, db: Db
) -> SupplierProductOut:
    service.record_stock_check(db, supplier_product_id, data)
    return single_product_out(db, service.get_supplier_product(db, supplier_product_id))


# ---- preorder commitments -------------------------------------------------------------------
@admin_router.get("/preorders/commitments")
def list_commitments(
    db: Db, status: Status = None, needs_review: bool | None = None
) -> list[CommitmentOut]:
    return [
        commitment_out(c)
        for c in service.list_commitments(db, status=status, needs_review=needs_review)
    ]


@admin_router.post("/preorders/commitments/{commitment_id}/deposit-verified")
def deposit_verified(commitment_id: UUID, db: Db) -> CommitmentOut:
    return commitment_out(service.mark_deposit_verified(db, commitment_id))


@admin_router.post("/preorders/commitments/{commitment_id}/release")
def release_commitment(commitment_id: UUID, db: Db) -> CommitmentOut:
    return commitment_out(service.release_commitment(db, commitment_id))


@admin_router.post("/preorders/flag-overdue")
def flag_overdue(db: Db) -> dict[str, int]:
    return {"flagged": service.flag_overdue_commitments(db)}


# ---- sourcing requests ----------------------------------------------------------------------
@admin_router.post("/sourcing/requests", status_code=201)
def create_request(data: SourcingCreate, db: Db) -> SourcingRequestOut:
    request = sourcing.create_request(db, UUID(data.supplier_product_id), note=data.note)
    return request_out(request)


@admin_router.get("/sourcing/requests")
def list_requests(db: Db, status: Status = None) -> list[SourcingRequestOut]:
    return [request_out(r) for r in sourcing.list_requests(db, status=status)]


@admin_router.get("/sourcing/unconfirmed")
def unconfirmed(db: Db) -> list[SourcingRequestOut]:
    return [request_out(r) for r in sourcing.list_unconfirmed_requests(db)]


@admin_router.post("/sourcing/flag-unconfirmed")
def flag_unconfirmed(db: Db) -> dict[str, int]:
    return {"alerted": sourcing.flag_unconfirmed_requests(db)}


@admin_router.post("/sourcing/requests/{request_id}/confirm")
def confirm(request_id: UUID, data: ConfirmRequest, db: Db) -> SourcingRequestOut:
    done = sourcing.confirm_request(db, request_id, data.expected_from, data.expected_to)
    return request_out(done)


@admin_router.post("/sourcing/requests/{request_id}/ship")
def ship(request_id: UUID, db: Db) -> SourcingRequestOut:
    return request_out(sourcing.ship_request(db, request_id))


@admin_router.post("/sourcing/requests/{request_id}/receive")
def receive(request_id: UUID, data: ReceiveRequest, db: Db) -> SourcingRequestOut:
    return request_out(sourcing.receive_request(db, request_id, data.received_quantity))


@admin_router.post("/sourcing/requests/{request_id}/fail")
def fail(request_id: UUID, data: NoteBody, db: Db) -> SourcingRequestOut:
    return request_out(sourcing.fail_request(db, request_id, note=data.note))


@admin_router.post("/sourcing/requests/{request_id}/cancel")
def cancel(request_id: UUID, data: NoteBody, db: Db) -> SourcingRequestOut:
    return request_out(sourcing.cancel_request(db, request_id, note=data.note))


@admin_router.post("/sourcing/requests/{request_id}/move-surplus-to-stock")
def move_surplus(request_id: UUID, data: SurplusMove, db: Db) -> SourcingRequestOut:
    done = sourcing.move_surplus_to_stock(db, request_id, data.quantity, note=data.note)
    return request_out(done)
