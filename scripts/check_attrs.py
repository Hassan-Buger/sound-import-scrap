import asyncio, json
from app.database import async_session_factory
from app.models import Product
from sqlalchemy import select

async def check():
    async with async_session_factory() as s:
        result = await s.execute(select(Product).where(Product.sku == 'H2606/920000'))
        p = result.scalar_one_or_none()
        if p:
            print(f"Attributes count: {len(p.attributes_rel)}")
            for a in p.attributes_rel:
                print(f"  {a.attribute_name}: {a.attribute_value}")
            print(f"\nShort desc: {p.short_description[:100] if p.short_description else None}")
            print(f"Long desc:  {p.long_description[:100] if p.long_description else None}")

asyncio.run(check())
