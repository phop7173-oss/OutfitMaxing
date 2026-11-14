"""Catalog service. Owns transactions: each public function commits or raises."""

import re

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.modules.products.models import (
    Product,
    ProductCategory,
    ProductImage,
    ProductVariant,
    product_code_seq,
)
from app.modules.products.schemas import (
    CategoryCreate,
    ImageCreate,
    ProductCreate,
    ProductUpdate,
    VariantCreate,
    VariantUpdate,
)

# Business spec: the shop does not sell these (watches, electronics, battery-powered, cosmetics).
# Checked against the category name and slug at category creation time.
EXCLUDED_TERMS = frozenset(
    {
        "watch", "watches", "smartwatch", "smartwatches",
        "electronic", "electronics", "battery", "batteries", "battery-powered",
        "cosmetic", "cosmetics", "makeup",
    }
)  # fmt: skip

STATUS_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ACTIVE", "ARCHIVED"},
    "ACTIVE": {"DRAFT", "ARCHIVED"},
    "ARCHIVED": {"DRAFT"},
}

CONFLICT_MESSAGES = {
    "uq_product_categories_slug": "A category with this slug already exists",
    "uq_product_variants_sku": "A variant with this SKU already exists",
    "uq_product_variants_combo": "This product already has a variant with the same size and color",
}


def is_excluded_category(name: str, slug: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", f"{name} {slug}".lower()))
    return bool(tokens & EXCLUDED_TERMS)


def _commit(session: Session, what: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diag = getattr(exc.orig, "diag", None)
        name = getattr(diag, "constraint_name", None)
        message = CONFLICT_MESSAGES.get(name, f"{what} conflicts with an existing record")
        raise ConflictError(message) from exc


def _category_by_slug(session: Session, slug: str) -> ProductCategory:
    category = session.execute(
        select(ProductCategory).where(ProductCategory.slug == slug)
    ).scalar_one_or_none()
    if category is None:
        raise NotFoundError("Category not found")
    return category


def _load_product(session: Session, code: str) -> Product | None:
    stmt = (
        select(Product)
        .where(Product.public_code == code)
        .options(
            selectinload(Product.variants),
            selectinload(Product.images).selectinload(ProductImage.variant),
            joinedload(Product.category),
        )
        .execution_options(populate_existing=True)
    )
    return session.execute(stmt).unique().scalar_one_or_none()


def _require_product(session: Session, code: str) -> Product:
    product = _load_product(session, code)
    if product is None:
        raise NotFoundError("Product not found")
    return product


# ---- categories -----------------------------------------------------------------------------
def create_category(session: Session, data: CategoryCreate) -> ProductCategory:
    if is_excluded_category(data.name, data.slug):
        raise UnprocessableError(
            "This category is not allowed: the shop does not sell watches, electronics, "
            "battery-powered products or cosmetics"
        )
    parent_id = _category_by_slug(session, data.parent_slug).id if data.parent_slug else None
    category = ProductCategory(
        name=data.name, slug=data.slug, parent_id=parent_id, sort_order=data.sort_order
    )
    session.add(category)
    _commit(session, "Category")
    return category


def list_categories(session: Session) -> list[ProductCategory]:
    stmt = (
        select(ProductCategory)
        .options(joinedload(ProductCategory.parent))
        .order_by(ProductCategory.sort_order, ProductCategory.name)
    )
    return list(session.execute(stmt).scalars())


# ---- products -------------------------------------------------------------------------------
def create_product(session: Session, data: ProductCreate) -> Product:
    category = _category_by_slug(session, data.category_slug)
    number = session.execute(select(product_code_seq.next_value())).scalar_one()
    code = f"OM-P-{number:06d}"
    session.add(
        Product(
            public_code=code,
            category_id=category.id,
            name=data.name,
            description=data.description,
            brand=data.brand,
            fulfilment_mode=data.fulfilment_mode,
            attributes=data.attributes,
            status="DRAFT",
        )
    )
    _commit(session, "Product")
    return _require_product(session, code)


def get_product(session: Session, code: str, *, public: bool) -> Product:
    product = _require_product(session, code)
    if public and product.status != "ACTIVE":
        raise NotFoundError("Product not found")
    return product


def _change_status(product: Product, target: str) -> None:
    if target not in STATUS_TRANSITIONS[product.status]:
        raise UnprocessableError(f"Cannot change status from {product.status} to {target}")
    if target == "ACTIVE" and not any(v.is_active for v in product.variants):
        raise UnprocessableError("A product needs at least one active variant before publishing")
    product.status = target


def update_product(session: Session, code: str, data: ProductUpdate) -> Product:
    product = _require_product(session, code)
    fields = data.model_fields_set
    if "category_slug" in fields and data.category_slug is not None:
        product.category_id = _category_by_slug(session, data.category_slug).id
    for field in ("name", "description", "brand", "fulfilment_mode", "attributes"):
        if field in fields:
            setattr(product, field, getattr(data, field))
    if "status" in fields and data.status is not None and data.status != product.status:
        _change_status(product, data.status)
    _commit(session, "Product")
    return _require_product(session, code)


def list_public_products(
    session: Session, *, category_slug: str | None, q: str | None, limit: int, offset: int
) -> tuple[list[Product], int]:
    conditions = [Product.status == "ACTIVE"]
    if category_slug:
        conditions.append(ProductCategory.slug == category_slug)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append(
            or_(
                Product.name.ilike(pattern, escape="\\"),
                Product.brand.ilike(pattern, escape="\\"),
                Product.description.ilike(pattern, escape="\\"),
            )
        )
    join_on = Product.category_id == ProductCategory.id
    total = session.execute(
        select(func.count()).select_from(Product).join(ProductCategory, join_on).where(*conditions)
    ).scalar_one()
    stmt = (
        select(Product)
        .join(ProductCategory, join_on)
        .where(*conditions)
        .options(
            selectinload(Product.variants),
            selectinload(Product.images).selectinload(ProductImage.variant),
            joinedload(Product.category),
        )
        .order_by(Product.created_at.desc(), Product.public_code.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).unique().scalars()), total


# ---- variants and images ----------------------------------------------------------------------
def add_variant(session: Session, code: str, data: VariantCreate) -> Product:
    product = _require_product(session, code)
    session.add(
        ProductVariant(
            product_id=product.id, sku=data.sku, size=data.size, color=data.color, extra=data.extra
        )
    )
    _commit(session, "Variant")
    return _require_product(session, code)


def update_variant(session: Session, sku: str, data: VariantUpdate) -> Product:
    variant = session.execute(
        select(ProductVariant).where(ProductVariant.sku == sku)
    ).scalar_one_or_none()
    if variant is None:
        raise NotFoundError("Variant not found")
    product = session.get(Product, variant.product_id)
    fields = data.model_fields_set
    if "is_active" in fields and data.is_active is False and variant.is_active:
        others = session.execute(
            select(func.count())
            .select_from(ProductVariant)
            .where(
                ProductVariant.product_id == variant.product_id,
                ProductVariant.is_active.is_(True),
                ProductVariant.id != variant.id,
            )
        ).scalar_one()
        if product is not None and product.status == "ACTIVE" and others == 0:
            raise UnprocessableError(
                "Cannot deactivate the last active variant of an active product; "
                "change the product status first"
            )
    if "is_active" in fields and data.is_active is not None:
        variant.is_active = data.is_active
    if "extra" in fields and data.extra is not None:
        variant.extra = data.extra
    _commit(session, "Variant")
    assert product is not None
    return _require_product(session, product.public_code)


def add_image(session: Session, code: str, data: ImageCreate) -> Product:
    product = _require_product(session, code)
    variant_id = None
    if data.variant_sku:
        variant = next((v for v in product.variants if v.sku == data.variant_sku), None)
        if variant is None:
            raise UnprocessableError("That variant does not belong to this product")
        variant_id = variant.id
    session.add(
        ProductImage(
            product_id=product.id,
            variant_id=variant_id,
            url=data.url,
            telegram_file_id=data.telegram_file_id,
            alt_text=data.alt_text,
            sort_order=data.sort_order,
        )
    )
    _commit(session, "Image")
    return _require_product(session, code)
