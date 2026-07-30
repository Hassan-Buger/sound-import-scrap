import json, asyncio
from app.database import async_session_factory
from app.models import Product
from sqlalchemy import select
from bs4 import BeautifulSoup

async def main():
    async with async_session_factory() as s:
        r = await s.execute(select(Product).where(Product.sku == 'H2606/920000'))
        p = r.scalar_one_or_none()
        if p and p.raw_json:
            raw = json.loads(p.raw_json)
            content = raw.get('content', '')
            soup = BeautifulSoup(content, 'html.parser')
            
            elements = soup.find_all(['h1','h2','h3','h4','h5','h6','p','ul','ol','hr'])
            print('Elements found:', len(elements))
            for el in elements:
                tag = el.name
                text = el.get_text(strip=True)[:80]
                print(f'  <{tag}>: {text}')
            
            from scraper.product import ProductScraper
            from scraper.client import HttpClient
            ps = ProductScraper(HttpClient())
            
            short_desc = 'Scan-Speak Discovery H2606/920000 1" Horn Dome Tweeter'
            extra = ps._extract_long_description_sections(soup, short_desc)
            print()
            print('extra result:', repr(extra[:500] if extra else None))

asyncio.run(main())
