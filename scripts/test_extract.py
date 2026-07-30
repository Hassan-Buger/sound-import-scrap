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
            from scraper.product import ProductScraper
            ps = ProductScraper.__new__(ProductScraper)
            attrs = ps._extract_attributes(raw)
            print('Extracted attributes count:', len(attrs))
            for a in attrs[:5]:
                aname = a["attribute_name"]
                aval = a["attribute_value"]
                print(f'  {aname}: {aval}')
            desc = raw.get("description", "")
            from scraper.soundimports import SoundImportsScraper
            sup = SoundImportsScraper.__new__(SoundImportsScraper)
            sup.product_scraper = ps
            detail = sup.extract_product_detail(raw, category_slug="tweeters")
            ld = detail.get("long_description", "")
            print()
            print("New long_description length:", len(ld))
            print("Differs from short:", ld != desc)
            print("First 300 chars:", ld[:300])

asyncio.run(check())
