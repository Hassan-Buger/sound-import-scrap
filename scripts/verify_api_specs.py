import asyncio
import httpx
from app.main import app
from app.models import Category, Product, Attribute
from app.database import async_session_factory, Base, engine
from app import crud

async def verify_endpoints():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Test /api/categories
        r = await client.get("/api/categories")
        assert r.status_code == 200, f"Categories failed: {r.status_code}"
        categories = r.json()
        print(f"Categories verified: {len(categories)} categories")

        # 2. Test /api/products
        r = await client.get("/api/products?limit=10")
        assert r.status_code == 200, f"Products failed: {r.status_code}"
        data = r.json()
        products = data.get("products", [])
        print(f"Products list verified: {len(products)} returned, total={data.get('total')}")
        for p in products:
            specs = p.get("specifications", [])
            for s in specs:
                assert s.get("value") and str(s.get("value")).strip(), f"Empty spec found: {s} in product {p.get('sku')}"
        print("All products in /api/products have valid, non-empty specifications!")

        if products:
            first_p = products[0]
            pid = first_p["id"]
            
            # 3. Test /api/product/{id}
            r = await client.get(f"/api/product/{pid}")
            assert r.status_code == 200, f"Product detail failed: {r.status_code}"
            detail = r.json()
            for s in detail.get("specifications", []):
                assert s.get("value") and str(s.get("value")).strip(), f"Empty spec in detail: {s}"
            print(f"Product detail for ID={pid} ({detail.get('sku')}) verified with {len(detail.get('specifications', []))} specs!")

            # 4. Test /api/product/{id}/specifications
            r = await client.get(f"/api/product/{pid}/specifications")
            assert r.status_code == 200, f"Product specs endpoint failed: {r.status_code}"
            specs_out = r.json()
            for s in specs_out:
                assert s.get("value") and str(s.get("value")).strip(), f"Empty spec in specs endpoint: {s}"
            print(f"Product specifications endpoint for ID={pid} verified with {len(specs_out)} clean specs!")

if __name__ == "__main__":
    asyncio.run(verify_endpoints())
