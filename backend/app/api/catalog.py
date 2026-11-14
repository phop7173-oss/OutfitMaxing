"""Catalog API. Public: read-only, ACTIVE products. Admin: write, requires X-Admin-Key."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.database.session import get_db
from app.modules.products import service
from app.modules.products.models import Product, ProductCategory
from app.modules.products.schemas import (
    CategoryCreate,
    CategoryOut,
    ImageCreate,
    ImageOut,
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


def product_out(p: Product, *, public: bool) -> ProductOut:
    variants = [v for v in p.variants if v.is_active or not public]
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
            VariantOut(sku=v.sku, size=v.size, color=v.color, extra=v.extra, is_active=v.is_active)
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
    return ProductPage(
        items=[product_out(p, public=True) for p in items], total=total, limit=limit, offset=offset
    )


@public_router.get("/products/{code}")
def public_product(code: str, db: Db) -> ProductOut:
    return product_out(service.get_product(db, code, public=True), public=True)


# ---- admin ----------------------------------------------------------------------------------
@admin_router.post("/categories", status_code=201)
def admin_create_category(data: CategoryCreate, db: Db) -> CategoryOut:
    return category_out(service.create_category(db, data))


@admin_router.post("/products", status_code=201)
def admin_create_product(data: ProductCreate, db: Db) -> ProductOut:
    return product_out(service.create_product(db, data), public=False)


@admin_router.get("/products/{code}")
def admin_get_product(code: str, db: Db) -> ProductOut:
    return product_out(service.get_product(db, code, public=False), public=False)


@admin_router.patch("/products/{code}")
def admin_update_product(code: str, data: ProductUpdate, db: Db) -> ProductOut:
    return product_out(service.update_product(db, code, data), public=False)


@admin_router.post("/products/{code}/variants", status_code=201)
def admin_add_variant(code: str, data: VariantCreate, db: Db) -> ProductOut:
    return product_out(service.add_variant(db, code, data), public=False)


@admin_router.patch("/variants/{sku}")
def admin_update_variant(sku: str, data: VariantUpdate, db: Db) -> ProductOut:
    return product_out(service.update_variant(db, sku, data), public=False)


@admin_router.post("/products/{code}/images", status_code=201)
def admin_add_image(code: str, data: ImageCreate, db: Db) -> ProductOut:
    return product_out(service.add_image(db, code, data), public=False)
