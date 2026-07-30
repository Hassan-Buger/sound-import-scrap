import asyncio, json
from app.database import async_session_factory
from app.models import Product
from sqlalchemy import select

async def check():
    async with async_session_factory() as s:
        result = await s.execute(select(Product).where(Product.sku == 'H2606/920000'))
        p = result.scalar_one_or_none()
        if p and p.raw_json:
            raw = json.loads(p.raw_json)
            for field in ['specs', 'content', 'custom', 'description', 'shorttitle', 'fulltitle', 'data_01', 'data_02', 'data_03']:
                val = raw.get(field)
                print(f'=== {field} ===')
                if isinstance(val, str):
                    print(val[:1000])
                elif val:
                    print(json.dumps(val, ensure_ascii=False, indent=2)[:2000])
                else:
                    print('None')
                print()

asyncio.run(check())
