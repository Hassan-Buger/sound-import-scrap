from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import Product, Category
from app.config import settings


class ImageOut(BaseModel):
    id: int
    src: str
    sort_order: int
    is_cover: bool

    @field_validator("src", mode="before")
    @classmethod
    def make_absolute_url(cls, v):
        if not v:
            return v
        if not v.startswith("http"):
            if isinstance(v, str) and v.isdigit():
                return (
                    f"https://cdn.webshopapp.com/shops/188510/files/{v}/500x500x2.jpg"
                )
            return settings.base_url.rstrip("/") + "/" + v.lstrip("/")
        return v


class AttributeOut(BaseModel):
    name: str
    value: Optional[str] = None
    sort_order: int = 0


class SpecificationOut(BaseModel):
    name: str
    value: Optional[str] = None
    sort_order: int = 0


class BrandOut(BaseModel):
    name: str
    product_count: int


class CategoryOut(BaseModel):
    id: int
    parent_id: Optional[int] = None
    name: str
    slug: str
    canonical_path: Optional[str] = None
    level: Optional[int] = 0
    product_count: Optional[int] = 0
    source_product_count: Optional[int] = 0
    is_active: Optional[bool] = True
    children: List["CategoryOut"] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def from_orm(cls, data):
        if isinstance(data, Category):
            raw_children = data.children
            if raw_children is None:
                children = []
            elif isinstance(raw_children, list):
                children = [CategoryOut.from_orm(c) for c in raw_children]
            else:
                children = [CategoryOut.from_orm(raw_children)]
            return {
                "id": data.id,
                "parent_id": data.parent_id,
                "name": data.name,
                "slug": data.slug,
                "canonical_path": getattr(data, "canonical_path", None),
                "level": data.level,
                "product_count": data.product_count,
                "source_product_count": getattr(data, "source_product_count", 0),
                "is_active": getattr(data, "is_active", True),
                "children": children,
            }
        return data


TOP_LEVEL_PARENTS = {
    "speakers",
    "home-audio",
    "car-audio",
    "components",
    "accessories",
    "audio-components",
    "crossover-components",
    "diy-kits",
}


def format_category_name(slug: str) -> str:
    if not slug:
        return ""
    clean = slug.strip().replace("-", " ")
    terms = {
        "diy": "DIY",
        "ean": "EAN",
        "sku": "SKU",
        "usb": "USB",
        "dac": "DAC",
        "dsp": "DSP",
        "rms": "RMS",
    }
    words = []
    for word in clean.split():
        w_lower = word.lower()
        if w_lower in terms:
            words.append(terms[w_lower])
        else:
            words.append(word.capitalize())
    return " ".join(words)


def _resolve_primary_category_slug(cats: List[str]) -> str:
    if not cats:
        return "Uncategorized"
    clean_cats = [c.strip() for c in cats if c and c.strip()]
    if not clean_cats:
        return "Uncategorized"
    for c in reversed(clean_cats):
        if c.lower() not in TOP_LEVEL_PARENTS:
            return c
    return clean_cats[-1]


def _resolve_primary_category(cats: List[str]) -> str:
    slug = _resolve_primary_category_slug(cats)
    return format_category_name(slug) if slug != "Uncategorized" else "Uncategorized"


def _resolve_category_path(cats: List[str]) -> List[str]:
    if not cats:
        return []
    clean_slugs = [c.strip() for c in cats if c and c.strip()]
    return [format_category_name(s) for s in clean_slugs]


def _category_path_from_orm(product: Product):
    """Derive the primary category and full path from the relational
    ``product.categories`` association (preferred over legacy slug string).

    Returns ``(category, categories, category_slugs)`` with the canonical
    root-to-leaf order, choosing the deepest category path as primary.
    """
    cats = list(getattr(product, "categories", None) or [])
    if not cats:
        return None

    def path_slugs(cat):
        raw = getattr(cat, "canonical_path", None) or ""
        parts = [p for p in raw.strip("/").split("/") if p]
        lang = ("en", "nl", "de")
        if parts and parts[0] in lang:
            parts = parts[1:]
        return parts

    best = max(cats, key=lambda c: (len(path_slugs(c)), c.slug or ""))
    slugs = path_slugs(best)
    names = [format_category_name(s) for s in slugs]
    return (
        names[-1] if names else format_category_name(best.slug),
        names,
        slugs,
    )


def _legacy_category_info(data):
    cats = data.category_ids.split(",") if data.category_ids else []
    return (
        _resolve_primary_category(cats),
        _resolve_category_path(cats),
        cats,
    )


class ProductListItem(BaseModel):
    id: int
    sku: str
    title: Optional[str] = None
    regular_price: Optional[float] = None
    short_description: Optional[str] = None
    stock: Optional[int] = None
    stock_status: Optional[str] = None
    brand: Optional[str] = None
    ean: Optional[str] = None
    currency: str = "EUR"
    url: Optional[str] = None
    category: str = "Uncategorized"
    categories: List[str] = Field(default_factory=list)
    category_slugs: List[str] = Field(default_factory=list)
    images: List[ImageOut] = Field(default_factory=list)
    updated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def map_from_orm(cls, data):
        if isinstance(data, Product):
            rel = _category_path_from_orm(data)
            if rel:
                primary, formatted_path, slugs = rel
            else:
                primary, formatted_path, slugs = _legacy_category_info(data)
            return {
                "id": data.id,
                "sku": data.sku,
                "title": data.title,
                "regular_price": data.regular_price
                if data.regular_price is not None
                else data.price,
                "short_description": data.short_description
                if data.short_description is not None
                else data.description,
                "stock": data.stock,
                "stock_status": data.stock_status,
                "brand": data.brand,
                "ean": data.ean,
                "currency": data.currency or "EUR",
                "url": data.url,
                "category": primary,
                "categories": formatted_path,
                "category_slugs": slugs,
                "images": [
                    ImageOut(
                        id=i.id,
                        src=i.image_url,
                        sort_order=i.sort_order,
                        is_cover=i.is_cover,
                    )
                    for i in data.images
                ],
                "updated_at": data.updated_at.isoformat() if data.updated_at else None,
            }
        return data


class ProductDetail(BaseModel):
    id: int
    sku: str
    title: Optional[str] = None
    regular_price: Optional[float] = None
    short_description: Optional[str] = None
    long_description: Optional[str] = None
    stock: Optional[int] = None
    stock_status: Optional[str] = None
    brand: Optional[str] = None
    ean: Optional[str] = None
    currency: str = "EUR"
    url: Optional[str] = None
    category: str = "Uncategorized"
    categories: List[str] = Field(default_factory=list)
    category_slugs: List[str] = Field(default_factory=list)
    images: List[ImageOut] = Field(default_factory=list)
    attributes: List[AttributeOut] = Field(default_factory=list)
    specifications: List[SpecificationOut] = Field(default_factory=list)
    updated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def map_from_orm(cls, data):
        if isinstance(data, Product):
            rel = _category_path_from_orm(data)
            if rel:
                primary, formatted_path, slugs = rel
            else:
                primary, formatted_path, slugs = _legacy_category_info(data)
            attrs_sorted = sorted(data.attributes_rel, key=lambda a: a.sort_order or 0)
            return {
                "id": data.id,
                "sku": data.sku,
                "title": data.title,
                "regular_price": data.regular_price
                if data.regular_price is not None
                else data.price,
                "short_description": data.short_description
                if data.short_description is not None
                else data.description,
                "long_description": data.long_description,
                "stock": data.stock,
                "stock_status": data.stock_status,
                "brand": data.brand,
                "ean": data.ean,
                "currency": data.currency or "EUR",
                "url": data.url,
                "category": primary,
                "categories": formatted_path,
                "category_slugs": slugs,
                "images": [
                    ImageOut(
                        id=i.id,
                        src=i.image_url,
                        sort_order=i.sort_order,
                        is_cover=i.is_cover,
                    )
                    for i in data.images
                ],
                "attributes": [
                    AttributeOut(
                        name=a.attribute_name,
                        value=a.attribute_value,
                        sort_order=a.sort_order or 0,
                    )
                    for a in attrs_sorted
                ],
                "specifications": [
                    SpecificationOut(
                        name=a.attribute_name,
                        value=a.attribute_value,
                        sort_order=a.sort_order or 0,
                    )
                    for a in attrs_sorted
                ],
                "updated_at": data.updated_at.isoformat() if data.updated_at else None,
            }
        return data


class ProductDescriptionOut(BaseModel):
    id: int
    sku: str
    title: Optional[str] = None
    regular_price: Optional[float] = None
    short_description: Optional[str] = None
    long_description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def map_from_orm(cls, data):
        if isinstance(data, Product):
            return {
                "id": data.id,
                "sku": data.sku,
                "title": data.title,
                "regular_price": data.regular_price
                if data.regular_price is not None
                else data.price,
                "short_description": data.short_description
                if data.short_description is not None
                else data.description,
                "long_description": data.long_description,
            }
        return data


class ChangedProductId(BaseModel):
    id: int


class ProductsResponse(BaseModel):
    total: int
    page: int
    limit: int
    products: List[ProductListItem]


class ChangedProductsResponse(BaseModel):
    since: str
    total: int
    product_ids: List[int]


class StatsOut(BaseModel):
    total_products: int
    total_categories: int
    total_brands: int
    last_sync: Optional[str] = None


class SyncResponse(BaseModel):
    job_id: int
    status: str
    message: str
