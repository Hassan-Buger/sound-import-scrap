from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

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
                return f"https://cdn.webshopapp.com/shops/188510/files/{v}/500x500x2.jpg"
            return settings.base_url.rstrip("/") + "/" + v.lstrip("/")
        return v


class AttributeOut(BaseModel):
    name: str
    value: Optional[str] = None


class BrandOut(BaseModel):
    name: str
    product_count: int


class CategoryOut(BaseModel):
    id: int
    parent_id: Optional[int] = None
    name: str
    slug: str
    children: List["CategoryOut"] = []

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
                "children": children,
            }
        return data


class ProductListItem(BaseModel):
    id: int
    sku: str
    title: Optional[str] = None
    regular_price: Optional[float] = None
    stock_status: Optional[str] = None
    brand: Optional[str] = None
    updated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def map_from_orm(cls, data):
        if isinstance(data, Product):
            return {
                "id": data.id,
                "sku": data.sku,
                "title": data.title,
                "regular_price": data.regular_price if data.regular_price is not None else data.price,
                "stock_status": data.stock_status,
                "brand": data.brand,
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
    categories: List[str] = []
    images: List[ImageOut] = []
    attributes: List[AttributeOut] = []
    updated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def map_from_orm(cls, data):
        if isinstance(data, Product):
            cats = data.category_ids.split(",") if data.category_ids else []
            return {
                "id": data.id,
                "sku": data.sku,
                "title": data.title,
                "regular_price": data.regular_price if data.regular_price is not None else data.price,
                "short_description": data.short_description if data.short_description is not None else data.description,
                "long_description": data.long_description,
                "stock": data.stock,
                "stock_status": data.stock_status,
                "brand": data.brand,
                "ean": data.ean,
                "currency": data.currency or "EUR",
                "url": data.url,
                "category": cats[0] if cats else "Uncategorized",
                "categories": cats,
                "images": [
                    ImageOut(id=i.id, src=i.image_url, sort_order=i.sort_order, is_cover=i.is_cover)
                    for i in data.images
                ],
                "attributes": [
                    AttributeOut(name=a.attribute_name, value=a.attribute_value)
                    for a in data.attributes_rel
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
                "regular_price": data.regular_price if data.regular_price is not None else data.price,
                "short_description": data.short_description if data.short_description is not None else data.description,
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
