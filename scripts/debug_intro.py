import asyncio, json
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
            
            print("=== All <p> elements with length info ===")
            for i, tag in enumerate(soup.find_all("p")):
                text = tag.get_text(strip=True)
                print(f"  p[{i}]: len={len(text)} text='{text[:120]}...' " if len(text) > 120 else f"  p[{i}]: len={len(text)} text='{text}'")
                if len(text) > 80:
                    print(f"    - has '. ': {'. ' in text}")
            
            from scraper.product import ProductScraper
            ps = ProductScraper.__new__(ProductScraper)
            ps.client = None
            intro = ps._find_intro_paragraph(soup)
            print(f"\nIntro found: {repr(intro[:150]) if intro else 'None'}")
            
            descs = ps._build_descriptions(raw)
            print(f"\nSHORT: {descs.get('short_description', '')[:150]}")
            long = descs.get('long_description', '')
            print(f"LONG length: {len(long)}")
            print(f"LONG starts: {long[:200]}")
            short = descs.get('short_description', '')
            print(f"Short in Long: {short[:80] in long}")

asyncio.run(main())
