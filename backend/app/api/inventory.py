"""Inventory admin API (stock levels and manual changes). Reservations are internal: the
checkout will call the service; there is deliberately no HTTP endpoint to reserve or commit."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.database.session import get_db
from app.modules.inventory import service
from app.modules.inventory.schemas import (
    LocationStockOut,
    MovementOut,
    StockAdjust,
    StockReceive,
    VariantStockOut,
)

admin_router = APIRouter(
    prefix="/admin/inventory", tags=["inventory-admin"], dependencies=[Depends(require_admin)]
)
Db = Annotated[Session, Depends(get_db)]


def _stock_out(db: Session, sku: str) -> VariantStockOut:
    _, levels = service.stock_levels(db, sku)
    return VariantStockOut(
        sku=sku,
        total_on_hand=sum(lv["on_hand"] for lv in levels),
        total_reserved=sum(lv["reserved"] for lv in levels),
        total_available=sum(lv["available"] for lv in levels),
        locations=[LocationStockOut(**lv) for lv in levels],
    )


@admin_router.get("/variants/{sku}")
def get_stock(sku: str, db: Db) -> VariantStockOut:
    return _stock_out(db, sku)


@admin_router.post("/variants/{sku}/receive", status_code=201)
def receive(sku: str, data: StockReceive, db: Db) -> VariantStockOut:
    service.receive_stock(
        db, sku, data.quantity, location_code=data.location_code, note=data.note
    )
    return _stock_out(db, sku)


@admin_router.post("/variants/{sku}/adjust")
def adjust(sku: str, data: StockAdjust, db: Db) -> VariantStockOut:
    service.adjust_stock(
        db, sku, data.delta, note=data.note, location_code=data.location_code
    )
    return _stock_out(db, sku)


@admin_router.get("/variants/{sku}/movements")
def movements(
    sku: str, db: Db, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[MovementOut]:
    return [
        MovementOut(
            created_at=m.created_at,
            location_code=code,
            reason=m.reason,
            delta_on_hand=m.delta_on_hand,
            delta_reserved=m.delta_reserved,
            note=m.note,
            actor=m.actor,
            reservation_id=str(m.reservation_id) if m.reservation_id else None,
        )
        for m, code in service.list_movements(db, sku, limit)
    ]
