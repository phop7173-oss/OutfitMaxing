"""Catalog API. Public: read-only, ACTIVE products. Admin: write, requires X-Admin-Key."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.database.session import get_db
from app.modules.inventory import service as inventory_service
from app.modules.products import service
from app.modules.products.models import Offer, Product, ProductCategory
from app.modules.products.schemas import (
    CategoryCreate,
    CategoryOut,
    ImageCreate,
    ImageOut,
    OfferCreate,
    OfferOut,
    ProductCreate,
    ProductOut,
    ProductPage,
    ProductUpdate,
    VariantCreate,
    VariantOut,
    VariantUpdate,
)

public_router = APIRouter(prefix="/catalog", tags=["catalog"])
admin_router = APIRouter(
    prefix="/admin/catalog", tags=["catalog-admin"], dependencies=[Depends(require_admin)]
)
Db = Annotated[Session, Depends(get_db)]


def category_out(c: ProductCategory) -> CategoryOut:
    return CategoryOut(
        name=c.name,
        slug=c.slug,
        parent_slug=c.parent.slug if c.parent else None,
        sort_order=c.sort_order,
    )


def prices_for(db: Session, products: list[Product]) -> dict[UUID, Offer]:
    return service.current_prices(db, [v.id for p in products for v in p.variants])


def availability_for(db: Session, products: list[Product]) -> dict[UUID, str]:
    """Customer-facing stock state per variant (never exact quantities)."""
    ids = [v.id for p in products for v in p.variants]
    available = inventory_service.available_quantities(db, ids)
    return {
        v.id: inventory_service.availability_label(p.fulfilment_mode, available.get(v.id, 0))
        for p in products
        for v in p.variants
    }


def product_out(
    p: Product, *, public: bool, prices: dict[UUID, Offer], availability: dict[UUID, str]
) -> ProductOut:
    """Public view: only active variants that have a current price (the others cannot be
    bought). Admin view: everything, with price null where none is in effect."""
    variants = [v for v in p.variants if not public or (v.is_active and v.id in prices)]
    visible = {v.id for v in variants}
    images = [i for i in p.images if i.variant_id is None or i.variant_id in visible]
    return ProductOut(
        public_code=p.public_code,
        name=p.name,
        description=p.description,
        brand=p.brand,
        category_slug=p.category.slug,
        category_name=p.category.name,
        fulfilment_mode=p.fulfilment_mode,
        status=p.status,
        attributes=p.attributes,
        variants=[
            VariantOut(
                sku=v.sku,
                size=v.size,
                color=v.color,
                extra=v.extra,
                is_active=v.is_active,
                price_mmk=prices[v.id].price_mmk if v.id in prices else None,
                compare_at_price_mmk=prices[v.id].compare_at_price_mmk if v.id in prices else None,
                availability=availability.get(v.id),
            )
            for v in variants
        ],
        images=[
            ImageOut(
                url=i.url,
                telegram_file_id=i.telegram_file_id,
                alt_text=i.alt_text,
                sort_order=i.sort_order,
                variant_sku=i.variant.sku if i.variant else None,
            )
            for i in images
        ],
    )


def admin_out(db: Session, p: Product) -> ProductOut:
    return product_out(
        p, public=False, prices=prices_for(db, [p]), availability=availability_for(db, [p])
    )


def offer_out(o: Offer) -> OfferOut:
    return OfferOut(
        id=str(o.id),
        sku=o.variant.sku,
        price_mmk=o.price_mmk,
        compare_at_price_mmk=o.compare_at_price_mmk,
        starts_at=o.starts_at,
        ends_at=o.ends_at,
        status=o.status,
        is_current=service.offer_is_current(o),
    )


# ---- public ---------------------------------------------------------------------------------
@public_router.get("/categories")
def public_categories(db: Db) -> list[CategoryOut]:
    return [category_out(c) for c in service.list_categories(db)]


@public_router.get("/products")
def public_products(
    db: Db,
    category: Annotated[str | None, Query(max_length=60)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductPage:
    items, total = service.list_public_products(
        db, category_slug=category, q=q, limit=limit, offset=offset
    )
    prices = prices_for(db, items)
    availability = availability_for(db, items)
    return ProductPage(
        items=[
            product_out(p, public=True, prices=prices, availability=availability) for p in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@public_router.get("/products/{code}")
def public_product(code: str, db: Db) -> ProductOut:
    product = service.get_product(db, code, public=True)
    return product_out(
        product,
        public=True,
        prices=prices_for(db, [product]),
        availability=availability_for(db, [product]),
    )


# ---- admin ----------------------------------------------------------------------------------
@admin_router.post("/categories", status_code=201)
def admin_create_category(data: CategoryCreate, db: Db) -> CategoryOut:
    return category_out(service.create_category(db, data))


@admin_router.post("/products", status_code=201)
def admin_create_product(data: ProductCreate, db: Db) -> ProductOut:
    return admin_out(db, service.create_product(db, data))


@admin_router.get("/products/{code}")
def admin_get_product(code: str, db: Db) -> ProductOut:
    return admin_out(db, service.get_product(db, code, public=False))


@admin_router.patch("/products/{code}")
def admin_update_product(code: str, data: ProductUpdate, db: Db) -> ProductOut:
    return admin_out(db, service.update_product(db, code, data))


@admin_router.post("/products/{code}/variants", status_code=201)
def admin_add_variant(code: str, data: VariantCreate, db: Db) -> ProductOut:
    return admin_out(db, service.add_variant(db, code, data))


@admin_router.patch("/variants/{sku}")
def admin_update_variant(sku: str, data: VariantUpdate, db: Db) -> ProductOut:
    return admin_out(db, service.update_variant(db, sku, data))


@admin_router.post("/products/{code}/images", status_code=201)
def admin_add_image(code: str, data: ImageCreate, db: Db) -> ProductOut:
    return admin_out(db, service.add_image(db, code, data))


@admin_router.post("/variants/{sku}/offers", status_code=201)
def admin_create_offer(sku: str, data: OfferCreate, db: Db) -> OfferOut:
    return offer_out(service.create_offer(db, sku, data))


@admin_router.get("/variants/{sku}/offers")
def admin_list_offers(sku: str, db: Db) -> list[OfferOut]:
    return [offer_out(o) for o in service.list_offers(db, sku)]


@admin_router.post("/offers/{offer_id}/cancel")
def admin_cancel_offer(offer_id: UUID, db: Db) -> OfferOut:
    return offer_out(service.cancel_offer(db, offer_id))
