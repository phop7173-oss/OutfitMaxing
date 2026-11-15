"""Catalog service. Owns transactions: each public function commits or raises."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.modules.products.models import (
    Offer,
    Product,
    ProductCategory,
    ProductImage,
    ProductVariant,
    product_code_seq,
)
from app.modules.products.schemas import (
    CategoryCreate,
    ImageCreate,
    OfferCreate,
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


# ---- prices (offers) helpers ------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(UTC)


def _current_offer_conditions(now: datetime) -> list[ColumnElement[bool]]:
    return [
        Offer.status == "ACTIVE",
        Offer.starts_at <= now,
        or_(Offer.ends_at.is_(None), Offer.ends_at > now),
    ]


def current_prices(
    session: Session, variant_ids: list[uuid.UUID], at: datetime | None = None
) -> dict[uuid.UUID, Offer]:
    """The price in effect for each variant at `at` (default now). Variants without one are
    absent from the result."""
    if not variant_ids:
        return {}
    now = at or _now()
    stmt = select(Offer).where(Offer.variant_id.in_(variant_ids), *_current_offer_conditions(now))
    return {offer.variant_id: offer for offer in session.execute(stmt).scalars()}


def offer_is_current(offer: Offer, at: datetime | None = None) -> bool:
    now = at or _now()
    return (
        offer.status == "ACTIVE"
        and offer.starts_at <= now
        and (offer.ends_at is None or offer.ends_at > now)
    )


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
    if public:
        active_ids = [v.id for v in product.variants if v.is_active]
        if product.status != "ACTIVE" or not current_prices(session, active_ids):
            raise NotFoundError("Product not found")
    return product


def _change_status(session: Session, product: Product, target: str) -> None:
    if target not in STATUS_TRANSITIONS[product.status]:
        raise UnprocessableError(f"Cannot change status from {product.status} to {target}")
    if target == "ACTIVE":
        active = [v for v in product.variants if v.is_active]
        if not active:
            raise UnprocessableError(
                "A product needs at least one active variant before publishing"
            )
        if not current_prices(session, [v.id for v in active]):
            raise UnprocessableError(
                "Set a current price (offer) on at least one active variant before publishing"
            )
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
        _change_status(session, product, data.status)
    _commit(session, "Product")
    return _require_product(session, code)


def list_public_products(
    session: Session, *, category_slug: str | None, q: str | None, limit: int, offset: int
) -> tuple[list[Product], int]:
    priced_variant_exists = (
        select(ProductVariant.id)
        .join(Offer, Offer.variant_id == ProductVariant.id)
        .where(
            ProductVariant.product_id == Product.id,
            ProductVariant.is_active.is_(True),
            *_current_offer_conditions(_now()),
        )
        .exists()
    )
    conditions = [Product.status == "ACTIVE", priced_variant_exists]
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
    variant = _variant_by_sku(session, sku)
    product = session.get(Product, variant.product_id)
    fields = data.model_fields_set
    if "is_active" in fields and data.is_active is False and variant.is_active:
        if product is not None and product.status == "ACTIVE":
            sibling_ids = list(
                session.execute(
                    select(ProductVariant.id).where(
                        ProductVariant.product_id == variant.product_id,
                        ProductVariant.is_active.is_(True),
                        ProductVariant.id != variant.id,
                    )
                ).scalars()
            )
            if not current_prices(session, sibling_ids):
                raise UnprocessableError(
                    "Cannot deactivate the last active, priced variant of an active product; "
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


# ---- offers (prices) ---------------------------------------------------------------------------
def _variant_by_sku(session: Session, sku: str, *, lock: bool = False) -> ProductVariant:
    stmt = select(ProductVariant).where(ProductVariant.sku == sku)
    if lock:
        stmt = stmt.with_for_update()
    variant = session.execute(stmt).scalar_one_or_none()
    if variant is None:
        raise NotFoundError("Variant not found")
    return variant


def create_offer(
    session: Session, sku: str, data: OfferCreate, *, now: datetime | None = None
) -> Offer:
    """Create a price for a variant.

    The variant row is locked first, so concurrent offer writes for the same variant run one
    after another; the overlap check below then sees the earlier commit. Two ACTIVE offers for
    one variant can therefore never cover the same moment."""
    now = now or _now()
    variant = _variant_by_sku(session, sku, lock=True)
    starts_at = data.starts_at or now
    if data.ends_at is not None and data.ends_at <= starts_at:
        session.rollback()
        raise UnprocessableError("ends_at must be after starts_at")
    overlap = [
        Offer.variant_id == variant.id,
        Offer.status == "ACTIVE",
        or_(Offer.ends_at.is_(None), Offer.ends_at > starts_at),
    ]
    if data.ends_at is not None:
        overlap.append(Offer.starts_at < data.ends_at)
    if session.execute(select(Offer.id).where(*overlap).limit(1)).first() is not None:
        session.rollback()
        raise ConflictError("This variant already has a price that overlaps that period")
    offer = Offer(
        variant_id=variant.id,
        price_mmk=data.price_mmk,
        compare_at_price_mmk=data.compare_at_price_mmk,
        starts_at=starts_at,
        ends_at=data.ends_at,
    )
    session.add(offer)
    _commit(session, "Offer")
    return offer


def list_offers(session: Session, sku: str) -> list[Offer]:
    variant = _variant_by_sku(session, sku)
    stmt = (
        select(Offer)
        .where(Offer.variant_id == variant.id)
        .options(joinedload(Offer.variant))
        .order_by(Offer.starts_at.desc(), Offer.created_at.desc())
    )
    return list(session.execute(stmt).scalars())


def cancel_offer(session: Session, offer_id: uuid.UUID) -> Offer:
    offer = session.get(Offer, offer_id)
    if offer is None:
        raise NotFoundError("Offer not found")
    if offer.status != "CANCELLED":
        offer.status = "CANCELLED"
        _commit(session, "Offer")
    return offer
